from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from modules.briefing import chart
from modules.briefing.market import ChartSeries

# KST 2026-08-18(화) 12:30.
MIDDAY = datetime(2026, 8, 18, 3, 30, tzinfo=UTC)


def series(symbol: str, label: str, *closes: str) -> ChartSeries:
    return ChartSeries(
        provider="kis",
        symbol=symbol,
        label=label,
        points=tuple(
            (MIDDAY + timedelta(minutes=index), Decimal(close)) for index, close in enumerate(closes)
        ),
    )


def test_no_series_is_an_error():
    """빈 차트를 조용히 올리면 안 된다. 생략 판단은 부르는 쪽이 한다."""
    with pytest.raises(chart.ChartError):
        chart.render_chart_png((), MIDDAY)


def test_renders_a_png_with_an_odd_subplot_count():
    """계열 셋이면 2×2 그리드의 마지막 칸은 비워야 한다. 홀수 개수가 죽으면 안 된다."""
    pytest.importorskip("matplotlib")
    png = chart.render_chart_png(
        (
            series("KOSPI", "코스피", "2685.10", "2687.45"),
            series("005930", "삼성전자", "268000", "267000"),
            series("000660", "SK하이닉스", "1520000", "1524000"),
        ),
        MIDDAY,
    )

    assert png[:8] == b"\x89PNG\r\n\x1a\n"
