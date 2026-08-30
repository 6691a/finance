import pytest
from airflow.sdk.exceptions import AirflowFailException

from dags import document_body_hourly as module
from modules.collectors.document.body import BodyCandidate, DocumentBody
from modules.collectors.document.documents import DocumentHTTPError

CANDIDATE = BodyCandidate(id=11, source_slug="bbc_business", canonical_url="https://b.example/a")


class FakeConnection:
    def __init__(self) -> None:
        self.commits = 0
        self.closed = False

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def run_collect(monkeypatch, tmp_path, *, waiting, collect, download=None, batch_size=200):
    """태스크 하나를 실제 DAG 코드로 돌린다. HTTP와 DB만 가짜다."""
    connections: list[FakeConnection] = []
    stored_bodies: list[DocumentBody] = []
    stored_attachments: list[tuple[int, object]] = []

    class FakeCollector:
        def __init__(self, file_root) -> None:
            self.file_root = file_root

        def collect(self, candidate):
            return collect(candidate)

        def download(self, candidate, url, position, now=None):
            assert download is not None
            return download(url, position)

        @staticmethod
        def store_body(connection, result):
            stored_bodies.append(result)
            return 1

        @staticmethod
        def store_attachment(connection, document_id, attachment):
            stored_attachments.append((document_id, attachment))

    def fake_connection():
        connection = FakeConnection()
        connections.append(connection)
        return connection

    monkeypatch.setattr(module, "DEFAULT_FILE_ROOT", tmp_path)
    monkeypatch.setattr(module, "DocumentBodyCollector", FakeCollector)
    monkeypatch.setattr(module, "pending_bodies", lambda connection, limit: waiting)
    monkeypatch.setattr(module, "_connection", fake_connection)

    task = module.document_body_hourly.task_dict["collect"]
    result = task.python_callable(params={"batch_size": batch_size})
    return result, stored_bodies, stored_attachments, connections


def test_a_missing_file_mount_fails_the_task_immediately(monkeypatch, tmp_path):
    """마운트가 없는데 조용히 넘기면 문서만 쌓이고 파일은 한 건도 안 남는다.

    그런데도 태스크는 성공으로 표시된다 — 아무도 그 사실을 모른다.
    """
    monkeypatch.setattr(module, "DEFAULT_FILE_ROOT", tmp_path / "not-mounted")

    task = module.document_body_hourly.task_dict["collect"]
    with pytest.raises(AirflowFailException, match="not mounted"):
        task.python_callable(params={"batch_size": 10})


def test_a_dead_url_is_settled_as_unavailable_instead_of_being_retried_forever(monkeypatch, tmp_path):
    """HTTP 404는 다시 쳐도 같은 답이다. 상태를 남겨야 큐에서 빠진다."""

    def collect(candidate):
        raise DocumentHTTPError(404)

    stored, bodies, _, _ = run_collect(monkeypatch, tmp_path, waiting=(CANDIDATE,), collect=collect)

    assert stored == 1
    assert bodies == [DocumentBody(document_id=CANDIDATE.id, status="unavailable")]


def test_a_server_error_leaves_the_document_in_the_queue(monkeypatch, tmp_path):
    """5xx는 제공처가 잠깐 죽은 것이다. 상태를 남기지 않아야 다음 실행이 다시 집는다."""
    other = CANDIDATE.model_copy(update={"id": 12})

    def collect(candidate):
        if candidate.id == CANDIDATE.id:
            raise DocumentHTTPError(503)
        return DocumentBody(document_id=candidate.id, status="ok", body="본문")

    stored, bodies, _, _ = run_collect(monkeypatch, tmp_path, waiting=(CANDIDATE, other), collect=collect)

    assert stored == 1
    assert [body.document_id for body in bodies] == [12]


def test_every_document_failing_raises_so_airflow_retries(monkeypatch, tmp_path):
    """하나가 죽는 것은 그 문서 문제이고 전부 죽는 것은 우리 쪽 문제다."""

    def collect(candidate):
        raise ConnectionError("dns down")

    with pytest.raises(ConnectionError, match="Every document failed"):
        run_collect(monkeypatch, tmp_path, waiting=(CANDIDATE,), collect=collect)


def test_one_dead_document_does_not_stop_the_rest(monkeypatch, tmp_path):
    other = CANDIDATE.model_copy(update={"id": 12})

    def collect(candidate):
        if candidate.id == CANDIDATE.id:
            raise ConnectionError("timeout")
        return DocumentBody(document_id=candidate.id, status="ok", body="본문")

    stored, bodies, _, _ = run_collect(monkeypatch, tmp_path, waiting=(CANDIDATE, other), collect=collect)

    assert stored == 1
    assert [body.document_id for body in bodies] == [12]


def test_a_failed_attachment_does_not_undo_the_body(monkeypatch, tmp_path):
    """본문을 먼저 커밋하고 파일은 뒤따른다. 파일 하나 때문에 본문을 버리지 않는다."""

    def collect(candidate):
        return DocumentBody(
            document_id=candidate.id,
            status="ok",
            body="본문",
            file_urls=("https://b.example/a.pdf", "https://b.example/b.pdf"),
        )

    def download(url, position):
        if url.endswith("a.pdf"):
            raise DocumentHTTPError(500)
        return module.DocumentBodyCollector  # 저장만 확인한다

    stored, bodies, attachments, _ = run_collect(
        monkeypatch, tmp_path, waiting=(CANDIDATE,), collect=collect, download=download
    )

    assert stored == 1
    assert bodies[0].body == "본문"
    # 죽은 파일만 빠지고 나머지는 붙는다.
    assert len(attachments) == 1


def test_an_empty_queue_is_not_a_failure(monkeypatch, tmp_path):
    """백필이 끝나면 대부분의 실행이 0건이다. 그것이 정상이다."""
    stored, bodies, _, _ = run_collect(
        monkeypatch, tmp_path, waiting=(), collect=lambda candidate: None
    )

    assert stored == 0
    assert bodies == []


def test_the_batch_size_reaches_the_queue(monkeypatch, tmp_path):
    seen: list[int] = []
    monkeypatch.setattr(module, "DEFAULT_FILE_ROOT", tmp_path)
    monkeypatch.setattr(module, "_connection", lambda: FakeConnection())
    monkeypatch.setattr(
        module,
        "pending_bodies",
        lambda connection, limit: seen.append(limit) or (),
    )

    module.document_body_hourly.task_dict["collect"].python_callable(params={"batch_size": 7})

    assert seen == [7]


def test_the_schedule_avoids_the_other_hourly_document_dags():
    """05분 발견, 25분 평가, 45분 사건 기대가 이미 차 있다."""
    assert module.document_body_hourly.schedule == "15 * * * *"
