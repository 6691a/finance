import pytest
from airflow.sdk.exceptions import AirflowFailException

from dags import document_attachment_parse_hourly as module
from modules.collectors.document.pdf import ParseCandidate, ParsedAttachment

CANDIDATE = ParseCandidate(id=7, storage_path="documents/boj/1042/0.pdf")


class FakeConnection:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


def run_parse(monkeypatch, tmp_path, *, waiting, parse, store=lambda result: 1, batch_size=50):
    """태스크 하나를 실제 DAG 코드로 돌린다. 파일과 DB만 가짜다. `store`는 갱신한 행 수를 낸다."""
    connections: list[FakeConnection] = []
    stored: list[ParsedAttachment] = []

    class FakeParser:
        def __init__(self, file_root) -> None:
            self.file_root = file_root

        def parse(self, candidate):
            return parse(candidate)

        @staticmethod
        def store(connection, result):
            stored.append(result)
            return store(result)

    def fake_connection():
        connection = FakeConnection()
        connections.append(connection)
        return connection

    monkeypatch.setattr(module, "DEFAULT_FILE_ROOT", tmp_path)
    monkeypatch.setattr(module, "AttachmentPdfParser", FakeParser)
    monkeypatch.setattr(module, "pending_attachments", lambda connection, limit: waiting)
    monkeypatch.setattr(module, "_connection", fake_connection)

    task = module.document_attachment_parse_hourly.task_dict["parse"]
    return task.python_callable(params={"batch_size": batch_size}), stored, connections


def test_a_missing_file_mount_fails_the_task_immediately(monkeypatch, tmp_path):
    """마운트가 없는데 조용히 넘기면 큐만 돌고 텍스트는 한 건도 안 남는다."""
    monkeypatch.setattr(module, "DEFAULT_FILE_ROOT", tmp_path / "not-mounted")

    task = module.document_attachment_parse_hourly.task_dict["parse"]
    with pytest.raises(AirflowFailException, match="not mounted"):
        task.python_callable(params={"batch_size": 10})


def test_an_empty_queue_is_not_a_failure(monkeypatch, tmp_path):
    parsed, stored, connections = run_parse(monkeypatch, tmp_path, waiting=(), parse=lambda candidate: None)

    assert parsed == 0
    assert stored == []
    # 저장할 것이 없으면 연결을 다시 열지 않는다.
    assert len(connections) == 1


def test_a_file_we_cannot_open_is_settled_so_the_queue_lets_it_go(monkeypatch, tmp_path):
    """손상된 파일은 다시 열어도 같은 답이다. 상태를 남겨야 큐에서 빠진다."""

    def parse(candidate):
        return ParsedAttachment(attachment_id=candidate.id, status="failed", source_sha256="abc")

    parsed, stored, _ = run_parse(monkeypatch, tmp_path, waiting=(CANDIDATE,), parse=parse)

    assert parsed == 1
    assert [result.status for result in stored] == ["failed"]


def test_a_partially_read_file_still_stores_what_it_read(monkeypatch, tmp_path):
    """일부라도 검색에 걸리는 편이 아무 것도 못 찾는 것보다 낫다."""

    def parse(candidate):
        return ParsedAttachment(
            attachment_id=candidate.id,
            status="partial",
            text="<!-- page:1 -->\n본문",
            source_sha256="abc",
            page_count=3,
            failures=("page 2(broken)",),
        )

    parsed, stored, connections = run_parse(monkeypatch, tmp_path, waiting=(CANDIDATE,), parse=parse)

    assert parsed == 1
    assert stored[0].text is not None
    assert connections[-1].commits == 1


def test_a_row_whose_sha_moved_while_parsing_is_counted_as_failed(monkeypatch, tmp_path, caplog):
    """UPDATE가 0행이면 그 텍스트는 다른 파일의 것이다. 버리고 실패로 세어 보이게 한다."""
    other = CANDIDATE.model_copy(update={"id": 8})

    def parse(candidate):
        return ParsedAttachment(attachment_id=candidate.id, status="ok", text="본문", source_sha256="def")

    parsed, stored, _ = run_parse(
        monkeypatch,
        tmp_path,
        waiting=(CANDIDATE, other),
        parse=parse,
        store=lambda result: 0 if result.attachment_id == CANDIDATE.id else 1,
    )

    assert parsed == 1
    assert [result.attachment_id for result in stored] == [7, 8]
    assert "7(sha changed while parsing)" in caplog.text


