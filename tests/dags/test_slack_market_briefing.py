"""DAG 객체를 봐야만 알 수 있는 것만 검증한다.

조회·렌더링·요약은 `modules/briefing/`에 있고 `tests/modules/`가 덮는다. 여기 남은 것은
스케줄과 휴장 판정처럼 `@dag`가 만든 객체나 DAG 파일의 상수를 읽어야 하는 것들이다.
"""

import pytest
from airflow.sdk.exceptions import AirflowFailException

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
    """08:10 프리마켓, 09:00 개장, 매시 정각 10:00~19:00(정규장·NXT 애프터마켓),
    15:35 KRX 마감, 20:15 최종이다.

    분이 제각각이라 cron 하나가 아니라 다중 cron 타임테이블이다. 주말은 cron이 뺀다.
    """
    timetable = slack_kr_market_briefing.slack_kr_market_briefing.schedule

    assert timetable.summary == "10 8 * * 1-5, 0 9 * * 1-5, 0 10-19 * * 1-5, 35 15 * * 1-5, 15 20 * * 1-5"


def test_scope_follows_the_scheduled_korean_slot(monkeypatch):
    """10시 전은 프리마켓, 15:35만 KRX 마감이고 이후에는 다시 NXT를 포함한다."""
    from datetime import UTC, datetime

    from modules.briefing.market import MarketScope

    monkeypatch.setattr(slack_kr_market_briefing, "get_current_context", dict)

    premarket = datetime(2026, 8, 17, 23, 10, tzinfo=UTC)  # KST 08:10
    opening = datetime(2026, 8, 18, 0, 0, tzinfo=UTC)  # KST 09:00
    regular = datetime(2026, 8, 18, 1, 0, tzinfo=UTC)  # KST 10:00
    krx_close = datetime(2026, 8, 18, 6, 35, tzinfo=UTC)  # KST 15:35
    aftermarket = datetime(2026, 8, 18, 7, 0, tzinfo=UTC)  # KST 16:00
    assert slack_kr_market_briefing._scope(premarket) is MarketScope.KOREA_PREOPEN
    assert slack_kr_market_briefing._scope(opening) is MarketScope.KOREA_PREOPEN
    assert slack_kr_market_briefing._scope(regular) is MarketScope.KOREA
    assert slack_kr_market_briefing._scope(krx_close) is MarketScope.KRX_CLOSE
    assert slack_kr_market_briefing._scope(aftermarket) is MarketScope.KOREA


def test_daily_chart_rides_only_the_open_and_close_slots(monkeypatch):
    """확정 일봉은 하루 한 번만 바뀌므로 매 발송에 붙이지 않는다. 장은 NXT를 포함하므로
    프리마켓이 열린 뒤(08:10)와 애프터마켓이 닫힌 뒤(20:15)가 시작·마감 슬롯이다.
    두 값은 발송 스케줄 안에 있어야 한다 — 없으면 일봉이 영영 안 나간다."""
    from datetime import UTC, datetime

    monkeypatch.setattr(slack_kr_market_briefing, "get_current_context", dict)

    timetable = slack_kr_market_briefing.slack_kr_market_briefing.schedule
    # 시각 범위(`0 10-19 * * 1-5`)가 섞여 있어 정수로 파싱하지 않고 분·시 칸을 글자로 맞춘다.
    fields = {" ".join(part.strip().split(" ", 2)[:2]) for part in timetable.summary.split(",")}
    assert {f"{minute} {hour}" for hour, minute in slack_kr_market_briefing.DAILY_CHART_SLOTS_KST} <= fields

    premarket = datetime(2026, 8, 17, 23, 10, tzinfo=UTC)  # KST 08:10
    aftermarket = datetime(2026, 8, 18, 11, 15, tzinfo=UTC)  # KST 20:15
    opening = datetime(2026, 8, 18, 0, 0, tzinfo=UTC)  # KST 09:00
    krx_close = datetime(2026, 8, 18, 6, 35, tzinfo=UTC)  # KST 15:35
    assert slack_kr_market_briefing._wants_daily_chart(premarket)
    assert slack_kr_market_briefing._wants_daily_chart(aftermarket)
    assert not slack_kr_market_briefing._wants_daily_chart(opening)
    assert not slack_kr_market_briefing._wants_daily_chart(krx_close)


def test_us_briefing_runs_the_morning_after():
    """미국 정규장은 KST 밤이라 장중 알림이 없다. 화~토인 이유는 KST 월요일 아침에
    직전 미국 세션이 없기 때문이다."""
    assert slack_us_market_briefing.slack_us_market_briefing.schedule == "0 8 * * 2-6"


def test_document_briefing_runs_four_times_a_day():
    """장 전·점심·KRX 마감·NXT 마감. `documents.SEND_SLOTS_KST`와 같은 목록이어야
    직전 발송 이후 창 계산이 슬롯을 이어서 덮는다."""
    from modules.briefing import documents

    timetable = slack_document_briefing.slack_document_briefing.schedule

    assert timetable.summary == "0 8 * * *, 0 12 * * *, 30 15 * * *, 0 20 * * *"
    crons = [part.strip().split(" ", 2)[:2] for part in timetable.summary.split(",")]
    assert [(int(hour), int(minute)) for minute, hour in crons] == list(documents.SEND_SLOTS_KST)


def test_ops_briefing_runs_daily():
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
