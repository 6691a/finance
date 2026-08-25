"""DAG 객체 자체를 봐야만 알 수 있는 것과, 태스크의 실패 판정만 본다.

추출·판정 규칙은 `modules/expectation.py`에 있고 `tests/modules/test_expectation.py`가 덮는다.
"""

from datetime import UTC, datetime, timedelta

import pytest
from airflow.exceptions import AirflowFailException

from dags import event_expectation_hourly as module
from modules.expectation import ExtractionError, ExtractionResponse, JudgedOutcome, PendingExtractionDocument
from modules.llm import LlmError, RetryableLlmError

DOCUMENT = PendingExtractionDocument(
    id=11,
    source_slug="naver_research_company",
    title="삼성전자: 주주환원 확대 기대 - 대신증권",
    summary="2026년 총 주주환원 9.5조원 전망.",
    body=None,
    published_at=datetime(2026, 8, 1, tzinfo=UTC),
    detected_at=datetime(2026, 8, 1, 1, 0, tzinfo=UTC),
    content_hash="abc",
    tickers=("005930",),
)
EMPTY = ExtractionResponse()


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


def _prepare(monkeypatch, documents, extract, stored: list[int]):
    monkeypatch.setattr(module, "get_current_context", lambda: {"params": {}, "run_id": "manual__x"})
    monkeypatch.setattr(module, "_connection", FakeConnection)

    class FakeStore:
        def __init__(self, connection) -> None:
            self.connection = connection

        def pending(self, limit):
            return documents

        def store_extraction(self, document, *args, **kwargs) -> None:
            stored.append(document.id)

    monkeypatch.setattr(module, "ExpectationStore", FakeStore)
    monkeypatch.setattr(module, "expectation_model", lambda: object())
    monkeypatch.setattr(module, "model_name", lambda model: "test-model")
    monkeypatch.setattr(module, "filter_claims", lambda response, document: ())

    class FakeExtractor:
        def __init__(self, model) -> None:
            pass

        def extract(self, document):
            return extract(document)

    monkeypatch.setattr(module, "ExpectationExtractor", FakeExtractor)


def test_the_dag_runs_after_ingestion_and_assessment_each_hour():
    dag = module.event_expectation_hourly

    # 수집이 매시 :05, 평가가 매시 :25다. 추출 대상이 "평가 완료 + 종목 태그"라 그 뒤여야 한다.
    assert dag.schedule == "45 * * * *"
    assert dag.max_active_runs == 1


def test_the_tasks_split_the_expensive_call_from_the_send():
    """Slack이 죽어도 LLM을 다시 부르지 않는다(thesis와 같은 이유)."""
    tasks = module.event_expectation_hourly.task_dict

    assert set(tasks) == {"extract_claims", "judge_outcomes", "notify_slack"}
    assert set(tasks["judge_outcomes"].upstream_task_ids) == {"extract_claims"}
    assert set(tasks["notify_slack"].upstream_task_ids) == {"judge_outcomes"}


def test_the_dag_fills_the_display_metadata():
    dag = module.event_expectation_hourly

    assert dag.dag_display_name.startswith("📐")
    assert dag.description
    assert dag.doc_md
    batch = dag.params.get_param("batch_size")
    assert batch.schema["title"]
    assert batch.description


def test_a_retryable_error_keeps_the_successes_and_asks_airflow_to_retry(monkeypatch):
    """앞 문서의 저장은 커밋된 채로 남고 재시도가 남은 것만 다시 집는다."""
    other = DOCUMENT.model_copy(update={"id": 12})
    stored: list[int] = []

    def extract(document):
        if document.id == DOCUMENT.id:
            return EMPTY
        raise RetryableLlmError("HTTP 520")

    _prepare(monkeypatch, (DOCUMENT, other), extract, stored)

    with pytest.raises(RetryableLlmError):
        module.event_expectation_hourly.task_dict["extract_claims"].python_callable()

    assert stored == [DOCUMENT.id]


def test_a_non_retryable_error_fails_the_task_immediately(monkeypatch):
    """인증·잘못된 요청은 재시도해도 같다."""

    def extract(document):
        raise LlmError("HTTP 401")

    _prepare(monkeypatch, (DOCUMENT,), extract, [])

    with pytest.raises(AirflowFailException):
        module.event_expectation_hourly.task_dict["extract_claims"].python_callable()


def test_every_response_being_malformed_fails_the_task(monkeypatch):
    """문서 하나의 문제가 아니라 프롬프트나 모델 쪽 문제라 재시도해도 같다."""

    def extract(document):
        raise ExtractionError("Model returned malformed JSON")

    _prepare(monkeypatch, (DOCUMENT,), extract, [])

    with pytest.raises(AirflowFailException):
        module.event_expectation_hourly.task_dict["extract_claims"].python_callable()


def test_one_broken_document_does_not_stop_the_others(monkeypatch):
    """형식이 깨진 문서는 원장에 오르지 않고 다음 실행이 다시 집는다."""
    other = DOCUMENT.model_copy(update={"id": 12})
    stored: list[int] = []

    def extract(document):
        if document.id == other.id:
            raise ExtractionError("malformed")
        return EMPTY

    _prepare(monkeypatch, (DOCUMENT, other), extract, stored)

    assert module.event_expectation_hourly.task_dict["extract_claims"].python_callable() == 1
    assert stored == [DOCUMENT.id]


def test_no_new_outcome_means_no_slack_call(monkeypatch):
    """재실행은 판정을 다시 쓰지 않는다. 같은 알림을 매시간 보내면 안 된다."""
    sent: list[str] = []

    class FakeSlackClient:
        def __init__(self, token) -> None:
            self.token = token

        def post_message(self, *args, **kwargs) -> str:
            sent.append("x")
            return "ts"

    monkeypatch.setattr(module, "SlackClient", FakeSlackClient)

    assert module.event_expectation_hourly.task_dict["notify_slack"].python_callable([]) == ""
    assert sent == []


def test_the_slack_task_needs_a_token_and_a_channel(monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_CHANNEL_MARKET", raising=False)
    judged = [
        JudgedOutcome(
            stock_code="005930",
            event_type="shareholder_return",
            period_key="2026",
            metric="total_return_amount",
            expected_value="9500000000000",
            expectation_count=2,
            actual_value="8000000000000",
            surprise_pct="-15.7895",
            verdict="miss",
            announced_at=datetime(2026, 8, 22, 8, 0, tzinfo=UTC),
        ).model_dump(mode="json")
    ]

    with pytest.raises(AirflowFailException, match="SLACK_BOT_TOKEN"):
        module.event_expectation_hourly.task_dict["notify_slack"].python_callable(judged)


def test_a_failed_hour_is_picked_up_by_the_next_one():
    dag = module.event_expectation_hourly

    # 다음 실행이 밀린 문서를 다시 집는다. 재시도를 길게 끌 이유가 없다.
    assert dag.default_args["retries"] == 1
    assert dag.default_args["retry_delay"] == timedelta(minutes=10)