def test_a_store_failure_does_not_block_the_rest_of_the_batch(monkeypatch, tmp_path, caplog):
    """파싱한 텍스트를 못 쓴 첨부 하나가 나머지의 저장까지 막지 않는다."""
    other = CANDIDATE.model_copy(update={"id": 8})

    def parse(candidate):
        return ParsedAttachment(attachment_id=candidate.id, status="ok", text="본문", source_sha256="abc")

    def store(result):
        if result.attachment_id == CANDIDATE.id:
            raise ValueError("A string literal cannot contain NUL (0x00) characters")
        return 1

    with pytest.raises(RuntimeError, match="cannot be retried"):
        run_parse(monkeypatch, tmp_path, waiting=(CANDIDATE, other), parse=parse, store=store)

    # 어느 첨부가 왜 못 쓰였는지가 로그에 남는다.
    assert "attachment 7 could not be stored" in caplog.text


def test_a_store_failure_kills_the_task_even_when_others_were_stored(monkeypatch, tmp_path):
    """저장 실패는 그 파일 문제가 아니라 우리 쪽 문제다. 조용히 넘기면 아무도 안 고친다 —
    NUL이 그렇게 발견됐다(2026-09-01)."""
    other = CANDIDATE.model_copy(update={"id": 8})
    attempted: list[int] = []

    def parse(candidate):
        return ParsedAttachment(attachment_id=candidate.id, status="ok", text="본문", source_sha256="abc")

    def store(result):
        attempted.append(result.attachment_id)
        if result.attachment_id == CANDIDATE.id:
            raise ValueError("boom")
        return 1

    with pytest.raises(RuntimeError, match=r"7\(.*boom"):
        run_parse(monkeypatch, tmp_path, waiting=(CANDIDATE, other), parse=parse, store=store)

    # 죽기 전에 나머지 첨부는 저장했다. 한 건 때문에 49건을 버리지 않는다.
    assert attempted == [7, 8]


def test_a_store_failure_keeps_the_original_exception(monkeypatch, tmp_path):
    """원인을 끊으면 추적이 거기서 멈춘다."""

    def parse(candidate):
        return ParsedAttachment(attachment_id=candidate.id, status="ok", text="본문", source_sha256="abc")

    def store(result):
        raise ValueError("adapt failed")

    with pytest.raises(RuntimeError) as caught:
        run_parse(monkeypatch, tmp_path, waiting=(CANDIDATE,), parse=parse, store=store)

    assert isinstance(caught.value.__cause__, ValueError)


def test_every_attachment_failing_kills_the_task(monkeypatch, tmp_path):
    """하나가 죽는 것은 그 파일 문제이고 전부 죽는 것은 마운트·권한 문제다."""

    def parse(candidate):
        raise OSError("input/output error")

    # AirflowFailException이 아니다 — 마운트·권한은 잠시 뒤 풀릴 수 있어 retries가 살아야 한다.
    with pytest.raises(OSError, match="Every attachment failed"):
        run_parse(monkeypatch, tmp_path, waiting=(CANDIDATE,), parse=parse)


def test_an_unexpected_parser_error_finishes_the_batch_then_kills_the_task(monkeypatch, tmp_path):
    """파서 버그는 다시 해도 같은 답이다. 나머지는 저장하되 태스크는 죽는다.

    나머지를 계속 도는 이유는 예외 하나가 나머지를 통째로 막은 적이 있어서다(2026-08-15).
    """
    other = CANDIDATE.model_copy(update={"id": 8})

    def parse(candidate):
        if candidate.id == CANDIDATE.id:
            raise ValueError("something we did not foresee")
        return ParsedAttachment(attachment_id=candidate.id, status="ok", text="본문", source_sha256="abc")

    with pytest.raises(RuntimeError, match="cannot be retried") as caught:
        run_parse(monkeypatch, tmp_path, waiting=(CANDIDATE, other), parse=parse)

    # 원인을 끊으면 추적이 거기서 멈춘다.
    assert isinstance(caught.value.__cause__, ValueError)


def test_an_unexpected_parser_error_still_stores_the_other_attachments(monkeypatch, tmp_path):
    """예외 하나가 나머지를 통째로 막은 적이 있다(2026-08-15). 죽더라도 배치는 끝낸다."""
    other = CANDIDATE.model_copy(update={"id": 8})
    attempted: list[int] = []

    def parse(candidate):
        if candidate.id == CANDIDATE.id:
            raise ValueError("something we did not foresee")
        return ParsedAttachment(attachment_id=candidate.id, status="ok", text="본문", source_sha256="abc")

    def store(result):
        attempted.append(result.attachment_id)
        return 1

    with pytest.raises(RuntimeError):
        run_parse(monkeypatch, tmp_path, waiting=(CANDIDATE, other), parse=parse, store=store)

    assert attempted == [8]


