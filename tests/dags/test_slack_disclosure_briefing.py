"""DAG 객체와 폴백 경로만 검증한다.

조회·계산·렌더는 `modules/briefing/disclosures.py`에 있고
`tests/modules/test_briefing_disclosures.py`가 덮는다. 여기 남은 것은 스케줄·태스크 구성과
"0건이면 아무 것도 부르지 않는다", "강조가 실패해도 보낸다"처럼 DAG이 정하는 판단이다.

설계는 docs/briefing/disclosure-briefing.md다.
"""

from datetime import UTC, date, datetime
from typing import Any, ClassVar

import pytest
from airflow.sdk.exceptions import AirflowFailException

from dags import slack_disclosure_briefing
from modules.briefing.disclosures import DisclosureBatch, Highlight, HighlightError, NewDisclosure
from modules.slack import SlackError

WINDOW_START = datetime(2026, 8, 27, 8, 30, tzinfo=UTC)
WINDOW_END = datetime(2026, 8, 27, 8, 40, tzinfo=UTC)

DISCLOSURE = NewDisclosure(
    rcept_no="20260827000123",
    stock_code="005930",
    company_name="삼성전자",
    report_name="연결재무제표기준영업(잠정)실적(공정공시)",
    receipt_date=date(2026, 8, 27),
    detected_at=datetime(2026, 8, 27, 8, 31, tzinfo=UTC),
)

BATCH = DisclosureBatch(
    generated_at=WINDOW_END,
    window_start=WINDOW_START,
    window_end=WINDOW_END,
    disclosures=(DISCLOSURE,),
)
EMPTY_BATCH = DisclosureBatch(generated_at=WINDOW_END, window_start=WINDOW_START, window_end=WINDOW_END)


class FakeConnection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeSlack:
    instances: ClassVar[list["FakeSlack"]] = []

    def __init__(self, token: Any) -> None:
        self.posts: list[dict[str, Any]] = []
        FakeSlack.instances.append(self)

    def post_message(self, channel: str, *, text: str, blocks: Any = None) -> str:
        self.posts.append({"channel": channel, "text": text, "blocks": blocks})
        return "1724740000.000100"


@pytest.fixture
def send(monkeypatch):
    """`send_alert` 태스크의 알맹이를 부를 수 있게 주변을 전부 가짜로 바꾼다."""
    FakeSlack.instances = []
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-secret")
    monkeypatch.setenv("SLACK_CHANNEL_DOCUMENT", "C-document")
    monkeypatch.setattr(slack_disclosure_briefing, "_connection", FakeConnection)
    monkeypatch.setattr(slack_disclosure_briefing, "SlackClient", FakeSlack)
    monkeypatch.setattr(
        slack_disclosure_briefing,
        "get_current_context",
        lambda: {"data_interval_start": WINDOW_START, "data_interval_end": WINDOW_END},
    )
    return slack_disclosure_briefing.slack_disclosure_briefing.task_dict["send_alert"].python_callable


# --- DAG 객체 ---------------------------------------------------------------


def test_it_runs_every_ten_minutes_while_disclosures_are_collected():
    """수집(`dart_disclosure_intraday`)이 평일 07:00~20:58에 2분마다 돈다.
    발송이 그 창을 덮어야 감지된 공시가 빠지지 않는다."""
    assert slack_disclosure_briefing.slack_disclosure_briefing.schedule == "*/10 7-20 * * 1-5"


def test_it_never_replays_a_missed_window():
    """알림이라 지난 공시를 몰아 보내지 않는다. 이것이 notified_at 컬럼을 안 만든 대가다."""
    dag = slack_disclosure_briefing.slack_disclosure_briefing

    assert dag.catchup is False
    assert dag.max_active_runs == 1


def test_it_has_one_task_so_a_retry_cannot_double_send():
    """발송이 마지막 단계라 그 전 실패는 중복 발송 없이 재시도된다."""
    assert list(slack_disclosure_briefing.slack_disclosure_briefing.task_dict) == ["send_alert"]


def test_the_screen_metadata_is_filled():
    dag = slack_disclosure_briefing.slack_disclosure_briefing

    assert dag.dag_display_name == "📄 새 공시 알림 (Slack)"
    assert dag.description
    assert dag.doc_md


