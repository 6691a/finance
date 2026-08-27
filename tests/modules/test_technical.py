"""기술지표 계산기 계약 테스트. 설계는 docs/analysis/market-technical-indicators.md 5절·12.1절이다."""

from datetime import date, timedelta

import pytest

from modules.technical.indicators import (
    RSI_OVERBOUGHT,
    RSI_OVERSOLD,
    RULE_VERSION,
    TECHNICAL_LOOKBACK_BARS,
    TECHNICAL_MIN_BARS,
    DailyBar,
    SignalKind,
    detect_signals,
    summarize,
)

START = date(2026, 1, 5)


def business_days(count: int) -> list[date]:
    """주말을 건너뛴 거래일 목록. 달력을 흉내 낼 뿐 실제 휴장일은 모른다."""
    days: list[date] = []
    cursor = START
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def bars_from(closes: list[float], volumes: list[int | None] | None = None) -> list[DailyBar]:
    dates = business_days(len(closes))
    return [
        DailyBar(
            business_date=dates[i],
            open=closes[i],
            high=closes[i] * 1.01,
            low=closes[i] * 0.99,
            close=closes[i],
            volume=None if volumes is None else volumes[i],
        )
        for i in range(len(closes))
    ]


def linear_bars() -> list[DailyBar]:
    """5.3절 고정 벡터. 종가와 거래량이 모두 1..120이다."""
    values = list(range(1, TECHNICAL_LOOKBACK_BARS + 1))
    return bars_from([float(v) for v in values], list(values))


class TestSummarize:
    def test_the_fixed_vector_from_the_spec(self) -> None:
        snapshot = summarize("KOSPI", "코스피", linear_bars())
        assert snapshot is not None
        assert snapshot.subject_code == "KOSPI"
        assert snapshot.label == "코스피"
        assert snapshot.as_of_date == business_days(120)[-1]
        assert snapshot.close == 120.0
        assert snapshot.observations == 120
        assert snapshot.sma20 == pytest.approx(110.5)
        assert snapshot.sma60 == pytest.approx(90.5)
        assert snapshot.rsi14 == pytest.approx(100.0)
        assert snapshot.macd == pytest.approx(7.0)
        assert snapshot.macd_signal == pytest.approx(7.0)
        assert snapshot.macd_histogram == pytest.approx(0.0)
        assert snapshot.volume_ratio20 == pytest.approx(1.095890410958904, rel=1e-9, abs=1e-9)

    def test_flat_prices_give_a_neutral_rsi(self) -> None:
        snapshot = summarize("KOSPI", "코스피", bars_from([100.0] * 120))
        assert snapshot is not None
        assert snapshot.rsi14 == pytest.approx(50.0)
        assert snapshot.macd == pytest.approx(0.0)
        assert snapshot.sma20 == pytest.approx(100.0)

    def test_fewer_than_the_minimum_bars_makes_no_snapshot(self) -> None:
        closes = [float(v) for v in range(1, TECHNICAL_MIN_BARS)]
        assert summarize("KOSPI", "코스피", bars_from(closes)) is None

    def test_exactly_the_minimum_bars_makes_a_snapshot(self) -> None:
        closes = [float(v) for v in range(1, TECHNICAL_MIN_BARS + 1)]
        snapshot = summarize("KOSPI", "코스피", bars_from(closes))
        assert snapshot is not None
        assert snapshot.observations == TECHNICAL_MIN_BARS

    def test_missing_volume_only_drops_the_volume_ratio(self) -> None:
        snapshot = summarize("KOSPI", "코스피", bars_from([float(v) for v in range(1, 121)]))
        assert snapshot is not None
        assert snapshot.volume_ratio20 is None

    def test_one_missing_volume_in_the_window_drops_the_ratio(self) -> None:
        volumes: list[int | None] = list(range(1, 121))
        volumes[-3] = None  # 직전 20거래일 창 안의 결측 하나
        snapshot = summarize("KOSPI", "코스피", bars_from([float(v) for v in range(1, 121)], volumes))
        assert snapshot is not None
        assert snapshot.volume_ratio20 is None

    def test_a_zero_average_volume_drops_the_ratio(self) -> None:
        volumes: list[int | None] = [0] * 120
        snapshot = summarize("KOSPI", "코스피", bars_from([100.0] * 120, volumes))
        assert snapshot is not None
        assert snapshot.volume_ratio20 is None

    def test_out_of_order_dates_make_no_snapshot(self) -> None:
        bars = linear_bars()
        bars[10], bars[11] = bars[11], bars[10]
        assert summarize("KOSPI", "코스피", bars) is None

    def test_duplicate_dates_make_no_snapshot(self) -> None:
        bars = linear_bars()
        bars[11] = bars[10]
        assert summarize("KOSPI", "코스피", bars) is None

    def test_a_price_gap_beyond_the_guard_makes_no_snapshot(self) -> None:
        closes = [100.0] * 120
        closes[80] = 140.0  # 하루 +40%
        bars = bars_from(closes)
        assert summarize("KOSPI", "코스피", bars, max_abs_daily_change_pct=35.0) is None
        # guard를 끄면 계산한다
        assert summarize("KOSPI", "코스피", bars) is not None


