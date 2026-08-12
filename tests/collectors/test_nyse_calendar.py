import json
import re
from datetime import UTC, date, datetime
from typing import Self

import pytest
from sqlalchemy import Table

from apps.models.market import MarketSession
from apps.models.raw import SourceRecord
from modules.collectors.nyse_calendar import (
    MARKET_SESSION_US_UPSERT,
    SOURCE_RECORD_INSERT,
    NyseCalendar,
    NyseFetch,
    NyseParseError,
    parse_calendar,
    parse_cell,
    session_days,
    store_calendar,
)

SOURCE_RECORD_ID = 11
STARTED_AT = datetime(2026, 8, 12, 22, 0, tzinfo=UTC)
COMPLETED_AT = datetime(2026, 8, 12, 22, 0, 3, tzinfo=UTC)

# 2026-08-12 실측 페이지의 표를 그대로 옮겼다. 각주 마커와 괄호 주석, 그 해에 없는 휴일
# (`—*`)까지 실제 값이다.
TABLE_ROWS = [
    ["Holiday", "2026", "2027", "2028"],
    ["New Year’s Day", "Thursday, January 1", "Friday, January 1", "—*"],
    ["Martin Luther King, Jr. Day", "Monday, January 19", "Monday, January 18", "Monday, January 17"],
    ["Washington's Birthday", "Monday, February 16", "Monday, February 15", "Monday, February 21"],
    ["Good Friday", "Friday, April 3", "Friday, March 26", "Friday, April 14"],
    ["Memorial Day", "Monday, May 25", "Monday, May 31", "Monday, May 29"],
    [
        "Juneteenth National Independence Day",
        "Friday, June 19",
        "Friday, June 18 (Juneteenth National Independence Day observed)",
        "Monday, June 19",
    ],
    [
        "Independence Day",
        "Friday, July 3 (Independence Day observed)",
        "Monday, July 5 (Independence Day observed)",
        "Tuesday, July 4**",
    ],
    ["Labor Day", "Monday, September 7", "Monday, September 6", "Monday, September 4"],
    ["Thanksgiving Day", "Thursday, November 26***", "Thursday, November 25***", "Thursday, November 23***"],
    ["Christmas Day", "Friday, December 25****", "Friday, December 24 (Christmas Day observed)", "Monday, December 25"],
]


def html(rows: list[list[str]] = TABLE_ROWS) -> str:
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<html><body><p>Holidays</p><table>{body}</table></body></html>"


class FakeCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, statement: str, parameters: tuple) -> None:
        self.calls.append((statement, parameters))

    def executemany(self, statement: str, parameters) -> None:
        self.calls.extend((statement, tuple(row)) for row in parameters)

    def fetchone(self) -> tuple[int]:
        return (SOURCE_RECORD_ID,)


class FakeConnection:
    def __init__(self) -> None:
        self.recorded_cursor = FakeCursor()

    def cursor(self) -> FakeCursor:
        return self.recorded_cursor


@pytest.fixture(autouse=True)
def without_the_psycopg2_fast_path(monkeypatch):
    monkeypatch.setattr("modules.upsert._execute_batch", None)


def inserted_columns(statement: str) -> tuple[str, ...]:
    columns = re.search(r"INSERT INTO \w+ \(([^)]+)\)", statement, re.DOTALL)
    assert columns is not None
    names = re.sub(r"--[^\n]*", "", columns.group(1))
    return tuple(name.strip() for name in names.split(",") if name.strip())


def required_columns(table: Table) -> set[str]:
    return {
        column.name
        for column in table.columns
        if not column.nullable and column.server_default is None and not column.primary_key
    }


def test_us_upsert_matches_the_model_and_leaves_settlement_alone():
    table = MarketSession.__table__
    columns = inserted_columns(MARKET_SESSION_US_UPSERT)

    assert set(columns) <= {column.name for column in table.columns}
    assert required_columns(table) <= set(columns)
    assert "ON CONFLICT (market_code, session_date)" in MARKET_SESSION_US_UPSERT
    # 결제일은 KIS 해외 수집이 채운다. 매일 도는 이 upsert가 덮으면 안 된다.
    updated = set(re.findall(r"^\s{4}(\w+) = EXCLUDED", MARKET_SESSION_US_UPSERT, re.MULTILINE))
    assert "local_settlement_date" not in updated
    assert "domestic_settlement_date" not in updated
    assert "verification_source_record_id" not in updated


