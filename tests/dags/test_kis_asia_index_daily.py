"""DAG 객체와 params 해석만 검증한다.

수집·검증 규칙은 `modules/collectors/market/kis_overseas_index_daily.py`에 있고
`tests/collectors/test_kis_overseas_index_daily_collector.py`·`test_kis_asia_index.py`가 덮는다.
"""

from datetime import UTC, date, datetime, timedelta

import pendulum
import pytest
from airflow.sdk.exceptions import AirflowFailException

from dags import kis_asia_index_daily, kis_asia_index_intraday

SESSION_DATE = date(2026, 9, 4)


def test_the_dag_runs_after_every_asian_close():
    dag = kis_asia_index_daily.kis_asia_index_daily

    # KST 평일 18:00 = UTC 09:00. 항셍(KST 17:00)이 가장 늦고 지연 15분 + 정산 봉 뒤다.
    assert kis_asia_index_daily.SCHEDULE == "0 18 * * 1-5"
    assert dag.schedule == kis_asia_index_daily.SCHEDULE
    assert dag.max_active_runs == 1
    assert set(dag.task_dict) == {"collect"}


def test_the_daily_dag_starts_after_the_intraday_polling_ends():
    """같은 토큰 캐시를 쓰고, 그날 마지막 분봉이 들어온 뒤라야 일봉이 확정값이다."""
    _, intraday_hours, *_ = kis_asia_index_intraday.SCHEDULE.split()
    _, daily_hour, *_ = kis_asia_index_daily.SCHEDULE.split()
    assert int(daily_hour) > int(intraday_hours.split("-")[1])


def test_the_display_metadata_is_filled():
    dag = kis_asia_index_daily.kis_asia_index_daily

    assert dag.dag_display_name.endswith("(KIS)")
    assert dag.description
    assert dag.doc_md
    for param in dag.params.values():
        assert param.schema.get("title")
        assert param.description


def test_the_start_date_is_a_kst_midnight():
    start = kis_asia_index_daily.kis_asia_index_daily.start_date

    assert start.tzinfo is not None
    assert start.astimezone(UTC) == datetime(2026, 9, 4, 15, 0, tzinfo=UTC)


def test_the_session_date_is_the_kst_date_of_the_run(monkeypatch):
    """아시아 네 시장의 거래일은 KST 날짜와 같다(도쿄는 같은 시각, 나머지는 한 시간 뒤)."""
    run_after = pendulum.datetime(2026, 9, 4, 18, 0, tz="Asia/Seoul")
    monkeypatch.setattr(kis_asia_index_daily, "get_current_context", lambda: {"data_interval_end": run_after})

    assert kis_asia_index_daily._session_date() == date(2026, 9, 4)


def test_the_session_date_falls_back_to_the_run_after(monkeypatch):
    """수동 run에는 `data_interval_end`가 없다. `datetime.now`로 물러서지 않는다."""
    run_after = pendulum.datetime(2026, 9, 4, 18, 0, tz="Asia/Seoul")
    monkeypatch.setattr(
        kis_asia_index_daily,
        "get_current_context",
        lambda: {"data_interval_end": None, "dag_run": type("Run", (), {"run_after": run_after})()},
    )

    assert kis_asia_index_daily._session_date() == date(2026, 9, 4)


def test_an_empty_end_date_means_the_session_date():
    assert kis_asia_index_daily.requested_end_date(SESSION_DATE, {}) == SESSION_DATE


def test_a_backfill_end_date_is_read_as_given():
    assert kis_asia_index_daily.requested_end_date(SESSION_DATE, {"end_date": "2026-08-19"}) == date(2026, 8, 19)


@pytest.mark.parametrize("given", ["20260904", "2026-W36", "yesterday"])
def test_an_unreadable_end_date_fails_before_any_call(given):
    with pytest.raises(AirflowFailException, match="must be YYYY-MM-DD"):
        kis_asia_index_daily.requested_end_date(SESSION_DATE, {"end_date": given})


def test_an_empty_start_date_keeps_the_fixed_span():
    start_date = kis_asia_index_daily.requested_start_date(SESSION_DATE, {})

    assert start_date == SESSION_DATE - timedelta(days=kis_asia_index_daily.SPAN_CALENDAR_DAYS)


def test_a_start_date_after_the_end_fails_before_any_call():
    with pytest.raises(AirflowFailException, match="must not be after"):
        kis_asia_index_daily.requested_start_date(SESSION_DATE, {"start_date": "2026-09-05"})


def test_the_backfill_span_is_cut_by_the_shared_rule():
    from modules.period import fetch_windows

    assert kis_asia_index_daily.fetch_windows is fetch_windows