def test_missing_slack_settings_fail_without_retry(monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_CHANNEL_DOCUMENT", raising=False)

    with pytest.raises(AirflowFailException):
        slack_disclosure_briefing._slack_settings()


def test_the_token_is_wrapped_so_logs_cannot_print_it(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-secret")
    monkeypatch.setenv("SLACK_CHANNEL_DOCUMENT", "C-document")

    token, channel = slack_disclosure_briefing._slack_settings()

    assert "xoxb-secret" not in str(token)
    assert channel == "C-document"


def test_a_run_without_a_data_interval_fails_instead_of_guessing(monkeypatch):
    """벽시계로 떨어지면 UI의 Trigger 버튼이 조용히 다른 창을 본다."""
    monkeypatch.setattr(slack_disclosure_briefing, "get_current_context", dict)

    with pytest.raises(AirflowFailException):
        slack_disclosure_briefing._window()


# --- 발송 판단 --------------------------------------------------------------


def test_an_empty_window_calls_neither_the_model_nor_slack(send, monkeypatch):
    """0건에도 보내는 문서 브리핑과 반대다. 하루의 대부분이 빈 메시지가 된다."""
    monkeypatch.setattr(slack_disclosure_briefing.disclosures, "collect_batch", lambda *a, **k: EMPTY_BATCH)

    def explode(*args, **kwargs):
        raise AssertionError("the model must not be called for an empty window")

    monkeypatch.setattr(slack_disclosure_briefing.disclosures, "pick_input", explode)

    assert send() == ""
    assert FakeSlack.instances == []


def test_a_highlight_failure_still_sends_the_plain_list(send, monkeypatch, caplog):
    """알림을 늦추는 것보다 강조 없이 제때 가는 편이 낫다."""
    monkeypatch.setattr(slack_disclosure_briefing.disclosures, "collect_batch", lambda *a, **k: BATCH)
    monkeypatch.setattr(
        slack_disclosure_briefing,
        "_highlight",
        lambda batch: (None, "boom"),
    )

    assert send() == "1724740000.000100"
    post = FakeSlack.instances[0].posts[0]
    assert post["channel"] == "C-document"
    assert "삼성전자" in post["text"]
    contexts = [block for block in post["blocks"] if block["type"] == "context"]
    assert "공시 강조 실패: boom" in contexts[0]["elements"][0]["text"]


@pytest.mark.parametrize("error", [HighlightError("bad json"), ConnectionError("timeout")])
def test_every_highlight_error_falls_back_instead_of_killing_the_task(send, monkeypatch, error):
    monkeypatch.setattr(slack_disclosure_briefing.disclosures, "collect_batch", lambda *a, **k: BATCH)

    class ExplodingPicker:
        def __init__(self, model: Any) -> None:
            pass

        def highlight(self, *args: Any, **kwargs: Any):
            raise error

    monkeypatch.setattr("modules.briefing.disclosure_picks.DisclosurePicker", ExplodingPicker)
    monkeypatch.setattr("modules.llm.briefing_model", lambda: object())

    assert send() == "1724740000.000100"
    assert FakeSlack.instances[0].posts


def test_highlights_reach_the_message(send, monkeypatch):
    monkeypatch.setattr(slack_disclosure_briefing.disclosures, "collect_batch", lambda *a, **k: BATCH)
    monkeypatch.setattr(
        slack_disclosure_briefing,
        "_highlight",
        lambda batch: ((Highlight(rcept_no=DISCLOSURE.rcept_no, reason="영업이익이 크게 늘었다"),), None),
    )

    send()
    body = "".join(
        block["text"]["text"] for block in FakeSlack.instances[0].posts[0]["blocks"] if block["type"] == "section"
    )
    assert "⭐ *삼성전자*" in body
    assert "영업이익이 크게 늘었다" in body


def test_slack_rejection_fails_the_task(send, monkeypatch):
    monkeypatch.setattr(slack_disclosure_briefing.disclosures, "collect_batch", lambda *a, **k: BATCH)
    monkeypatch.setattr(slack_disclosure_briefing, "_highlight", lambda batch: (None, None))

    class RejectingSlack:
        def __init__(self, token: Any) -> None:
            pass

        def post_message(self, *args: Any, **kwargs: Any) -> str:
            raise SlackError("invalid_blocks")

    monkeypatch.setattr(slack_disclosure_briefing, "SlackClient", RejectingSlack)

    with pytest.raises(AirflowFailException):
        send()


def test_the_connection_is_closed_even_when_the_query_fails(send, monkeypatch):
    connections: list[FakeConnection] = []

    def make() -> FakeConnection:
        connection = FakeConnection()
        connections.append(connection)
        return connection

    monkeypatch.setattr(slack_disclosure_briefing, "_connection", make)

    def explode(*args: Any, **kwargs: Any):
        raise RuntimeError("db is down")

    monkeypatch.setattr(slack_disclosure_briefing.disclosures, "collect_batch", explode)

    with pytest.raises(RuntimeError):
        send()
    assert connections[0].closed
