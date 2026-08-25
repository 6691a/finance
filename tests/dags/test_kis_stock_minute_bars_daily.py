"""DAG 객체와 params 해석만 검증한다.

봉을 걷고 거르는 규칙은 `modules/collectors/kis.py`에 있고 `tests/collectors/test_kis.py`가 덮는다.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Self

import pytest
from airflow.exceptions import AirflowFailException

from dags import kis_stock_minute_bars_daily
from modules.utility import KST_TIMEZONE

NOW_KST = datetime(2026, 8, 14, 18, 40, tzinfo=KST_TIMEZONE)


class FakeCursor:
    def __init__(self, row) -> None:
        self.row = row
        self.calls: list[tuple[str, tuple]] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, statement: str, parameters: tuple) -> None:
        self.calls.append((statement, parameters))

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, row) -> None:
        self.recorded_cursor = FakeCursor(row)

    def cursor(self) -> FakeCursor:
        return self.recorded_cursor


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


def test_days_above_the_cap_fail():
    """`max_active_runs=1`이라 긴 백필 run 하나가 그날 마감 확정 run을 직접 점유한다."""
    with pytest.raises(AirflowFailException, match="at most 31"):
        kis_stock_minute_bars_daily.requested_days({"days": kis_stock_minute_bars_daily.MAX_DAYS + 1})


def test_the_cap_is_also_on_the_param():
    """UI 트리거는 태스크에 닿기 전에 막는다. 태스크 검사는 그 밖의 경로를 위한 것이다."""
    # ParamsDict의 `[]`는 해석된 값을 준다. Param 객체 자체는 `get_param`이 준다.
    param = kis_stock_minute_bars_daily.kis_stock_minute_bars_daily.params.get_param("days")

    assert param.schema["maximum"] == kis_stock_minute_bars_daily.MAX_DAYS
    assert param.schema["minimum"] == 1


def test_the_previous_close_is_read_for_the_day_before():
    """그날 종가를 분모로 쓰면 변동률이 항상 0에 가깝게 나온다."""
    connection = FakeConnection((Decimal(268000),))

    value = kis_stock_minute_bars_daily.previous_close(connection, "005930", date(2026, 8, 14))

    statement, parameters = connection.recorded_cursor.calls[0]
    assert value == Decimal(268000)
    assert parameters == ("005930", date(2026, 8, 14))
    assert "business_date < %s" in statement
    assert "ORDER BY business_date DESC" in statement


def test_a_missing_previous_close_is_none():
    """확정 일별 수급이 그 구간을 아직 안 채웠다는 뜻이다. 분모를 지어내지 않는다."""
    connection = FakeConnection(None)

    assert kis_stock_minute_bars_daily.previous_close(connection, "005930", date(2026, 8, 14)) is None
