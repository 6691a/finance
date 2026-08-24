"""DAG 객체와 params 해석만 검증한다.

검출·저장 규칙은 `modules/technical_signals.py`에 있고 `tests/modules/test_technical_signals.py`가
덮는다. 설계는 docs/market-technical-indicators.md 12.3절이다.
"""

from datetime import UTC, datetime

import pytest
from airflow.exceptions import AirflowFailException

from dags import technical_signal_daily
from modules.technical import SIGNAL_SCAN_BARS_MAX


def test_the_dag_runs_after_both_daily_collectors():
    dag = technical_signal_daily.technical_signal_daily

    # 종목 확정 일봉 18:10, 지수 일봉 18:20 뒤다. KST 평일 18:40 = UTC 평일 09:40.
    assert dag.schedule == "40 18 * * 1-5"
    assert dag.max_active_runs == 1
    assert set(dag.task_dict) == {"detect"}


def test_the_start_date_is_a_kst_midnight():
    start = technical_signal_daily.technical_signal_daily.start_date

    assert start.tzinfo is not None
    # KST 2026-08-24 00:00 = UTC 2026-08-23 15:00.
    assert start.astimezone(UTC) == datetime(2026, 8, 23, 15, 0, tzinfo=UTC)


def test_the_display_metadata_is_filled():
    dag = technical_signal_daily.technical_signal_daily

    assert dag.dag_display_name
    assert dag.description
    assert dag.doc_md
    for param in dag.params.values():
        assert param.schema.get("title")
        assert param.description


def test_scan_bars_defaults_to_a_few_days():
    """앞단이 하루 늦게 복구돼도 사건이 빠지지 않을 만큼만 되돌아본다."""
    assert technical_signal_daily.requested_scan_bars({}) == technical_signal_daily.DEFAULT_SCAN_BARS
    assert technical_signal_daily.requested_scan_bars({"scan_bars": None}) == technical_signal_daily.DEFAULT_SCAN_BARS
    assert technical_signal_daily.DEFAULT_SCAN_BARS > 1


def test_a_backfill_scan_is_read_as_given():
    assert technical_signal_daily.requested_scan_bars({"scan_bars": 120}) == 120


@pytest.mark.parametrize("given", [0, -1, SIGNAL_SCAN_BARS_MAX + 1])
def test_a_scan_outside_the_range_fails_before_any_query(given):
    """0을 조용히 기본값으로 되돌리면 운영자가 뜻한 것과 다른 구간을 훑는다."""
    with pytest.raises(AirflowFailException, match="must be between"):
        technical_signal_daily.requested_scan_bars({"scan_bars": given})


def test_the_scan_cap_is_the_lookback_window():
    """계산에 쓰는 봉보다 더 되돌아볼 수는 없다."""
    from modules.technical import TECHNICAL_LOOKBACK_BARS

    assert SIGNAL_SCAN_BARS_MAX == TECHNICAL_LOOKBACK_BARS
