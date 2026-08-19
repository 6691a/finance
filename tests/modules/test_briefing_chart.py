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


def test_an_empty_series_is_an_error():
    """빈 차트를 조용히 올리면 안 된다. 생략 판단은 부르는 쪽이 한다."""
    with pytest.raises(chart.ChartError):
        chart.render_series_png(series("KOSPI", "코스피"))


def test_renders_one_png_per_series():
    pytest.importorskip("matplotlib")
    png = chart.render_series_png(series("005930", "삼성전자", "268000", "267000", "267500"))

    assert png[:8] == b"\x89PNG\r\n\x1a\n"
