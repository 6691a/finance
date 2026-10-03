"""DAG 객체와 받을 날짜 계산만 검증한다. 수집 규칙은 tests/collectors/test_dtcc_cds.py가 덮는다."""

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from airflow.sdk.exceptions import AirflowFailException

from dags import dtcc_cds_daily

DAG = dtcc_cds_daily.dtcc_cds_daily


def test_the_dag_runs_after_the_file_is_posted():
    # 파일은 다음 날 00:15 UTC쯤 올라온다. KST 화~토 10:00 = UTC 화~토 01:00.
    assert DAG.schedule == "0 10 * * 2-6"
    assert DAG.max_active_runs == 1


def test_the_display_metadata_is_filled():
    assert DAG.dag_display_name
    assert DAG.description
    assert DAG.doc_md
    for param in DAG.params.values():
        assert param.schema.get("title")
        assert param.description


def test_a_scheduled_run_takes_the_previous_utc_day():
    context = {"params": {}, "data_interval_end": datetime(2026, 10, 6, 1, 0, tzinfo=UTC)}

    assert dtcc_cds_daily.resolve_trade_dates(context) == [date(2026, 10, 5)]


def test_a_manual_run_falls_back_to_run_after():
    context = {"params": {}, "dag_run": SimpleNamespace(run_after=datetime(2026, 10, 3, 1, 0, tzinfo=UTC))}

    assert dtcc_cds_daily.resolve_trade_dates(context) == [date(2026, 10, 2)]


def test_a_range_skips_weekends():
    context = {"params": {"trade_start": "2026-09-25", "trade_end": "2026-09-29"}}

    assert dtcc_cds_daily.resolve_trade_dates(context) == [date(2026, 9, 25), date(2026, 9, 28), date(2026, 9, 29)]


def test_a_reversed_range_fails():
    with pytest.raises(AirflowFailException):
        dtcc_cds_daily.resolve_trade_dates({"params": {"trade_start": "2026-09-30", "trade_end": "2026-09-29"}})
