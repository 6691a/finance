"""신호 검출·저장 모듈. 계산 자체는 `test_technical.py`가 덮는다."""

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Self

import pytest
from sqlalchemy import Table

from apps.models.analysis import TechnicalSignal
from modules import technical_signals
from modules.technical import RULE_VERSION, SignalKind

AS_OF = datetime(2026, 8, 24, 9, 40, tzinfo=UTC)


class FakeCursor:
    def __init__(self, connection: "FakeConnection") -> None:
        self._connection = connection
        self._rows: list[tuple] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: str, parameters: Any = ()) -> None:
        recorded = dict(parameters) if isinstance(parameters, dict) else tuple(parameters)
        self._connection.calls.append((statement, recorded))
        self._rows = list(self._connection.rows)

    def executemany(self, statement: str, parameters) -> None:
        self._connection.calls.extend((statement, tuple(row)) for row in parameters)

    def fetchall(self) -> list[tuple]:
        return self._rows


class FakeConnection:
    def __init__(self, rows: list[tuple] | None = None) -> None:
        self.rows = rows or []
        self.calls: list[tuple[str, Any]] = []

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)


@pytest.fixture(autouse=True)
def without_the_psycopg2_fast_path(monkeypatch):
    """행 단위 파라미터가 그대로 보이는 PEP 249 경로에 고정한다(`test_kis.py`와 같은 이유)."""
    monkeypatch.setattr("modules.upsert._execute_batch", None)


def history_rows(symbol: str = "KOSPI", label: str = "코스피", kind: str = "index", count: int = 120) -> list[tuple]:
    """`technical/select_history.sql` 결과 모양. 최신순이고 SMA20이 SMA60을 위로 뚫는다."""
    closes = [3000.0 + value for value in range(120, 60, -1)] + [3000.0 + value for value in range(61, 121)]
    closes = closes[-count:]
    rows = []
    cursor = date(2026, 1, 5)
    made = 0
    while made < len(closes):
        if cursor.weekday() < 5:
            close = Decimal(str(closes[made]))
            rows.append(("kis", symbol, label, kind, "KR", cursor, close, close, close, close, 1000 + made))
            made += 1
        cursor += timedelta(days=1)
    return list(reversed(rows))


def upsert_calls(connection: FakeConnection) -> list[tuple]:
    return [parameters for statement, parameters in connection.calls if "INSERT INTO technical_signal" in statement]


def test_the_query_asks_for_the_indexes_and_the_watched_stocks():
    connection = FakeConnection(history_rows())

    technical_signals.detect_and_store(connection, as_of_at=AS_OF, scan_bars=5)

    statement, parameters = connection.calls[0]
    assert "WITH requested AS" in statement
    assert parameters["symbols"] == list(technical_signals.SIGNAL_INDEXES)
    assert parameters["include_watched"] is True
    assert parameters["as_of_at"] == AS_OF
    assert parameters["limit"] == technical_signals.TECHNICAL_LOOKBACK_BARS


def test_the_lookback_widens_only_when_asked():
    """`lookback_bars`가 조회 창을 정한다. 이력 백필만 이 값을 넓힌다.

    기본값이 일상 실행의 창과 같아야 옛 동작이 그대로 남는다
    (docs/analysis/market-thesis/10-base-rate.md 4.1절).
    """
    connection = FakeConnection(history_rows())

    technical_signals.detect_and_store(connection, as_of_at=AS_OF, scan_bars=5, lookback_bars=3000)

    _statement, parameters = connection.calls[0]
    assert parameters["limit"] == 3000


def test_events_are_stored_in_column_order():
    connection = FakeConnection(history_rows())

    result = technical_signals.detect_and_store(connection, as_of_at=AS_OF, scan_bars=120)

    calls = upsert_calls(connection)
    assert result.stored == len(calls) > 0
    provider, symbol, signal_date, kind, direction, close, sma20, sma60, rsi14, macd, signal, ratio, version = calls[0]
    assert (provider, symbol) == ("kis", "KOSPI")
    assert isinstance(signal_date, date)
    assert kind in {member.value for member in SignalKind}
    assert direction in {"up", "down"}
    assert close > 0
    assert version == RULE_VERSION
    assert ratio is None or ratio > 0
    assert (sma20, sma60, rsi14, macd, signal) == pytest.approx((sma20, sma60, rsi14, macd, signal))


def test_a_golden_cross_is_stored_for_the_index():
    connection = FakeConnection(history_rows())

    technical_signals.detect_and_store(connection, as_of_at=AS_OF, scan_bars=120)

    crosses = [call for call in upsert_calls(connection) if call[3] == SignalKind.SMA_CROSS.value]
    assert [call[4] for call in crosses] == ["up"]


def test_scan_bars_narrows_how_far_back_events_are_written():
    connection = FakeConnection(history_rows())

    technical_signals.detect_and_store(connection, as_of_at=AS_OF, scan_bars=1)

    assert upsert_calls(connection) == []


def test_every_subject_too_short_is_an_error_that_names_them():
    """조용한 성공을 만들지 않는다. 볼 대상이 하나도 없으면 태스크가 죽어야 한다."""
    connection = FakeConnection(history_rows(count=30))

    with pytest.raises(technical_signals.TechnicalSignalError, match="KOSPI"):
        technical_signals.detect_and_store(connection, as_of_at=AS_OF, scan_bars=5)


def test_no_bars_at_all_is_an_error():
    connection = FakeConnection([])

    with pytest.raises(technical_signals.TechnicalSignalError, match="No daily bars"):
        technical_signals.detect_and_store(connection, as_of_at=AS_OF, scan_bars=5)


def test_zero_events_is_a_normal_success():
    """교차가 없는 날은 정상이다. 볼 대상이 없는 것과 다르다."""
    connection = FakeConnection(history_rows())

    result = technical_signals.detect_and_store(connection, as_of_at=AS_OF, scan_bars=1)

    assert result.stored == 0
    assert result.subjects == ("KOSPI",)
    assert result.skipped == ()


def test_one_short_subject_does_not_stop_the_others():
    rows = history_rows() + history_rows("005930", "삼성전자", "equity", count=30)
    connection = FakeConnection(rows)

    result = technical_signals.detect_and_store(connection, as_of_at=AS_OF, scan_bars=120)

    assert result.skipped == ("005930",)
    assert result.stored > 0
    assert {call[1] for call in upsert_calls(connection)} == {"KOSPI"}


def _inserted_columns(statement: str) -> tuple[str, ...]:
    columns = re.search(r"INSERT INTO \w+ \(([^)]+)\)", statement, re.DOTALL)
    assert columns is not None
    names = re.sub(r"--[^\n]*", "", columns.group(1))
    return tuple(name.strip() for name in names.split(",") if name.strip())


def _required_columns(table: Table) -> set[str]:
    return {
        column.name
        for column in table.columns
        if not column.nullable and column.server_default is None and not column.primary_key
    }


def test_the_upsert_matches_the_model():
    statement = technical_signals.SIGNAL_UPSERT
    table = TechnicalSignal.__table__
    columns = _inserted_columns(statement)

    assert set(columns) <= {column.name for column in table.columns}
    assert _required_columns(table) <= set(columns)
    assert statement.count("%s") == len(columns)


def test_the_upsert_key_matches_the_natural_key():
    """멱등 키가 어긋나면 매일 같은 사건이 새 행으로 쌓인다."""
    assert "ON CONFLICT (provider, symbol, signal_date, kind) DO UPDATE" in technical_signals.SIGNAL_UPSERT
