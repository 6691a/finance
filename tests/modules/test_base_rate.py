"""조건부 기저율. SQL은 가짜 연결로 대신하고 **버킷팅과 경계값**을 본다.

버킷팅이 SQL이 아니라 파이썬에 있는 이유가 이 테스트다 — 채점과 같은 임계를 쓰는지,
경계값이 같은 쪽으로 떨어지는지를 DB 없이 확인한다.
"""

from datetime import date
from decimal import Decimal
from typing import Any, Self

import pytest

from modules.technical import base_rate
from modules.thesis_domain import FLAT_THRESHOLD_PCT

AS_OF = date(2026, 8, 26)
SYMBOLS = ("KOSPI", "005930")


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
        # 어느 쿼리인지는 주석 첫 줄로 가른다. 두 SQL이 파라미터 모양이 달라서다.
        if "무조건 기저" in statement:
            self._rows = list(self._connection.unconditional)
        else:
            self._rows = list(self._connection.conditional)

    def fetchall(self) -> list[tuple]:
        return self._rows


class FakeConnection:
    def __init__(self, conditional: list[tuple], unconditional: list[tuple]) -> None:
        self.conditional = conditional
        self.unconditional = unconditional
        self.calls: list[tuple[str, Any]] = []

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)


def conditional_rows(
    returns: list[float],
    *,
    symbol: str = "KOSPI",
    kind: str = "sma_cross",
    direction: str = "up",
    horizon: int = 1,
) -> list[tuple]:
    """`select_forward_returns.sql` 결과 모양."""
    return [
        (symbol, kind, direction, date(2026, 1, 5), horizon, Decimal(str(value))) for value in returns
    ]


def unconditional_rows(returns: list[float], *, symbol: str = "KOSPI", horizon: int = 1) -> list[tuple]:
    """`select_unconditional_returns.sql` 결과 모양."""
    return [(symbol, horizon, Decimal(str(value))) for value in returns]


def test_counts_split_by_the_same_threshold_as_grading():
    """분류가 `classify_outcome`과 같은 임계를 쓴다. 지평 1의 임계는 0.3이다."""
    threshold = float(FLAT_THRESHOLD_PCT[1])
    assert threshold == 0.3

    # up 10건, flat 5건, down 5건. 경계값 0.30은 방향 쪽, 0.29는 flat 쪽이다.
    returns = [0.30] * 10 + [0.29, -0.29, 0.0, 0.1, -0.1] + [-0.30] * 5
    connection = FakeConnection(conditional_rows(returns), unconditional_rows(returns))

    rates = base_rate.signal_base_rates(connection, as_of_date=AS_OF, symbols=SYMBOLS, horizons=(1,))

    horizon = rates[("KOSPI", "sma_cross", "up")].conditional[0]
    assert horizon.sample_size == 20
    assert horizon.up == 0.5
    assert horizon.flat == 0.25
    assert horizon.down == 0.25
    assert horizon.up + horizon.flat + horizon.down == pytest.approx(1.0)


def test_thresholds_widen_with_the_horizon():
    """같은 등락률이라도 지평 5에서는 flat이다. 임계가 0.3이 아니라 0.7이라서다."""
    returns = [0.5] * 20
    connection = FakeConnection(
        conditional_rows(returns, horizon=5),
        unconditional_rows(returns, horizon=5),
    )

    rates = base_rate.signal_base_rates(connection, as_of_date=AS_OF, symbols=SYMBOLS, horizons=(5,))

    horizon = rates[("KOSPI", "sma_cross", "up")].conditional[0]
    assert horizon.flat == 1.0
    assert horizon.up == 0.0


def test_a_thin_sample_reports_the_count_without_ratios():
    """표본이 모자라면 비율이 전부 `None`이다. 0으로 채우지 않는다."""
    returns = [1.0] * (base_rate.MIN_BASE_RATE_SAMPLE - 1)
    connection = FakeConnection(conditional_rows(returns), unconditional_rows(returns))

    rates = base_rate.signal_base_rates(connection, as_of_date=AS_OF, symbols=SYMBOLS, horizons=(1,))

    horizon = rates[("KOSPI", "sma_cross", "up")].conditional[0]
    assert horizon.sample_size == base_rate.MIN_BASE_RATE_SAMPLE - 1
    assert horizon.up is None
    assert horizon.flat is None
    assert horizon.down is None
    assert horizon.median_return_pct is None


def test_the_unconditional_baseline_rides_along():
    """조건부만 주면 거짓말이 된다. 같은 심볼의 무조건 분포가 같은 객체에 있어야 한다."""
    connection = FakeConnection(
        conditional_rows([1.0] * 20),
        unconditional_rows([-1.0] * 20),
    )

    rates = base_rate.signal_base_rates(connection, as_of_date=AS_OF, symbols=SYMBOLS, horizons=(1,))

    rate = rates[("KOSPI", "sma_cross", "up")]
    assert rate.conditional[0].up == 1.0
    assert rate.unconditional[0].down == 1.0


def test_symbols_outside_the_request_are_dropped():
    """추론 대상이 아닌 심볼의 사건은 프롬프트에 실릴 자리가 없다."""
    connection = FakeConnection(
        conditional_rows([1.0] * 20, symbol="NIKKEI225"),
        unconditional_rows([1.0] * 20),
    )

    rates = base_rate.signal_base_rates(connection, as_of_date=AS_OF, symbols=SYMBOLS, horizons=(1,))

    assert rates == {}


