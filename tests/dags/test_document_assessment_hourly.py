from datetime import UTC, datetime, timedelta

import pytest
from airflow.exceptions import AirflowFailException

from dags import document_assessment_hourly as module
from modules.assessment import Assessment, AssessmentResult, Candidates, PendingDocument
from modules.llm import RetryableLlmError

DOCUMENT = PendingDocument(
    id=11,
    source_slug="test",
    title="문서",
    summary=None,
    body=None,
    language="ko",
    published_at=datetime(2026, 8, 14, 22, 30, tzinfo=UTC),
    content_hash="abc",
)
CANDIDATES = Candidates(instruments=(("005930", "삼성전자"),), indicators=())
VALID_ASSESSMENT = Assessment.model_validate(
    {
        "scores": {"relevance": 1, "novelty": 1, "specificity": 1, "impact": 1},
    }
)


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


@pytest.mark.parametrize(
    ("retryable", "expected_error"),
    [(True, RetryableLlmError), (False, AirflowFailException)],
)
def test_evaluate_saves_successes_before_raising_provider_error(monkeypatch, retryable, expected_error):
    other = DOCUMENT.model_copy(update={"id": 12})
    connections: list[FakeConnection] = []
    persisted: list[int] = []

    class FakeBatch:
        def __init__(self, *args) -> None:
            pass

        def run(self, documents, candidates):
            return (
                AssessmentResult(document_id=DOCUMENT.id, assessment=VALID_ASSESSMENT),
                AssessmentResult(document_id=other.id, error="HTTP 520", retryable=retryable),
            )

    monkeypatch.setattr(module, "get_current_context", lambda: {"params": {}})
    monkeypatch.setattr(module, "_connection", lambda: connections.append(FakeConnection()) or connections[-1])

    class FakeStore:
        def __init__(self, connection, prompt_revision) -> None:
            self.connection = connection

        def candidates(self):
            return CANDIDATES

        def pending(self, limit):
            return (DOCUMENT, other)

        def store(self, document, *args) -> None:
            persisted.append(document.id)

    monkeypatch.setattr(module, "AssessmentStore", FakeStore)
    monkeypatch.setattr(module, "document_model", lambda: object())
    monkeypatch.setattr(module, "DocumentAssessor", lambda model, settings: object())
    monkeypatch.setattr(module, "AssessmentBatch", FakeBatch)
    monkeypatch.setattr(module, "model_name", lambda model: "test-model")

    task = module.document_assessment_hourly.task_dict["evaluate"]
    with pytest.raises(expected_error):
        task.python_callable()

    assert persisted == [DOCUMENT.id]
    assert sum(connection.commits for connection in connections) == 1


def test_evaluate_uses_short_exponential_retries_for_transient_llm_errors():
    task = module.document_assessment_hourly.task_dict["evaluate"]

    assert task.retries == 3
    assert task.retry_delay == timedelta(minutes=1)
    assert task.retry_exponential_backoff is True
    assert task.max_retry_delay == timedelta(minutes=5)
