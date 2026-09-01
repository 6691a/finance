"""DAG 객체와 params 해석만 검증한다.

봉을 걷고 거르는 규칙과 확정 종가 조회는 `modules/collectors/kis.py`에 있고
`tests/collectors/test_kis.py`가 덮는다.
"""

from datetime import UTC, date, datetime

import pendulum
import pytest
from airflow.sdk.exceptions import AirflowFailException

from dags import kis_stock_minute_bars_daily
from modules.utility import KST_TIMEZONE

RUN_DATE = date(2026, 8, 14)


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


def test_the_business_date_comes_from_the_run(monkeypatch):
    """벽시계가 아니라 이 run의 시각이다. 해외지수 마감 DAG과 같은 규칙이다."""
    monkeypatch.setattr(
        kis_stock_minute_bars_daily,
        "get_current_context",
        lambda: {"data_interval_end": pendulum.datetime(2026, 8, 14, 20, 5, tz=KST_TIMEZONE)},
    )

    assert kis_stock_minute_bars_daily._run_date() == date(2026, 8, 14)


def test_the_business_date_falls_back_to_the_run_after(monkeypatch):
    """수동 run에는 `data_interval_end`가 없다. `datetime.now`로 물러서지 않는다."""
    run_after = pendulum.datetime(2026, 8, 14, 20, 5, tz=KST_TIMEZONE)
    monkeypatch.setattr(
        kis_stock_minute_bars_daily,
        "get_current_context",
        lambda: {"data_interval_end": None, "dag_run": type("Run", (), {"run_after": run_after})()},
    )

    assert kis_stock_minute_bars_daily._run_date() == date(2026, 8, 14)


def test_a_cleared_run_keeps_its_own_day(monkeypatch):
    """며칠 뒤 clear 해도 그 run이 맡은 날을 받는다. `days` 기본이 1이라 덮어 줄 창이 없다."""
    monkeypatch.setattr(
        kis_stock_minute_bars_daily,
        "get_current_context",
        lambda: {"data_interval_end": pendulum.datetime(2026, 8, 11, 20, 5, tz=KST_TIMEZONE)},
    )

    assert kis_stock_minute_bars_daily.requested_business_date(kis_stock_minute_bars_daily._run_date(), {}) == date(
        2026, 8, 11
    )


def test_an_empty_business_date_means_the_run_day():
    assert kis_stock_minute_bars_daily.requested_business_date(RUN_DATE, {}) == RUN_DATE


def test_a_backfill_date_is_read_as_given():
    given = {"business_date": "2026-07-03"}

    assert kis_stock_minute_bars_daily.requested_business_date(RUN_DATE, given) == date(2026, 7, 3)


@pytest.mark.parametrize("given", ["20260814", "2026-W33", "yesterday"])
def test_a_date_that_is_not_a_calendar_day_fails(given):
    """`date.fromisoformat`은 앞의 둘을 받는다. 주 표기는 그 주 월요일이 되어 조용히 어긋난다."""
    with pytest.raises(AirflowFailException, match="must be YYYY-MM-DD"):
        kis_stock_minute_bars_daily.requested_business_date(RUN_DATE, {"business_date": given})


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


def test_a_run_that_could_not_call_kis_at_all_fails():
    """전일종가가 두 종목 다 없으면 호출 0회·`failures` 0건으로 초록이었다(G-35). 하루 한 번
    도는 확정 수집이라 다시 집는 실행이 없다."""
    with pytest.raises(AirflowFailException, match="no previous close"):
        kis_stock_minute_bars_daily.require_attempts(0, RUN_DATE, 1)


def test_a_run_that_called_kis_passes_through():
    kis_stock_minute_bars_daily.require_attempts(2, RUN_DATE, 1)


def test_no_bars_on_an_open_day_is_a_failure():
    """이 DAG만 `krx_open_day`를 안 물어 개장일 0봉과 휴장일이 같았다."""
    assert kis_stock_minute_bars_daily.no_bars_failure(True, "005930:KRX:2026-08-14") == (
        "005930:KRX:2026-08-14(no bars on an open day)"
    )


@pytest.mark.parametrize("open_day", [False, None])
def test_no_bars_on_a_closed_or_unknown_day_is_normal(open_day):
    """휴장일은 0봉이 정상이고, 캘린더가 모르면(None) 저장소 규칙대로 fail-open이다."""
    assert kis_stock_minute_bars_daily.no_bars_failure(open_day, "005930:KRX:2026-08-14") is None
