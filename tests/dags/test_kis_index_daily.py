"""DAG 객체와 params 해석만 검증한다.

수집·검증 규칙은 `modules/collectors/kis.py`의 `fetch_index_daily`/`store_index_daily`에 있고
`tests/collectors/test_kis.py`가 덮는다. 설계는 docs/analysis/market-technical-indicators.md 4.4절이다.
"""

from datetime import UTC, date, datetime, timedelta
from itertools import pairwise

import pytest
from airflow.sdk.exceptions import AirflowFailException

from dags import kis_index_daily
from modules.utility import KST_TIMEZONE

NOW_KST = datetime(2026, 8, 24, 18, 20, tzinfo=KST_TIMEZONE)


def test_the_dag_runs_after_the_daily_bars_are_final():
    dag = kis_index_daily.kis_index_daily

    # KST 평일 18:20 = UTC 평일 09:20. 종목 확정 일봉(18:10) 뒤, 신호 계산(18:40) 앞이다.
    assert dag.schedule == "20 18 * * 1-5"
    assert dag.max_active_runs == 1
    assert set(dag.task_dict) == {"collect"}


def test_the_daily_dag_does_not_reuse_the_market_movement_subset():
    """`MOVEMENT_INDEXES`는 상승·보합·하락 분포용 부분집합이다.

    일봉 순회에 그걸 쓰면 KOSPI200이 조용히 빠진다. 실제로 그렇게 빠져 있었다.
    """
    assert not hasattr(kis_index_daily, "MOVEMENT_INDEXES")


def test_the_display_metadata_is_filled():
    dag = kis_index_daily.kis_index_daily

    assert dag.dag_display_name
    assert dag.description
    assert dag.doc_md
    for param in dag.params.values():
        assert param.schema.get("title")
        assert param.description


def test_the_start_date_is_a_kst_midnight():
    start = kis_index_daily.kis_index_daily.start_date

    assert start.tzinfo is not None
    # KST 2026-08-24 00:00 = UTC 2026-08-23 15:00.
    assert start.astimezone(UTC) == datetime(2026, 8, 23, 15, 0, tzinfo=UTC)


def test_an_empty_end_date_means_the_run_day():
    assert kis_index_daily.requested_end_date(NOW_KST, {}) == date(2026, 8, 24)


def test_a_backfill_end_date_is_read_as_given():
    assert kis_index_daily.requested_end_date(NOW_KST, {"end_date": "2026-08-21"}) == date(2026, 8, 21)


@pytest.mark.parametrize("given", ["20260821", "2026-W34", "yesterday"])
def test_an_unreadable_end_date_fails_before_any_call(given):
    with pytest.raises(AirflowFailException, match="must be YYYY-MM-DD"):
        kis_index_daily.requested_end_date(NOW_KST, {"end_date": given})


def test_the_span_is_a_fixed_calendar_window():
    # 200달력일은 연휴가 끼어도 SMA60·EMA 안정화 120거래일을 확보하는 고정 창이다(4.4절).
    end = date(2026, 8, 24)
    assert kis_index_daily.span_start(end) == end - timedelta(days=kis_index_daily.SPAN_CALENDAR_DAYS)
    assert kis_index_daily.SPAN_CALENDAR_DAYS == 200


# ---------------------------------------------------------------------------
# 이력 백필
# ---------------------------------------------------------------------------


def test_an_empty_start_date_keeps_the_fixed_span():
    """일상 실행의 동작이 바뀌면 안 된다. 창 하나가 정확히 200달력일이다."""
    end_date = date(2026, 8, 25)

    start_date = kis_index_daily.requested_start_date(end_date, {})

    assert start_date == end_date - timedelta(days=kis_index_daily.SPAN_CALENDAR_DAYS)
    assert kis_index_daily.fetch_windows(start_date, end_date) == [(start_date, end_date)]


def test_a_backfill_span_is_cut_into_page_sized_windows():
    """한 심볼의 페이지 상한(`INDEX_DAILY_MAX_PAGES`)을 넘지 않으려고 끊는다.

    한 장이 50봉이고 200달력일이 약 135거래일이라 창 하나가 3장 안쪽이다(2026-08-26 실측).
    """
    start_date, end_date = date(2016, 8, 15), date(2026, 8, 25)

    windows = kis_index_daily.fetch_windows(start_date, end_date)

    assert windows[0][0] == start_date
    assert windows[-1][1] == end_date
    assert all(
        (window_end - window_start).days <= kis_index_daily.SPAN_CALENDAR_DAYS
        for window_start, window_end in windows
    )
    # 창끼리 겹치거나 벌어지지 않는다. 벌어지면 지표 계산 창에 구멍이 남는다.
    for earlier, later in pairwise(windows):
        assert later[0] == earlier[1] + timedelta(days=1)


def test_a_start_date_after_the_end_fails_before_any_call():
    """조용히 빈 구간이 되면 0건 저장을 정상으로 읽는다."""
    with pytest.raises(AirflowFailException, match="must not be after"):
        kis_index_daily.requested_start_date(date(2026, 8, 25), {"start_date": "2026-08-26"})


def test_an_iso_week_start_date_is_rejected():
    """`date.fromisoformat`은 `2026-W34`도 받아 그 주의 월요일로 바꾼다."""
    with pytest.raises(AirflowFailException, match="must be YYYY-MM-DD"):
        kis_index_daily.requested_start_date(date(2026, 8, 25), {"start_date": "2026-W34"})
