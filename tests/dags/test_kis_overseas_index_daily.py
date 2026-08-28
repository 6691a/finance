"""DAG 객체와 params 해석만 검증한다.

수집·검증 규칙은 `modules/collectors/market/kis_overseas_index_daily.py`에 있고
`tests/collectors/test_kis_overseas_index_daily_collector.py`가 덮는다. 구간 계산은
`modules/period.py`이고 `tests/modules/test_period.py`가 덮는다.
"""

from datetime import UTC, date, datetime, timedelta

import pendulum
import pytest
from airflow.sdk.exceptions import AirflowFailException

from dags import kis_overseas_index_close, kis_overseas_index_daily

SESSION_DATE = date(2026, 8, 21)


def test_the_dag_runs_after_the_close_bars_and_before_the_briefing():
    dag = kis_overseas_index_daily.kis_overseas_index_daily

    # KST 화~토 07:35 = UTC 월~금 22:35. 캘린더(07:00)와 마감 분봉(07:30) 뒤, 브리핑(08:00) 앞이다.
    assert dag.schedule == "35 7 * * 2-6"
    assert dag.max_active_runs == 1
    assert set(dag.task_dict) == {"collect"}


def test_the_close_and_daily_dags_do_not_collide():
    """마감 분봉이 먼저 끝나야 한다. 둘 다 같은 KIS 토큰 캐시를 쓴다."""
    assert kis_overseas_index_close.SCHEDULE == "30 7 * * 2-6"
    assert kis_overseas_index_daily.SCHEDULE == "35 7 * * 2-6"


def test_the_display_metadata_is_filled():
    dag = kis_overseas_index_daily.kis_overseas_index_daily

    assert dag.dag_display_name
    assert dag.description
    assert dag.doc_md
    for param in dag.params.values():
        assert param.schema.get("title")
        assert param.description


def test_the_start_date_is_a_kst_midnight():
    start = kis_overseas_index_daily.kis_overseas_index_daily.start_date

    assert start.tzinfo is not None
    assert start.astimezone(UTC) == datetime(2026, 8, 27, 15, 0, tzinfo=UTC)


def test_the_session_date_comes_from_the_run(monkeypatch):
    """벽시계가 아니라 이 run의 시각이다. 마감 분봉 DAG과 같은 규칙이다."""
    run_after = pendulum.datetime(2026, 8, 22, 7, 35, tz="Asia/Seoul")
    monkeypatch.setattr(
        kis_overseas_index_daily,
        "get_current_context",
        lambda: {"data_interval_end": run_after},
    )

    # KST 토요일 07:35 = 뉴욕 금요일 18:35. 막 끝난 세션은 금요일이다.
    assert kis_overseas_index_daily._session_date() == date(2026, 8, 21)


def test_the_session_date_falls_back_to_the_run_after(monkeypatch):
    """수동 run에는 `data_interval_end`가 없다. `datetime.now`로 물러서지 않는다."""
    run_after = pendulum.datetime(2026, 8, 22, 7, 35, tz="Asia/Seoul")
    monkeypatch.setattr(
        kis_overseas_index_daily,
        "get_current_context",
        lambda: {"data_interval_end": None, "dag_run": type("Run", (), {"run_after": run_after})()},
    )

    assert kis_overseas_index_daily._session_date() == date(2026, 8, 21)


def test_an_empty_end_date_means_the_session_date():
    assert kis_overseas_index_daily.requested_end_date(SESSION_DATE, {}) == SESSION_DATE


def test_a_backfill_end_date_is_read_as_given():
    given = {"end_date": "2026-08-19"}
    assert kis_overseas_index_daily.requested_end_date(SESSION_DATE, given) == date(2026, 8, 19)


@pytest.mark.parametrize("given", ["20260821", "2026-W34", "yesterday"])
def test_an_unreadable_end_date_fails_before_any_call(given):
    """되돌릴 수 없는 설정 오류다. `ValueError`가 아니라 즉시 죽는 예외로 올린다."""
    with pytest.raises(AirflowFailException, match="must be YYYY-MM-DD"):
        kis_overseas_index_daily.requested_end_date(SESSION_DATE, {"end_date": given})


def test_an_empty_start_date_keeps_the_fixed_span():
    start_date = kis_overseas_index_daily.requested_start_date(SESSION_DATE, {})

    assert start_date == SESSION_DATE - timedelta(days=kis_overseas_index_daily.SPAN_CALENDAR_DAYS)


def test_a_start_date_after_the_end_fails_before_any_call():
    with pytest.raises(AirflowFailException, match="must not be after"):
        kis_overseas_index_daily.requested_start_date(SESSION_DATE, {"start_date": "2026-08-22"})


def test_an_iso_week_start_date_is_rejected():
    with pytest.raises(AirflowFailException, match="must be YYYY-MM-DD"):
        kis_overseas_index_daily.requested_start_date(SESSION_DATE, {"start_date": "2026-W34"})


def test_the_backfill_span_is_cut_by_the_shared_rule():
    """구간 끊기의 원본은 `modules/period.py`다. DAG마다 복사하지 않는다."""
    from modules.period import fetch_windows

    assert kis_overseas_index_daily.fetch_windows is fetch_windows
