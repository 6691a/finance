import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Self

import pytest
from sqlalchemy import Table

from apps.models.finance import ExchangeRate
from apps.models.market import (
    IndicatorObservation,
    MarketInvestorFlowSnapshot,
    MarketMovementSnapshot,
    QuoteBar,
)
from apps.models.reference import IndicatorSeries, QuoteSymbol
from modules.briefing import market
from modules.briefing.market import MarketScope

# KST 2026-08-18(화) 08:00. 이때 뉴욕은 아직 8월 17일(월) 저녁이다.
MORNING = datetime(2026, 8, 17, 23, 0, tzinfo=UTC)
# KST 2026-08-18(화) 12:30. 국내 정규장 한가운데.
MIDDAY = datetime(2026, 8, 18, 3, 30, tzinfo=UTC)

QUOTE_ROWS = [
    ("kis", "KOSPI", "코스피", "index", "KR", Decimal("2687.45"), Decimal("2665.60"), MIDDAY),
    ("kis", "KOSPI200_FUT", "코스피200 선물", "index_future", "KR", Decimal("361.20"), Decimal("358.80"), MIDDAY),
    ("yahoo", "SP500_FUT", "S&P500 선물", "index_future", "US", Decimal("5621.50"), Decimal("5600.25"), MIDDAY),
    ("yahoo", "SOX", "필라델피아 반도체 지수", "index", "US", Decimal("5310.00"), Decimal("5200.00"), MIDDAY),
    ("yahoo", "NIKKEI225", "닛케이225", "index", "JP", Decimal(38000), Decimal(38100), MIDDAY),
]

FX_ROWS = [
    ("USD", date(2026, 8, 18), 12, Decimal("1388.60"), Decimal("1392.90")),
    ("JPY", date(2026, 8, 18), 12, Decimal("941.20"), None),
]

RATE_ROWS = [
    ("fred", "DGS10", "US", "미국", "미국 10년물", date(2026, 8, 17), Decimal("4.21"), Decimal("4.18")),
    ("ecos", "KTB10Y", "KR", "한국", "국고채 10년", date(2026, 8, 17), Decimal("3.05"), Decimal("3.09")),
]

FLOW_ROWS = [("KOSPI", MIDDAY, Decimal(-152300000000), Decimal(88400000000), Decimal(61200000000))]

MOVEMENT_ROWS = [("KOSPI", MIDDAY, 512, 61, 341)]


class FakeCursor:
    def __init__(self, results: list[list[tuple]]) -> None:
        self.results = results
        self.calls: list[tuple[str, tuple]] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, statement: str, parameters: tuple = ()) -> None:
        self.calls.append((statement, parameters))

    def fetchall(self) -> list[tuple]:
        return self.results.pop(0)


class FakeConnection:
    def __init__(self, *results: list[tuple]) -> None:
        self.cursors: list[FakeCursor] = []
        self.results = list(results)

    def cursor(self) -> FakeCursor:
        cursor = FakeCursor(self.results)
        self.cursors.append(cursor)
        return cursor


def summary(now: datetime = MIDDAY):
    connection = FakeConnection(QUOTE_ROWS, FX_ROWS, RATE_ROWS, FLOW_ROWS, MOVEMENT_ROWS)
    return market.collect_summary(connection, now)


def test_change_is_computed_from_the_stored_previous_close():
    quotes = {quote.symbol: quote for quote in summary().quotes}

    assert quotes["KOSPI"].change_percent == pytest.approx(0.82, abs=0.01)
    assert quotes["NIKKEI225"].change_percent < 0


def test_korea_report_shows_domestic_quotes_and_us_futures_only():
    text = _block_text(market.render_blocks(summary(), MarketScope.KOREA, "요약"))

    assert "코스피" in text
    assert "S&P500 선물" in text
    # 미국 현물 지수는 한국장 시간에 멈춰 있다. 선물만 실시간이다.
    assert "필라델피아 반도체 지수" not in text
    # 금리는 미국장 아침 리포트가 그린다.
    assert "미국 10년물" not in text


