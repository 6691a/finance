"""두 추론 DAG가 함께 쓰는 관측 상태.

기술적 관측 블록의 계약은 docs/analysis/market-technical-indicators.md 14.1절이다.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Self

import pytest

from modules import base_rate, thesis_common
from modules.technical import TECHNICAL_LOOKBACK_BARS
from modules.thesis_domain import RSI_OVERBOUGHT, RSI_OVERSOLD, ThesisSubjectKind
from modules.thesis_generation import SYSTEM_PROMPT
from modules.thesis_state import (
    IndexObservation,
    ObservedState,
    SignalObservation,
    TechnicalObservation,
    TechnicalState,
)

SESSION = date(2026, 8, 21)
AS_OF = datetime(2026, 8, 21, 6, 30, tzinfo=UTC)  # KST 15:30



# 기저율 조회 둘. 관측 상태를 만들 때마다 불린다.
BASE_RATE_QUERIES = frozenset({base_rate.FORWARD_RETURNS, base_rate.UNCONDITIONAL_RETURNS})

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
        rows = list(self._connection.results.get(_key(statement), []))
        # 신호 조회는 대상마다 따로 불린다. 실제 SQL이 걸러 주는 것을 여기서 흉내 낸다.
        if _key(statement) == "signals" and isinstance(recorded, dict):
            rows = [row for row in rows if row[1] == recorded.get("symbol")]
        self._rows = rows

    def fetchall(self) -> list[tuple]:
        return self._rows

    def fetchone(self) -> tuple | None:
        return self._rows[0] if self._rows else None


class FakeConnection:
    def __init__(self, results: dict[str, list[tuple]] | None = None) -> None:
        self.results = results or {}
        self.calls: list[tuple[str, Any]] = []

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)


def _key(statement: str) -> str:
    # 기저율 조회가 먼저다. 둘 다 `FROM technical_signal`·`FROM stock_investor_trade_daily`를
    # 담고 있어 아래 규칙에 걸리면 모양이 다른 행을 받는다.
    if statement in BASE_RATE_QUERIES:
        return "base_rate"
    if "WITH requested AS" in statement:
        return "technical"
    if "FROM technical_signal" in statement:
        return "signals"
    if "FROM index_bar" in statement:
        return "index"
    if "FROM stock_investor_trade_daily" in statement:
        return "stock"
    if "FROM market_session" in statement:
        return "session"
    return "other"


class Subject:
    def __init__(self, code: str, kind) -> None:
        self.code = code
        self.kind = kind


TARGETS = (
    Subject("KOSPI", ThesisSubjectKind.INDEX),
    Subject("005930", ThesisSubjectKind.STOCK),
)


def history_rows(symbol: str, label: str, kind: str, base: float, count: int = 120) -> list[tuple]:
    """`technical/select_history.sql` 결과 모양. 최신순이고 종가가 올라간다."""
    rows = []
    cursor = date(2026, 1, 5)
    made = 0
    while made < count:
        if cursor.weekday() < 5:
            close = Decimal(str(base + made + 1))
            rows.append(("kis", symbol, label, kind, "KR", cursor, close, close, close, close, 1000 + made))
            made += 1
        cursor += timedelta(days=1)
    return list(reversed(rows))


def both_subjects(count: int = 120) -> list[tuple]:
    return history_rows("KOSPI", "코스피", "index", 3000.0, count) + history_rows(
        "005930", "삼성전자", "equity", 70_000.0, count
    )


def state(connection: FakeConnection) -> ObservedState:
    run = thesis_common.ThesisRun(connection, run_date=SESSION, as_of_at=AS_OF)
    return run.observed_state(SESSION, TARGETS)


def test_the_state_is_a_model_not_a_bare_dict():
    """프롬프트와 JSONB 둘로 나가는 값이라 키 오타가 조용히 살아남으면 안 된다."""
    result = state(
        FakeConnection(
            {
                "index": [("KOSPI", Decimal(3150), Decimal(3125))],
                "stock": [("005930", Decimal(71500))],
                "technical": both_subjects(),
                "signals": [],
            }
        )
    )

    assert isinstance(result, ObservedState)
    assert isinstance(result.technical, TechnicalState)
    assert isinstance(result.index["KOSPI"], IndexObservation)
    # JSON 경계에서만 dict가 된다.
    payload = result.model_dump(mode="json")
    assert payload["session"] == "2026-08-21"
    assert set(payload) == {"session", "index", "stock", "intraday", "technical"}
    # 장전·장후는 `intraday`를 안 채운다. 장중 슬롯만 쓰는 칸이고 둘은 배타적이다.
    assert payload["intraday"] == {}
    assert set(payload["technical"]) == {"as_of_date", "subjects"}


def test_the_technical_block_carries_ratios_not_raw_averages():
    """모델이 "종가가 SMA20 위인가"를 계산하지 않고 읽게 한다."""
    technical = state(FakeConnection({"technical": both_subjects(), "signals": []})).technical

    assert technical.as_of_date == date(2026, 6, 19)
    kospi = technical.subjects["KOSPI"]
    assert isinstance(kospi, TechnicalObservation)
    # 오르는 계열이라 종가는 SMA20 위, SMA20은 SMA60 위다.
    assert kospi.close_vs_sma20_pct > 0
    assert kospi.sma20_vs_sma60_pct > 0
    assert 0 <= kospi.rsi14 <= 100
    # 절대값은 담지 않는다 — 필요하면 `daily_history` 툴이 있다.
    assert "sma20" not in kospi.model_dump()


def test_the_block_covers_every_target():
    technical = state(FakeConnection({"technical": both_subjects(), "signals": []})).technical

    assert set(technical.subjects) == {"KOSPI", "005930"}


def test_a_short_sample_is_null_not_a_neutral_reading():
    """키를 빼거나 0으로 채우면 모델이 "지표가 중립"으로 읽는다."""
    technical = state(FakeConnection({"technical": both_subjects(30), "signals": []})).technical

    assert technical.subjects == {"KOSPI": None, "005930": None}
    assert technical.as_of_date is None


def test_a_target_without_bars_is_null():
    rows = history_rows("KOSPI", "코스피", "index", 3000.0)
    technical = state(FakeConnection({"technical": rows, "signals": []})).technical

    assert technical.subjects["005930"] is None
    assert technical.subjects["KOSPI"] is not None


def test_recent_signals_come_with_a_ref_so_they_can_be_cited():
    signals = [(1042, "KOSPI", date(2026, 6, 17), "sma_cross", "up")]
    technical = state(FakeConnection({"technical": both_subjects(), "signals": signals})).technical

    kospi = technical.subjects["KOSPI"]
    assert kospi is not None
    assert kospi.recent_signals == (
        SignalObservation(ref="technical_signal:1042", signal_date=date(2026, 6, 17), kind="sma_cross", direction="up"),
    )
    samsung = technical.subjects["005930"]
    assert samsung is not None
    assert samsung.recent_signals == ()


def test_the_queries_end_at_the_slot_time():
    """`now()`를 보면 장전 슬롯 재실행이 장중 데이터를 끌어온다."""
    connection = FakeConnection({"technical": both_subjects(), "signals": []})
    state(connection)

    for statement, parameters in connection.calls:
        if _key(statement) in {"technical", "signals"}:
            assert parameters["as_of_at"] == AS_OF
            assert "as_of_at" in statement


def test_the_technical_query_asks_only_for_the_targets():
    """관측 상태는 추론 대상만 본다. watched 목록을 다시 끌어오지 않는다."""
    connection = FakeConnection({"technical": both_subjects(), "signals": []})
    state(connection)

    parameters = next(parameters for statement, parameters in connection.calls if _key(statement) == "technical")
    assert sorted(parameters["symbols"]) == ["005930", "KOSPI"]
    assert parameters["include_watched"] is False
    assert parameters["limit"] == TECHNICAL_LOOKBACK_BARS


def test_no_session_gives_an_empty_state():
    """휴장·미판정이면 관측 상태 자체가 비어 있다."""
    run = thesis_common.ThesisRun(FakeConnection(), run_date=SESSION, as_of_at=AS_OF)
    result = run.observed_state(None, TARGETS)

    assert result == ObservedState()
    assert result.session is None
    assert result.technical.subjects == {}


@pytest.mark.parametrize("threshold", [RSI_OVERBOUGHT, RSI_OVERSOLD])
def test_the_prompt_carries_the_shared_thresholds(threshold):
    """상수를 고치면 프롬프트가 따라간다. 두 곳에 숫자를 적으면 반드시 어긋난다."""
    assert str(int(threshold)) in SYSTEM_PROMPT


def test_the_prompt_explains_how_to_read_the_block():
    prompt = SYSTEM_PROMPT

    assert "technical" in prompt
    assert "as_of_date" in prompt
    assert "recent_signals" in prompt
    # 사건이지 판정이 아니라는 것을 프롬프트가 직접 말한다.
    assert "사건" in prompt


# --- ThesisRun ----------------------------------------------------------------


def test_the_previous_open_day_is_read_once():
    """장전이 이 값을 매크로 창의 시작과 관측 세션 둘에 쓴다.

    두 번 조회하면 그 사이 `market_calendar_daily`가 행을 넣어 두 답이 갈릴 수 있다.
    창은 어제 마감부터인데 관측은 오늘 세션을 보는 상태가 그대로 프롬프트에 실린다.
    """
    connection = FakeConnection({"session": [(date(2026, 8, 20),)]})
    run = thesis_common.ThesisRun(connection, run_date=SESSION, as_of_at=AS_OF)

    assert run.previous_open_day() == run.previous_open_day() == date(2026, 8, 20)
    assert len([call for call in connection.calls if _key(call[0]) == "session"]) == 1


def test_an_unfilled_calendar_is_remembered_as_none():
    """달력이 아직 없는 것도 답이다. 두 번째 호출이 다시 조회하지 않는다."""
    connection = FakeConnection()
    run = thesis_common.ThesisRun(connection, run_date=SESSION, as_of_at=AS_OF)

    assert run.previous_open_day() is None
    assert run.previous_open_day() is None
    assert len([call for call in connection.calls if _key(call[0]) == "session"]) == 1
