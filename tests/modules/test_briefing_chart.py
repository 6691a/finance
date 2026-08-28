from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from modules.briefing import chart
from modules.briefing.market_data import ChartSeries, DailyChartSeries
from modules.technical import indicators

# KST 2026-08-18(화) 12:30.
MIDDAY = datetime(2026, 8, 18, 3, 30, tzinfo=UTC)


def series(symbol: str, label: str, *closes: str) -> ChartSeries:
    return ChartSeries(
        provider="kis",
        symbol=symbol,
        label=label,
        venue="KRX",
        points=tuple((MIDDAY + timedelta(minutes=index), Decimal(close)) for index, close in enumerate(closes)),
    )


def test_an_empty_series_is_an_error():
    """빈 차트를 조용히 올리면 안 된다. 생략 판단은 부르는 쪽이 한다."""
    with pytest.raises(chart.ChartError):
        chart.render_series_png(series("KOSPI", "코스피"))


def test_renders_one_png_per_series():
    pytest.importorskip("matplotlib")
    png = chart.render_series_png(series("005930", "삼성전자", "268000", "267000", "267500"))

    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def daily_series(count: int, kind: str = "equity") -> DailyChartSeries:
    """오르내리는 종가 `count`개. 이동평균이 갈리도록 방향을 한 번 바꾼다."""
    bars = tuple(
        indicators.DailyBar(
            business_date=date(2026, 1, 1) + timedelta(days=index),
            open=70000.0 + index,
            high=70000.0 + index,
            low=70000.0 + index,
            close=70000.0 + (index if index < count // 2 else count - index),
        )
        for index in range(count)
    )
    return DailyChartSeries(subject_code="005930", label="삼성전자", kind=kind, venue="KRX", bars=bars)


def test_too_few_bars_is_an_error():
    """지표를 못 내는 계열을 빈 차트로 올리지 않는다. 생략 판단은 부르는 쪽이 한다."""
    with pytest.raises(chart.ChartError):
        chart.render_daily_png(daily_series(indicators.TECHNICAL_MIN_BARS - 1))


def test_renders_a_daily_indicator_png():
    pytest.importorskip("matplotlib")
    png = chart.render_daily_png(daily_series(indicators.TECHNICAL_LOOKBACK_BARS))

    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_only_stocks_and_indexes_get_candles_and_indicator_panels():
    """환율은 종가 선 하나다. 봉도 RSI·MACD도 붙이지 않고 이동평균선만 얹는다."""
    assert chart.draws_candles("equity")
    assert chart.draws_candles("index")
    assert not chart.draws_candles("fx")


def test_a_fx_series_renders_a_shorter_png():
    """단이 하나뿐이라 세로가 짧다. 같은 함수가 두 모양을 그린다."""
    pytest.importorskip("matplotlib")
    stock = chart.render_daily_png(daily_series(indicators.TECHNICAL_LOOKBACK_BARS))
    fx = chart.render_daily_png(daily_series(indicators.TECHNICAL_LOOKBACK_BARS, kind="fx"))

    assert fx[:8] == b"\x89PNG\r\n\x1a\n"
    assert _png_height(fx) < _png_height(stock)


def _png_height(png: bytes) -> int:
    """IHDR 청크의 세로 픽셀. 표준 라이브러리만으로 읽는다."""
    return int.from_bytes(png[20:24], "big")


def test_the_bar_interval_is_read_from_the_bars():
    """봉 간격은 상수가 아니라 봉에서 읽는다. 수집이 5분으로 바뀌면 제목도 따라간다."""
    minute = [MIDDAY + timedelta(minutes=index) for index in range(5)]
    five = [MIDDAY + timedelta(minutes=5 * index) for index in range(5)]
    # 점심 공백이 끼어도 가장 짧은 사이가 봉 간격이다.
    with_gap = [MIDDAY, MIDDAY + timedelta(minutes=1), MIDDAY + timedelta(minutes=90)]

    assert chart.interval_minutes(minute) == 1
    assert chart.interval_minutes(five) == 5
    assert chart.interval_minutes(with_gap) == 1
    assert chart.interval_minutes([MIDDAY]) == 0


# MACD 히스토그램의 값 없음. 창 앞쪽은 MACD가 아직 안 서서 `None`인데, 0으로 그리면
# 실제 0(MACD와 시그널이 만난 날)과 같은 막대가 되고 상승색까지 붙는다.


def test_a_missing_histogram_value_is_not_drawn_as_a_zero_bar():
    heights, _ = chart.histogram_bars([None, Decimal(0), Decimal("-1.5")])

    assert heights[0] != heights[1]
    assert heights[0] != heights[0]  # NaN. matplotlib이 막대를 안 그린다
    assert heights[1] == 0.0


def test_only_a_real_negative_value_gets_the_fall_color():
    _, colors = chart.histogram_bars([None, Decimal(0), Decimal("-1.5")])

    assert colors[2] == chart.FALL_COLOR
    assert colors[0] == chart.RISE_COLOR
    assert colors[1] == chart.RISE_COLOR