def test_us_report_shows_overseas_rates_and_the_korean_recap():
    text = _block_text(market.render_blocks(summary(MORNING), MarketScope.US, "요약"))

    assert "필라델피아 반도체 지수" in text
    assert "미국 10년물" in text
    # 조합 평가를 읽는 사람이 같은 화면에서 전일 한국장을 봐야 한다.
    assert "코스피" in text


def test_us_comment_reads_both_markets():
    """미국장 리포트의 요약은 밤사이 미국 값과 전일 한국 값을 함께 본다."""
    payload = json.loads(market.comment_input(summary(MORNING), MarketScope.US))

    countries = {quote["country"] for quote in payload["quotes"]}
    assert {"KR", "US"} <= countries
    assert payload["rates"]


def test_korea_comment_leaves_out_the_rate_section():
    payload = json.loads(market.comment_input(summary(), MarketScope.KOREA))

    assert "rates" not in payload


def test_missing_comment_drops_its_block():
    with_comment = market.render_blocks(summary(), MarketScope.KOREA, "요약")
    without = market.render_blocks(summary(), MarketScope.KOREA, None)

    assert "요약" in _block_text(with_comment)
    assert len(without) == len(with_comment) - 2  # divider + section


def test_us_session_date_follows_new_york_not_seoul():
    """KST 화요일 아침에 보는 미국 세션은 뉴욕 월요일이다."""
    assert market.us_session_date(MORNING) == date(2026, 8, 17)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 8, 18, 3, 30, tzinfo=UTC), "장중"),  # KST 12:30
        (datetime(2026, 8, 17, 23, 30, tzinfo=UTC), "개장 전"),  # KST 08:30
        (datetime(2026, 8, 18, 7, 30, tzinfo=UTC), "마감 후"),  # KST 16:30
    ],
)
def test_session_state_uses_the_korean_clock(now, expected):
    assert market.session_state(now) == expected


def test_header_is_written_in_korean_time():
    blocks = market.render_blocks(summary(), MarketScope.KOREA, None)

    header = blocks[0]["text"]["text"]
    assert "12:30" in header
    # 요일은 표에서 온다. strftime("%a")는 컨테이너 로케일을 타서 조용히 영어가 된다.
    assert "08/18(화)" in header


def test_fallback_text_is_one_line():
    text = market.render_text(summary(), MarketScope.KOREA)

    assert "\n" not in text
    assert "코스피" in text


@pytest.mark.parametrize(
    ("statement", "table", "columns"),
    [
        (market.LATEST_QUOTES, QuoteBar.__table__, ("provider", "symbol", "close", "previous_close", "bar_at")),
        (market.LATEST_QUOTES, QuoteSymbol.__table__, ("label", "kind", "country")),
        (market.LATEST_EXCHANGE_RATES, ExchangeRate.__table__, ("currency", "round", "exchange_standard_rate")),
        (
            market.LATEST_RATES,
            IndicatorObservation.__table__,
            ("provider", "series_id", "observation_date", "value"),
        ),
        (market.LATEST_RATES, IndicatorSeries.__table__, ("country_name", "maturity_months", "kind")),
        (
            market.LATEST_FLOWS,
            MarketInvestorFlowSnapshot.__table__,
            ("market_code", "observed_at", "foreign_net_buy_amount"),
        ),
        (market.LATEST_MOVEMENTS, MarketMovementSnapshot.__table__, ("rising_count", "falling_count")),
    ],
)
def test_queries_name_columns_that_exist(statement: str, table: Table, columns: tuple[str, ...]):
    """조회하는 컬럼이 모델에 실제로 있는지 대조한다. 수집기 테스트가 INSERT에 하는 것과 같다."""
    for column in columns:
        assert column in table.columns, f"{table.name}.{column}"
        assert column in statement


def _block_text(blocks) -> str:
    return json.dumps(blocks, ensure_ascii=False)
