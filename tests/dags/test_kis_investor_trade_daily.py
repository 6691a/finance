"""DAG 객체와 params 해석만 검증한다.

파싱과 저장 규칙은 `modules/collectors/kis_investor_flow.py`에 있고 `tests/collectors/`가 덮는다.
"""

from datetime import UTC, date, datetime

import pytest
from airflow.exceptions import AirflowFailException

from dags import kis_investor_trade_daily
from modules.utility import KST_TIMEZONE

NOW_KST = datetime(2026, 8, 14, 18, 10, tzinfo=KST_TIMEZONE)


def test_the_dag_runs_after_the_session_closes():
    dag = kis_investor_trade_daily.kis_investor_trade_daily

    # 확정값이라 장중에 부를 이유가 없다. KST 평일 18:10 = UTC 평일 09:10.
    assert dag.schedule == "10 18 * * 1-5"
    assert dag.max_active_runs == 1
    assert set(dag.task_dict) == {"collect"}


def test_the_start_date_is_a_kst_midnight():
    """naive start_date 는 배포 환경의 시계를 타서 첫 run 이 하루 어긋난다."""
    start = kis_investor_trade_daily.kis_investor_trade_daily.start_date

    assert start.tzinfo is not None
    # Airflow 가 UTC 로 정규화한다. KST 2026-08-15 00:00 = UTC 2026-08-14 15:00.
    assert start.astimezone(UTC) == datetime(2026, 8, 14, 15, 0, tzinfo=UTC)


def test_an_empty_end_date_means_the_run_day():
    assert kis_investor_trade_daily.requested_end_date(NOW_KST, {}) == date(2026, 8, 14)


def test_a_backfill_end_date_is_read_as_given():
    given = {"end_date": "2026-07-01"}

    assert kis_investor_trade_daily.requested_end_date(NOW_KST, given) == date(2026, 7, 1)


def test_an_unreadable_end_date_fails_before_any_call():
    """조용히 오늘로 되돌리면 운영자가 백필했다고 믿는 구간이 비어 있게 된다."""
    with pytest.raises(AirflowFailException, match="must be YYYY-MM-DD"):
        kis_investor_trade_daily.requested_end_date(NOW_KST, {"end_date": "20260701"})


def test_an_iso_week_end_date_is_rejected():
    """date.fromisoformat 은 2026-W32 를 그 주 월요일로 받는다. 조용히 다른 구간이 된다."""
    with pytest.raises(AirflowFailException, match="must be YYYY-MM-DD"):
        kis_investor_trade_daily.requested_end_date(NOW_KST, {"end_date": "2026-W32"})


@pytest.mark.parametrize(("given", "expected"), [({}, 1), ({"pages": 6}, 6), ({"pages": None}, 1)])
def test_pages_default_to_one(given, expected):
    assert kis_investor_trade_daily.requested_pages(given) == expected


def test_pages_below_one_fail():
    with pytest.raises(AirflowFailException, match="at least 1"):
        kis_investor_trade_daily.requested_pages({"pages": 0})