def test_a_missing_file_is_transient_and_leaves_the_task_green(monkeypatch, tmp_path):
    """마운트가 흔들린 것이라 다음 실행이 고친다. 이것으로 죽이면 경보만 는다."""
    other = CANDIDATE.model_copy(update={"id": 8})

    def parse(candidate):
        if candidate.id == CANDIDATE.id:
            raise FileNotFoundError("documents/boj/1042/0.pdf")
        return ParsedAttachment(attachment_id=candidate.id, status="ok", text="본문", source_sha256="abc")

    parsed, stored, _ = run_parse(monkeypatch, tmp_path, waiting=(CANDIDATE, other), parse=parse)

    assert parsed == 1
    assert [result.attachment_id for result in stored] == [8]


def test_settled_bad_files_are_counted_in_the_summary(monkeypatch, tmp_path, caplog):
    """손상·암호는 상태로 확정돼 큐에서 빠진다. 세지 않으면 50건이 전부 그래도 로그가 같다."""
    other = CANDIDATE.model_copy(update={"id": 8})

    def parse(candidate):
        status = "failed" if candidate.id == CANDIDATE.id else "unsupported"
        return ParsedAttachment(attachment_id=candidate.id, status=status, source_sha256="abc")

    parsed, _, _ = run_parse(monkeypatch, tmp_path, waiting=(CANDIDATE, other), parse=parse)

    assert parsed == 2
    assert "2 settled as unreadable files" in caplog.text


def test_settled_bad_files_raise_an_error_log_without_failing_the_task(monkeypatch, tmp_path, caplog):
    """파일 문제인지 파서 문제인지는 사람이 봐야 안다. **태스크는 죽이지 않는다** — 나머지가
    다 됐는데 우리가 고칠 수 없는 파일 하나로 빨개지면 안 된다.

    `logger.error`인 이유는 Airflow의 Sentry 통합이 표준 LoggingIntegration이라 ERROR만
    이벤트가 되기 때문이다. WARNING은 breadcrumb으로만 남아 아무도 안 본다.
    """
    other = CANDIDATE.model_copy(update={"id": 8})

    def parse(candidate):
        if candidate.id == CANDIDATE.id:
            return ParsedAttachment(
                attachment_id=candidate.id,
                status="failed",
                source_sha256="abc",
                reason="cannot open broken document",
            )
        return ParsedAttachment(attachment_id=candidate.id, status="ok", text="본문", source_sha256="abc")

    parsed, _, _ = run_parse(monkeypatch, tmp_path, waiting=(CANDIDATE, other), parse=parse)

    # 나머지는 저장됐고 태스크는 초록이다.
    assert parsed == 2

    errors = [record for record in caplog.records if record.levelname == "ERROR"]
    assert len(errors) == 1
    message = errors[0].getMessage()
    # 조사에 필요한 것 셋 — 어느 첨부인지, 어느 파일인지, 무엇이 났는지.
    assert "7" in message
    assert "documents/boj/1042/0.pdf" in message
    assert "cannot open broken document" in message


def test_no_error_log_when_every_file_was_readable(monkeypatch, tmp_path, caplog):
    """울릴 것이 없으면 울리지 않는다. 매시 도는 DAG이라 빈 경보는 곧 무시된다."""

    def parse(candidate):
        return ParsedAttachment(attachment_id=candidate.id, status="ok", text="본문", source_sha256="abc")

    run_parse(monkeypatch, tmp_path, waiting=(CANDIDATE,), parse=parse)

    assert [record for record in caplog.records if record.levelname == "ERROR"] == []


def test_the_schedule_stands_behind_the_body_collector():
    """15분에 파일을 받고 20분에 그 파일을 읽는다."""
    assert module.document_attachment_parse_hourly.schedule == "20 * * * *"


def test_the_parse_task_holds_the_single_nas_slot():
    """NAS에서 파싱이 하나만 돌아야 한다. 호출이 없는 DAG이라 pool은 여기만 문다."""
    assert module.document_attachment_parse_hourly.task_dict["parse"].pool == "pdf_parse"


def test_the_display_metadata_is_filled():
    dag = module.document_attachment_parse_hourly
    assert dag.dag_display_name == "📄 첨부 PDF 파싱"
    assert dag.description
    assert dag.doc_md
    assert dag.params["batch_size"] == module.DEFAULT_BATCH_SIZE
