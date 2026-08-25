"""DAG 객체와 params 해석만 검증한다.

봉을 걷고 거르는 규칙과 확정 종가 조회는 `modules/collectors/kis.py`에 있고
`tests/collectors/test_kis.py`가 덮는다.
"""

from datetime import UTC, date, datetime

import pytest
from airflow.exceptions import AirflowFailException

from dags import kis_stock_minute_bars_daily
from modules.utility import KST_TIMEZONE

NOW_KST = datetime(2026, 8, 14, 18, 40, tzinfo=KST_TIMEZONE)


def test_the_dag_runs_after_the_daily_flow_dag():
    """전일종가를 `stock_investor_trade_daily`에서 읽으므로 그 DAG 뒤에 돌아야 한다."""
    dag = kis_stock_minute_bars_daily.kis_stock_minute_bars_daily

    # KST 평일 20:05 = UTC 평일 11:05. 확정 일별 수급은 18:10, NXT 애프터마켓은 20:00에
    # 끝나고, 20:15 최종 브리핑보다 먼저 돌아 확정 봉을 넘긴다.
    assert dag.schedule == "5 20 * * 1-5"
    assert dag.max_active_runs == 1
    assert set(dag.task_dict) == {"collect"}


def test_the_start_date_is_a_kst_midnight():
    start = kis_stock_minute_bars_daily.kis_stock_minute_bars_daily.start_date

    assert start.tzinfo is not None
    assert start.astimezone(UTC) == datetime(2026, 8, 14, 15, 0, tzinfo=UTC)


def test_an_empty_business_date_means_the_run_day():
    assert kis_stock_minute_bars_daily.requested_business_date(NOW_KST, {}) == date(2026, 8, 14)


def test_a_backfill_date_is_read_as_given():
    given = {"business_date": "2026-07-03"}

    assert kis_stock_minute_bars_daily.requested_business_date(NOW_KST, given) == date(2026, 7, 3)


@pytest.mark.parametrize("given", ["20260814", "2026-W33", "yesterday"])
def test_a_date_that_is_not_a_calendar_day_fails(given):
    """`date.fromisoformat`은 앞의 둘을 받는다. 주 표기는 그 주 월요일이 되어 조용히 어긋난다."""
    with pytest.raises(AirflowFailException, match="must be YYYY-MM-DD"):
        kis_stock_minute_bars_daily.requested_business_date(NOW_KST, {"business_date": given})


@pytest.mark.parametrize(("given", "expected"), [({}, 1), ({"days": 5}, 5), ({"days": None}, 1)])
def test_days_default_to_one(given, expected):
    assert kis_stock_minute_bars_daily.requested_days(given) == expected


def test_days_below_one_fail():
    with pytest.raises(AirflowFailException, match="at least 1"):
        kis_stock_minute_bars_daily.requested_days({"days": 0})