def test_horizons_and_the_rule_version_reach_the_query():
    """지평과 `rule_version`을 SQL에 그대로 넘긴다. 규칙이 바뀌면 옛 사건은 다른 사건이다."""
    connection = FakeConnection(conditional_rows([1.0] * 20), unconditional_rows([1.0] * 20))

    base_rate.signal_base_rates(connection, as_of_date=AS_OF, symbols=SYMBOLS, horizons=(1, 3))

    forward = next(params for statement, params in connection.calls if "무조건 기저" not in statement)
    assert forward["horizons"] == [1, 3]
    assert forward["as_of_date"] == AS_OF
    assert forward["rule_version"]

    unconditional = next(params for statement, params in connection.calls if "무조건 기저" in statement)
    assert unconditional["symbols"] == list(SYMBOLS)


def test_no_symbols_means_no_query():
    """대상이 없으면 조회하지 않는다. 빈 배열로 전 종목을 훑는 사고를 막는다."""
    connection = FakeConnection([], [])

    assert base_rate.signal_base_rates(connection, as_of_date=AS_OF, symbols=()) == {}
    assert connection.calls == []


def test_the_baseline_horizons_skip_the_prediction_day():
    """T+0은 없다. 신호가 그날 종가로 검출되므로 등락률이 정의상 0이다."""
    assert base_rate.BASE_RATE_HORIZON_DAYS == (1, 3, 5)
    assert 0 not in base_rate.BASE_RATE_HORIZON_DAYS


# ---------------------------------------------------------------------------
# `flat` 기준선 — 프롬프트가 상수로 들고 있던 값
# ---------------------------------------------------------------------------


def test_the_flat_baseline_reads_only_the_recent_window():
    """전 이력이 아니라 최근 `FLAT_BASE_RATE_BARS`봉이다.

    신호 기저율과 창이 다른 이유는 재는 대상이 다르기 때문이다 — 신호는 "그 사건이 평소보다
    나았나"라 사건과 같은 기간과 견줘야 하고, 이 값은 "앞으로 얼마나 자주 일어나나"라 지금
    체제를 재야 한다. 코스피 flat 비율이 2016년 45퍼센트에서 2026년 6퍼센트로 단조 감소한
    것이 그 차이의 근거다(2026-08-26 실측).
    """
    # 오래된 구간은 전부 flat, 최근 창은 전부 up. 창을 안 자르면 flat이 섞인다.
    old = [0.0] * 500
    recent = [1.0] * base_rate.FLAT_BASE_RATE_BARS
    connection = FakeConnection([], unconditional_rows(old + recent))

    rates = base_rate.flat_base_rates(connection, as_of_date=AS_OF, symbols=("KOSPI",))

    assert rates["KOSPI"].sample_size == base_rate.FLAT_BASE_RATE_BARS
    assert rates["KOSPI"].up == 1.0
    assert rates["KOSPI"].flat == 0.0


def test_the_flat_baseline_is_per_symbol():
    """상수는 지수 둘·종목 전체를 세 값으로 묶었다. 종목끼리도 1.5배 달랐다."""
    connection = FakeConnection(
        [],
        unconditional_rows([0.0] * 40, symbol="KOSPI") + unconditional_rows([1.0] * 40, symbol="005930"),
    )

    rates = base_rate.flat_base_rates(connection, as_of_date=AS_OF, symbols=("KOSPI", "005930"))

    assert rates["KOSPI"].flat == 1.0
    assert rates["005930"].flat == 0.0


def test_a_symbol_without_enough_bars_gets_no_ratios():
    """표본이 모자라면 비율이 `None`이다. 프롬프트가 그때만 다르게 읽으라고 적어 뒀다."""
    connection = FakeConnection([], unconditional_rows([1.0] * (base_rate.MIN_BASE_RATE_SAMPLE - 1)))

    rates = base_rate.flat_base_rates(connection, as_of_date=AS_OF, symbols=("KOSPI",))

    assert rates["KOSPI"].flat is None
    assert rates["KOSPI"].sample_size == base_rate.MIN_BASE_RATE_SAMPLE - 1


def test_the_flat_baseline_is_the_one_day_horizon():
    """세 확률의 채점 창이 예측일 세션 하나라 그것과 같은 축이어야 한다."""
    connection = FakeConnection([], unconditional_rows([1.0] * 40))

    base_rate.flat_base_rates(connection, as_of_date=AS_OF, symbols=("KOSPI",))

    params = next(p for statement, p in connection.calls if "무조건 기저" in statement)
    assert params["horizons"] == [1]


def test_the_signal_baseline_still_uses_the_whole_history():
    """창을 자르는 것은 `flat` 기준선뿐이다. 신호 비교는 사건과 같은 기간을 봐야 한다."""
    returns = [1.0] * (base_rate.FLAT_BASE_RATE_BARS + 100)
    connection = FakeConnection(conditional_rows([1.0] * 20), unconditional_rows(returns))

    rates = base_rate.signal_base_rates(connection, as_of_date=AS_OF, symbols=SYMBOLS, horizons=(1,))

    assert rates[("KOSPI", "sma_cross", "up")].unconditional[0].sample_size == len(returns)