def crossing_bars() -> list[DailyBar]:
    """내림 60봉 뒤 오름 60봉. SMA20이 SMA60을 아래에서 위로 뚫는다."""
    closes = [float(v) for v in range(120, 60, -1)] + [float(v) for v in range(61, 121)]
    return bars_from(closes, [1000] * 120)


def golden_cross_date(bars: list[DailyBar]) -> date:
    """검증용 재계산. 구현과 같은 정의(SMA20-SMA60 부호 음→양)를 손으로 센다."""
    closes = [bar.close for bar in bars]
    crossed: list[date] = []
    for i in range(len(bars)):
        if i + 1 < 60:
            continue
        sma20 = sum(closes[i - 19 : i + 1]) / 20
        sma60 = sum(closes[i - 59 : i + 1]) / 60
        previous20 = sum(closes[i - 20 : i]) / 20
        previous60 = sum(closes[i - 60 : i]) / 60
        if previous20 - previous60 < 0 and sma20 - sma60 > 0:
            crossed.append(bars[i].business_date)
    assert len(crossed) == 1
    return crossed[0]


class TestDetectSignals:
    def test_a_golden_cross_happens_once_on_the_computed_date(self) -> None:
        bars = crossing_bars()
        events = detect_signals(bars, scan_bars=120)
        crosses = [event for event in events if event.kind is SignalKind.SMA_CROSS]
        assert [event.direction for event in crosses] == ["up"]
        assert crosses[0].signal_date == golden_cross_date(bars)
        assert crosses[0].rule_version == RULE_VERSION
        assert crosses[0].close > 0
        assert crosses[0].volume_ratio20 is not None

    def test_a_monotonic_series_has_no_cross_events(self) -> None:
        events = detect_signals(linear_bars(), scan_bars=120)
        assert [event for event in events if event.kind is SignalKind.SMA_CROSS] == []
        assert [event for event in events if event.kind is SignalKind.MACD_CROSS] == []
        # RSI는 100에 붙어 70을 위에서 아래로 지나지 않는다
        assert [event for event in events if event.kind is SignalKind.RSI_REVERSAL] == []

    def test_a_macd_cross_is_detected(self) -> None:
        # 완만한 하락이 가속되면 히스토그램이 음수가 되고, 반등하면 위로 교차한다.
        # 등속 하락은 히스토그램이 정확히 0이라 교차가 아니다(12.1절의 0 규칙).
        closes = [float(v) for v in range(300, 220, -1)]
        closes += [closes[-1] - 3.0 * step for step in range(1, 21)]
        closes += [closes[-1] + 3.0 * step for step in range(1, 21)]
        events = detect_signals(bars_from(closes, [1000] * len(closes)), scan_bars=len(closes))
        directions = [event.direction for event in events if event.kind is SignalKind.MACD_CROSS]
        assert "up" in directions

    def test_an_rsi_reversal_out_of_oversold_is_detected(self) -> None:
        closes = [float(v) for v in range(200, 80, -1)]  # RSI가 30 아래로 내려간다
        closes += [closes[-1] + 2.0 * step for step in range(1, 11)]  # 다시 30 위로
        events = detect_signals(bars_from(closes, [1000] * len(closes)), scan_bars=len(closes))
        reversals = [event for event in events if event.kind is SignalKind.RSI_REVERSAL]
        assert [event.direction for event in reversals][:1] == ["up"]

    def test_an_rsi_reversal_out_of_overbought_is_detected(self) -> None:
        closes = [float(v) for v in range(80, 200)]  # RSI가 70 위로 올라간다
        closes += [closes[-1] - 2.0 * step for step in range(1, 11)]  # 다시 70 아래로
        events = detect_signals(bars_from(closes, [1000] * len(closes)), scan_bars=len(closes))
        reversals = [event for event in events if event.kind is SignalKind.RSI_REVERSAL]
        assert "down" in [event.direction for event in reversals]

    def test_scan_bars_limits_how_far_back_events_are_reported(self) -> None:
        bars = crossing_bars()
        cross_date = golden_cross_date(bars)
        later_bars = sum(1 for bar in bars if bar.business_date > cross_date)
        assert detect_signals(bars, scan_bars=later_bars) == []
        events = detect_signals(bars, scan_bars=later_bars + 1)
        assert [event.signal_date for event in events if event.kind is SignalKind.SMA_CROSS] == [cross_date]

    def test_too_few_bars_or_a_price_gap_gives_no_events(self) -> None:
        closes = [float(v) for v in range(1, TECHNICAL_MIN_BARS)]
        assert detect_signals(bars_from(closes), scan_bars=5) == []
        gap = [100.0] * 120
        gap[80] = 140.0
        assert detect_signals(bars_from(gap), scan_bars=120, max_abs_daily_change_pct=35.0) == []

    def test_the_thresholds_are_the_shared_constants(self) -> None:
        assert RSI_OVERSOLD == 30.0
        assert RSI_OVERBOUGHT == 70.0
        assert RULE_VERSION == "1"
