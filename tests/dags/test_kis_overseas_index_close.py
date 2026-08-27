"""DAG 객체와 휴장 판정만 검증한다. 파싱·저장은 `tests/collectors/test_kis_overseas_index.py`가 덮는다."""

from datetime import UTC, date, datetime
from typing import Self

import pytest
from airflow.sdk.exceptions import AirflowSkipException

from dags import kis_overseas_index_close, slack_us_market_briefing
from modules.briefing import market_data


class FakeCursor:
    def __init__(self, row: tuple | None) -> None:
        self.row = row

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, statement: str, parameters: tuple) -> None:
        self.statement = statement
        self.parameters = parameters

    def fetchone(self) -> tuple | None:
        return self.row


class FakeConnection:
    def __init__(self, row: tuple | None) -> None:
        self.recorded_cursor = FakeCursor(row)
        self.closed = False

    def cursor(self) -> FakeCursor:
        return self.recorded_cursor

    def close(self) -> None:
        self.closed = True


def fake_hook(monkeypatch, row: tuple | None) -> FakeConnection:
    connection = FakeConnection(row)

    class Hook:
        def __init__(self, postgres_conn_id: str) -> None:
            self.postgres_conn_id = postgres_conn_id

        def get_conn(self) -> FakeConnection:
            return connection

    monkeypatch.setattr(kis_overseas_index_close, "PostgresHook", Hook)
    return connection


def test_the_dag_runs_after_the_us_close_and_before_the_briefing():
    # KST 화~토 07:30 = UTC 월~금 22:30. 미국 마감(KST 05/06시) 뒤, 브리핑(08:00) 앞이다.
    assert kis_overseas_index_close.SCHEDULE == "30 7 * * 2-6"
    assert kis_overseas_index_close.kis_overseas_index_close.schedule == kis_overseas_index_close.SCHEDULE
    minute, hour, *_, weekdays = kis_overseas_index_close.SCHEDULE.split()
    briefing_minute, briefing_hour, *_, briefing_weekdays = slack_us_market_briefing.SCHEDULE.split()
    assert weekdays == briefing_weekdays
    assert (int(hour), int(minute)) < (int(briefing_hour), int(briefing_minute))


def test_the_dag_has_one_task_and_no_overlap():
    dag = kis_overseas_index_close.kis_overseas_index_close

    assert set(dag.task_dict) == {"collect"}
    assert dag.max_active_runs == 1
    assert dag.params == {}


def test_display_metadata_is_filled():
    dag = kis_overseas_index_close.kis_overseas_index_close

    assert dag.dag_display_name.endswith("(KIS)")
    assert dag.description
    assert dag.doc_md and "102봉" in dag.doc_md


@pytest.mark.parametrize(
    ("row", "skips"),
    [((False,), True), ((True,), False), ((None,), False), (None, False)],
)
def test_it_skips_only_on_a_confirmed_us_holiday(monkeypatch, row, skips):
    """모르면(캘린더 없음) 진행한다. 묵은 날짜 검사가 뒤에서 잡는다."""
    connection = fake_hook(monkeypatch, row)
    session_date = date(2026, 8, 24)

    if skips:
        with pytest.raises(AirflowSkipException):
            kis_overseas_index_close._skip_when_closed(session_date)
    else:
        kis_overseas_index_close._skip_when_closed(session_date)

    assert connection.closed
    assert connection.recorded_cursor.parameters == ("US_EQUITY", session_date)


def test_the_session_date_is_the_new_york_date():
    # KST 화요일 07:30 = UTC 월요일 22:30 = 뉴욕 월요일 18:30. 막 끝난 세션은 월요일이다.
    moment = datetime(2026, 8, 24, 22, 30, tzinfo=UTC)

    assert kis_overseas_index_close.us_session_date(moment) == date(2026, 8, 24)
    assert kis_overseas_index_close.us_session_date(moment) == market_data.us_session_date(moment)


def test_the_session_date_comes_from_the_run_not_the_wall_clock(monkeypatch):
    # KST 토 07:30 인터벌 = UTC 금 22:30 = 뉴욕 금 18:30. 그 run이 월요일 오후에 돌아도 금요일 세션이다.
    interval_end = datetime(2026, 8, 21, 22, 30, tzinfo=UTC)

    class FakeRun:
        run_after = datetime(2026, 8, 24, 22, 30, tzinfo=UTC)

    monkeypatch.setattr(
        kis_overseas_index_close,
        "get_current_context",
        lambda: {"data_interval_end": interval_end, "dag_run": FakeRun()},
    )
    assert kis_overseas_index_close._session_date() == date(2026, 8, 21)

    # data interval이 없는 수동 run은 run_after로 떨어진다.
    monkeypatch.setattr(
        kis_overseas_index_close,
        "get_current_context",
        lambda: {"data_interval_end": None, "dag_run": FakeRun()},
    )
    assert kis_overseas_index_close._session_date() == date(2026, 8, 24)
