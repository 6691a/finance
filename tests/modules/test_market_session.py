from datetime import date
from typing import Self

import pytest

from modules.market_session import krx_open_day, market_open_day, us_equity_open_day


class FakeCursor:
    def __init__(self, row: tuple | None) -> None:
        self.row = row
        self.calls: list[tuple[str, tuple]] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, statement: str, parameters: tuple) -> None:
        self.calls.append((statement, parameters))

    def fetchone(self) -> tuple | None:
        return self.row


class FakeConnection:
    def __init__(self, row: tuple | None) -> None:
        self.recorded_cursor = FakeCursor(row)

    def cursor(self) -> FakeCursor:
        return self.recorded_cursor


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ((True,), True),
        ((False,), False),
        # 아직 판정하지 않은 날짜. 조회하는 쪽은 개장과 같게 다룬다.
        ((None,), None),
        # 캘린더가 아직 그 날짜를 못 채웠다.
        (None, None),
    ],
)
def test_unknown_days_read_as_none(row, expected):
    connection = FakeConnection(row)

    assert market_open_day(connection, "KRX", date(2026, 8, 12)) is expected


def test_each_market_asks_with_its_own_code():
    krx = FakeConnection((True,))
    us = FakeConnection((True,))

    krx_open_day(krx, date(2026, 8, 12))
    us_equity_open_day(us, date(2026, 8, 12))

    assert krx.recorded_cursor.calls[0][1] == ("KRX", date(2026, 8, 12))
    assert us.recorded_cursor.calls[0][1] == ("US_EQUITY", date(2026, 8, 12))
