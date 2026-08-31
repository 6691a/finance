import pytest
from airflow.sdk.exceptions import AirflowFailException

from dags import document_attachment_parse_hourly as module
from modules.collectors.document.pdf import FileChangedError, ParseCandidate, ParsedAttachment

CANDIDATE = ParseCandidate(id=7, document_id=42, storage_path="documents/boj/1042/0.pdf", sha256="abc")


class FakeConnection:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


def run_parse(monkeypatch, tmp_path, *, waiting, parse, batch_size=50):
    """태스크 하나를 실제 DAG 코드로 돌린다. 파일과 DB만 가짜다."""
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
            return 1

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
        return ParsedAttachment(attachment_id=candidate.id, status="failed", source_sha256=candidate.sha256)

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
            source_sha256=candidate.sha256,
            page_count=3,
            failures=("page 2(broken)",),
        )

    parsed, stored, connections = run_parse(monkeypatch, tmp_path, waiting=(CANDIDATE,), parse=parse)

    assert parsed == 1
    assert stored[0].text is not None
    assert connections[-1].commits == 1


def test_a_file_that_changed_on_disk_stays_in_the_queue(monkeypatch, tmp_path):
    """상태를 남기지 않아야 다음 실행이 새 SHA로 다시 집는다."""
    other = CANDIDATE.model_copy(update={"id": 8})

    def parse(candidate):
        if candidate.id == CANDIDATE.id:
            raise FileChangedError("attachment 7 on disk is def, not abc")
        return ParsedAttachment(attachment_id=candidate.id, status="ok", text="본문", source_sha256="abc")

    parsed, stored, _ = run_parse(monkeypatch, tmp_path, waiting=(CANDIDATE, other), parse=parse)

    assert parsed == 1
    assert [result.attachment_id for result in stored] == [8]


def test_every_attachment_failing_kills_the_task(monkeypatch, tmp_path):
    """하나가 죽는 것은 그 파일 문제이고 전부 죽는 것은 마운트·권한 문제다."""

    def parse(candidate):
        raise OSError("input/output error")

    with pytest.raises(AirflowFailException, match="Every attachment failed"):
        run_parse(monkeypatch, tmp_path, waiting=(CANDIDATE,), parse=parse)


def test_an_unexpected_error_stops_at_that_attachment(monkeypatch, tmp_path):
    """파서 예외 하나가 나머지를 통째로 막은 적이 있다(2026-08-15)."""
    other = CANDIDATE.model_copy(update={"id": 8})

    def parse(candidate):
        if candidate.id == CANDIDATE.id:
            raise ValueError("something we did not foresee")
        return ParsedAttachment(attachment_id=candidate.id, status="ok", text="본문", source_sha256="abc")

    parsed, stored, _ = run_parse(monkeypatch, tmp_path, waiting=(CANDIDATE, other), parse=parse)

    assert parsed == 1
    assert [result.attachment_id for result in stored] == [8]


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
