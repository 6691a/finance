"""DAG 객체와 params 해석만 검증한다.

수집·검증 규칙은 `modules/collectors/market/kis_future_daily.py`에 있고
`tests/collectors/test_kis_future_daily.py`가 덮는다. 구간 계산은 `modules/period.py`이고
`tests/modules/test_period.py`가 덮는다. 여기 남는 것은 스케줄, 화면 메타데이터,
파라미터가 틀렸을 때 어떤 Airflow 예외로 죽는지다.
"""

from datetime import UTC, date, datetime, timedelta

import pytest
from airflow.sdk.exceptions import AirflowFailException

from dags import kis_future_daily
from modules.utility import KST_TIMEZONE

NOW_KST = datetime(2026, 8, 24, 18, 30, tzinfo=KST_TIMEZONE)


def test_the_dag_runs_after_the_index_bars_and_before_the_signals():
    dag = kis_future_daily.kis_future_daily

    # KST 평일 18:30 = UTC 평일 09:30. 현물 지수 일봉(18:20) 뒤, 신호 계산(18:40) 앞이다.
    assert dag.schedule == "30 18 * * 1-5"
    assert dag.max_active_runs == 1
    assert set(dag.task_dict) == {"collect"}


def test_the_display_metadata_is_filled():
    dag = kis_future_daily.kis_future_daily

    assert dag.dag_display_name
    assert dag.description
    assert dag.doc_md
    for param in dag.params.values():
        assert param.schema.get("title")
        assert param.description


def test_the_start_date_is_a_kst_midnight():
    start = kis_future_daily.kis_future_daily.start_date

    assert start.tzinfo is not None
    assert start.astimezone(UTC) == datetime(2026, 8, 27, 15, 0, tzinfo=UTC)


def test_an_empty_end_date_means_the_run_day():
    assert kis_future_daily.requested_end_date(NOW_KST, {}) == date(2026, 8, 24)


def test_a_backfill_end_date_is_read_as_given():
    assert kis_future_daily.requested_end_date(NOW_KST, {"end_date": "2026-08-21"}) == date(2026, 8, 21)


@pytest.mark.parametrize("given", ["20260821", "2026-W34", "yesterday"])
def test_an_unreadable_end_date_fails_before_any_call(given):
    """되돌릴 수 없는 설정 오류다. `ValueError`가 아니라 즉시 죽는 예외로 올린다."""
    with pytest.raises(AirflowFailException, match="must be YYYY-MM-DD"):
        kis_future_daily.requested_end_date(NOW_KST, {"end_date": given})


def test_an_empty_start_date_keeps_the_fixed_span():
    end_date = date(2026, 8, 25)

    start_date = kis_future_daily.requested_start_date(end_date, {})

    assert start_date == end_date - timedelta(days=kis_future_daily.SPAN_CALENDAR_DAYS)


def test_a_start_date_after_the_end_fails_before_any_call():
    """조용히 빈 구간이 되면 0건 저장을 정상으로 읽는다."""
    with pytest.raises(AirflowFailException, match="must not be after"):
        kis_future_daily.requested_start_date(date(2026, 8, 25), {"start_date": "2026-08-26"})


def test_an_iso_week_start_date_is_rejected():
    with pytest.raises(AirflowFailException, match="must be YYYY-MM-DD"):
        kis_future_daily.requested_start_date(date(2026, 8, 25), {"start_date": "2026-W34"})


def test_the_backfill_span_is_cut_by_the_shared_rule():
    """구간 끊기의 원본은 `modules/period.py`다. DAG마다 복사하지 않는다."""
    from modules.period import fetch_windows

    assert kis_future_daily.fetch_windows is fetch_windows
