"""DAG 객체와 params 해석만 검증한다.

수집·검증 규칙은 `modules/collectors/kis.py`의 `fetch_index_daily`/`store_index_daily`에 있고
`tests/collectors/test_kis.py`가 덮는다. 설계는 docs/market-technical-indicators.md 4.4절이다.
"""

from datetime import UTC, date, datetime, timedelta

import pytest
from airflow.exceptions import AirflowFailException

from dags import kis_index_daily
from modules.utility import KST_TIMEZONE

NOW_KST = datetime(2026, 8, 24, 18, 20, tzinfo=KST_TIMEZONE)


def test_the_dag_runs_after_the_daily_bars_are_final():
    dag = kis_index_daily.kis_index_daily

    # KST 평일 18:20 = UTC 평일 09:20. 종목 확정 일봉(18:10) 뒤, 신호 계산(18:40) 앞이다.
    assert dag.schedule == "20 18 * * 1-5"
    assert dag.max_active_runs == 1
    assert set(dag.task_dict) == {"collect"}


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
