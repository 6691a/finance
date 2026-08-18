"""DAG 객체를 봐야만 알 수 있는 것만 검증한다.

조회·렌더링·요약은 `modules/briefing/`에 있고 `tests/modules/`가 덮는다. 여기 남은 것은
스케줄과 휴장 판정처럼 `@dag`가 만든 객체나 DAG 파일의 상수를 읽어야 하는 것들이다.
"""

import pytest
from airflow.exceptions import AirflowFailException

from dags import (
    slack_document_briefing,
    slack_kr_market_briefing,
    slack_ops_briefing,
    slack_us_market_briefing,
)

ALL_BRIEFINGS = [
    slack_kr_market_briefing,
    slack_us_market_briefing,
    slack_document_briefing,
    slack_ops_briefing,
]


def test_korea_briefing_runs_during_the_domestic_session():
    # 오전장 요약과 마감 후. 주말은 cron이 뺀다.
    assert slack_kr_market_briefing.slack_kr_market_briefing.schedule == "30 12,16 * * 1-5"


def test_us_briefing_runs_the_morning_after():
    """미국 정규장은 KST 밤이라 장중 알림이 없다. 화~토인 이유는 KST 월요일 아침에
    직전 미국 세션이 없기 때문이다."""
    assert slack_us_market_briefing.slack_us_market_briefing.schedule == "0 8 * * 2-6"


def test_document_and_ops_briefings_run_daily():
    assert slack_document_briefing.slack_document_briefing.schedule == "0 8 * * *"
    assert slack_ops_briefing.slack_ops_briefing.schedule == "0 8 * * *"


@pytest.mark.parametrize("module", ALL_BRIEFINGS)
def test_each_report_has_one_task(module):
    """발송이 마지막 단계라 그 전 실패는 중복 발송 없이 재시도된다."""
    dag = getattr(module, module.__name__.rsplit(".", 1)[-1])

    assert list(dag.task_dict) == ["send_briefing"]


@pytest.mark.parametrize("module", ALL_BRIEFINGS)
def test_missing_slack_settings_fail_without_retry(module, monkeypatch):
    for name in ("SLACK_BOT_TOKEN", "SLACK_CHANNEL_MARKET", "SLACK_CHANNEL_DOCUMENT", "SLACK_CHANNEL_OPS"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(AirflowFailException):
        module._slack_settings()


@pytest.mark.parametrize("module", ALL_BRIEFINGS)
def test_the_token_is_wrapped_so_logs_cannot_print_it(module, monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-secret")
    for name in ("SLACK_CHANNEL_MARKET", "SLACK_CHANNEL_DOCUMENT", "SLACK_CHANNEL_OPS"):
        monkeypatch.setenv(name, "C123")

    token, channel = module._slack_settings()

    assert "xoxb-secret" not in str(token)
    assert channel == "C123"


def test_each_report_goes_to_its_own_channel(monkeypatch):
    """운영 리포트가 시장 채널에 섞이면 나머지가 고장났다는 신호를 놓친다."""
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-secret")
    monkeypatch.setenv("SLACK_CHANNEL_MARKET", "C-market")
    monkeypatch.setenv("SLACK_CHANNEL_DOCUMENT", "C-document")
    monkeypatch.setenv("SLACK_CHANNEL_OPS", "C-ops")

    channels = {module._slack_settings()[1] for module in ALL_BRIEFINGS}

    # 한국장과 미국장은 같은 주제라 채널을 공유한다. 문서·운영은 각자 채널이다.
    assert channels == {"C-market", "C-document", "C-ops"}