def test_source_record_insert_matches_the_model():
    table = SourceRecord.__table__
    columns = inserted_columns(SOURCE_RECORD_INSERT)

    assert set(columns) <= {column.name for column in table.columns}
    assert required_columns(table) <= set(columns)


@pytest.mark.parametrize(
    ("cell", "year", "expected"),
    [
        ("Thursday, January 1", 2026, date(2026, 1, 1)),
        ("Friday, July 3 (Independence Day observed)", 2026, date(2026, 7, 3)),
        ("Thursday, November 26***", 2026, date(2026, 11, 26)),
        ("Friday, December 25****", 2026, date(2026, 12, 25)),
        ("—*", 2028, None),
    ],
)
def test_cells_survive_markers_and_notes(cell, year, expected):
    assert parse_cell(cell, year) == expected


def test_a_weekday_that_disagrees_with_the_year_fails():
    # 2027-01-01은 금요일이다. 2026 열에 넣으면 요일이 먼저 틀어진다.
    with pytest.raises(NyseParseError, match="Thursday"):
        parse_cell("Thursday, January 1", 2027)


def test_an_unknown_month_fails():
    with pytest.raises(NyseParseError, match="unknown month"):
        parse_cell("Thursday, Januar 1", 2026)


def test_parse_reads_years_from_the_header():
    calendar = parse_calendar(html())

    assert calendar.years == (2026, 2027, 2028)
    assert date(2026, 7, 3) in calendar.holidays
    assert date(2026, 11, 26) in calendar.holidays
    # 2028년 신정은 토요일이라 표가 `—*`로 비워 뒀다.
    assert date(2028, 1, 1) not in calendar.holidays


def test_a_changed_column_count_fails():
    rows = [row[:] for row in TABLE_ROWS]
    rows[1].append("Monday, January 1")

    with pytest.raises(NyseParseError, match="cells, expected"):
        parse_calendar(html(rows))


def test_a_non_year_header_fails():
    rows = [row[:] for row in TABLE_ROWS]
    rows[0][1] = "This year"

    with pytest.raises(NyseParseError, match="not a year"):
        parse_calendar(html(rows))


def test_session_days_cover_every_day_of_every_supported_year():
    calendar = parse_calendar(html())

    days = session_days(calendar)
    verdicts = {day.session_date: day.open_day for day in days}

    assert len(days) == 365 + 365 + 366  # 2028이 윤년이다
    assert verdicts[date(2026, 7, 3)] is False  # 완전 휴장
    assert verdicts[date(2026, 8, 15)] is False  # 토요일
    assert verdicts[date(2026, 8, 16)] is False  # 일요일
    assert verdicts[date(2026, 8, 12)] is True  # 평범한 수요일
    assert verdicts[date(2026, 11, 27)] is True  # 조기 폐장은 개장으로 본다
    assert verdicts[date(2028, 1, 1)] is False  # 토요일이라 휴일이 아니어도 닫혀 있다


def test_store_writes_every_day_with_one_lineage_record():
    connection = FakeConnection()
    calendar = NyseCalendar(years=(2026,), holidays=(date(2026, 7, 3),))
    fetch = NyseFetch(
        url="https://www.nyse.com/trade/hours-calendars",
        html="<html></html>",
        status=200,
        started_at=STARTED_AT,
        completed_at=COMPLETED_AT,
    )

    count = store_calendar(connection, fetch, calendar)

    assert count == 365
    statement, parameters = connection.recorded_cursor.calls[0]
    assert "INSERT INTO source_record" in statement
    source_type, source, source_key, _, _, status, record_count, payload, metadata = parameters
    assert (source_type, source, source_key) == ("crawl", "nyse", "hours_calendars")
    assert (status, record_count) == ("succeeded", 365)
    # HTML은 jsonb에 넣을 수 없다.
    assert payload is None
    assert json.loads(metadata)["years"] == [2026]

    upserts = [
        parameters
        for upsert_statement, parameters in connection.recorded_cursor.calls
        if "INSERT INTO market_session" in upsert_statement
    ]
    assert upserts[0] == (date(2026, 1, 1), True, COMPLETED_AT, SOURCE_RECORD_ID)
    assert (date(2026, 7, 3), False, COMPLETED_AT, SOURCE_RECORD_ID) in upserts
