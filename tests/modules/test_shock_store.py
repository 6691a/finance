"""쿨다운 판정과 SQL 파라미터. 가짜 연결을 쓴다."""

from datetime import UTC, datetime, timedelta

from modules.shock.domain import PEER_SPECS
from modules.shock.store import ShockStore, within_cooldown

DETECTED = datetime(2026, 9, 3, 5, 16, tzinfo=UTC)


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self.statements: list[tuple[str, object]] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, statement, parameters=()):
        self.statements.append((statement, parameters))

    def executemany(self, statement, parameters):
        self.statements.append((statement, parameters))

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class FakeConnection:
    def __init__(self, rows=()):
        self.cursors: list[FakeCursor] = []
        self._rows = list(rows)

    def cursor(self):
        cursor = FakeCursor(self._rows)
        self.cursors.append(cursor)
        return cursor


def test_no_previous_event_is_never_inside_the_cooldown():
    assert within_cooldown(DETECTED, None, 60) is False


def test_a_recent_event_is_the_same_shock():
    assert within_cooldown(DETECTED, DETECTED - timedelta(minutes=25), 60) is True


def test_the_cooldown_boundary_is_outside():
    """정확히 60분이면 새 사건이다. 쿨다운이 끝난 순간부터 받는다."""
    assert within_cooldown(DETECTED, DETECTED - timedelta(minutes=60), 60) is False


def test_peer_bars_keeps_a_key_for_a_market_with_no_bars():
    """봉이 0건인 시장도 키가 남는다.

    빠진 키와 빈 값을 부르는 쪽이 다르게 다루면 "못 봤다"가 조용히 사라진다.
    """
    connection = FakeConnection(
        [("NIKKEI225", datetime(2026, 9, 3, 5, 0, tzinfo=UTC), 1, 2, 3, 4)],
    )

    grouped = ShockStore(connection).peer_bars(
        (PEER_SPECS["NIKKEI225"], PEER_SPECS["TAIEX"]),
        window_start=DETECTED - timedelta(minutes=30),
        window_end=DETECTED,
    )

    assert set(grouped) == {"NIKKEI225", "TAIEX"}
    assert len(grouped["NIKKEI225"]) == 1
    assert grouped["TAIEX"] == []


def test_peer_bars_splits_symbols_across_the_two_bar_tables():
    """아시아 지수는 index_bar, 미국 선물은 index_future_bar에 있다. 한 왕복으로 둘을 본다."""
    connection = FakeConnection([])

    ShockStore(connection).peer_bars(
        (PEER_SPECS["NIKKEI225"], PEER_SPECS["SP500_FUT"]),
        window_start=DETECTED - timedelta(minutes=30),
        window_end=DETECTED,
    )

    statement, parameters = connection.cursors[0].statements[0]
    assert "index_bar" in statement
    assert "index_future_bar" in statement
    assert parameters["index_symbols"] == ["NIKKEI225"]
    assert parameters["future_symbols"] == ["SP500_FUT"]
    assert parameters["index_provider"] == "kis"
    assert parameters["future_provider"] == "yahoo"


def test_nth_open_day_asks_market_session_rather_than_counting_days():
    """날짜를 우리가 세지 않는다. 휴장일에서 어긋난다."""
    connection = FakeConnection([(DETECTED.date(),)])

    ShockStore(connection).nth_open_day(DETECTED.date(), 3)

    statement, parameters = connection.cursors[0].statements[0]
    assert "market_session" in statement
    assert "effective_open_day" in statement
    assert parameters == (DETECTED.date(), 3)
