import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Self

import pytest
from sqlalchemy import Table

from apps.models.market import (
    IndexBar,
    IndicatorObservation,
    KrxMarketFundsDaily,
    KrxStockSecuritiesLendingDaily,
    KrxStockShortSaleDaily,
    MarketInvestorFlowSnapshot,
    MarketMovementSnapshot,
    StockBar,
    StockInvestorEstimateSnapshot,
    StockInvestorTradeDaily,
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
    ("yahoo", "BTCUSD", "비트코인", "crypto", "XX", Decimal(118000), Decimal(115000), MIDDAY),
    ("yahoo", "USDKRW", "원/달러(장외)", "fx", "KR", Decimal("1391.20"), Decimal("1388.60"), MIDDAY),
    # 미국 상장 ADR. country는 회사 국적(TW·KR)이라 국내·아시아 표로 새기 쉬운 값이다.
    ("yahoo", "TSMC_ADR", "TSMC ADR", "equity", "TW", Decimal("192.40"), Decimal("189.10"), MIDDAY),
    ("yahoo", "SK_HYNIX_ADR", "SK하이닉스 ADR", "equity", "KR", Decimal("155.62"), Decimal("151.30"), MIDDAY),
]

# 국내 종목은 quote_bar 뷰가 아니라 stock_bar 직접 조회다(NXT 포함). 마지막 칸이 거래소다.
DOMESTIC_STOCK_ROWS = [
    ("kis", "005930", "삼성전자", "equity", "KR", Decimal(268500), Decimal(266000), MIDDAY, "KRX"),
    ("kis", "000660", "SK하이닉스", "equity", "KR", Decimal(298000), Decimal(295500), MIDDAY, "KRX"),
]

RATE_ROWS = [
    ("fred", "DGS10", "US", "미국", "미국 10년물", date(2026, 8, 17), Decimal("4.21"), Decimal("4.18")),
    ("ecos", "KTB10Y", "KR", "한국", "국고채 10년", date(2026, 8, 17), Decimal("3.05"), Decimal("3.09")),
]

FLOW_ROWS = [("KOSPI", MIDDAY, Decimal(-152300000000), Decimal(88400000000), Decimal(61200000000))]

STOCK_FLOW_ROWS = [("005930", "삼성전자", date(2026, 8, 18), 1_971_000, -648_000, 1_323_000, MIDDAY)]

# 마감 확정: 종가·전일종가와 12분류 중 브리핑이 읽는 몫. 거래일이 실행일과 같아야 그려진다.
STOCK_TRADE_ROWS = [
    (
        "005930",
        "삼성전자",
        date(2026, 8, 18),
        Decimal(268500),
        Decimal(281500),
        -1_500_000,
        820_000,
        640_000,
        500_000,
        120_000,
        10_000,
        5_000,
        30_000,
        5_000,
        155_000,
    )
]

MOVEMENT_ROWS = [("KOSPI", MIDDAY, 512, 61, 341)]

# 증시자금 최신 2영업일. 신용융자·미수금 증감은 전일 행과의 차이로 계산된다.
FUNDS_ROWS = [
    (date(2026, 8, 18), Decimal("512345.00"), Decimal("-2345.00"), Decimal("205000.00"), Decimal("9100.00")),
    (date(2026, 8, 17), Decimal("514690.00"), Decimal("1000.00"), Decimal("203500.00"), Decimal("9500.00")),
]

SHORT_POSITION_ROWS = [
    ("005930", "삼성전자", date(2026, 8, 18), 1_200_000, Decimal("3.42"), 25_000_000, -350_000),
]

# 스프레드 다리. 미국 10-2 = 53bp(전일 48bp), 한미 10년 = -116bp.
SPREAD_ROWS = [
    ("fred", "DGS2", date(2026, 8, 17), Decimal("3.68"), Decimal("3.70")),
    ("fred", "DGS10", date(2026, 8, 17), Decimal("4.21"), Decimal("4.18")),
    ("ecos", "KTB2Y", date(2026, 8, 18), Decimal("2.55"), Decimal("2.54")),
    ("ecos", "KTB10Y", date(2026, 8, 18), Decimal("3.05"), Decimal("3.09")),
]


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
    connection = FakeConnection(
        QUOTE_ROWS,
        DOMESTIC_STOCK_ROWS,
        RATE_ROWS,
        FLOW_ROWS,
        STOCK_FLOW_ROWS,
        STOCK_TRADE_ROWS,
        MOVEMENT_ROWS,
        FUNDS_ROWS,
        SHORT_POSITION_ROWS,
        SPREAD_ROWS,
    )
    return market.collect_summary(connection, now)


def test_change_is_computed_from_the_stored_previous_close():
    quotes = {quote.symbol: quote for quote in summary().quotes}

    assert quotes["KOSPI"].change_percent == pytest.approx(0.82, abs=0.01)
    assert quotes["NIKKEI225"].change_percent < 0


def test_the_quote_window_survives_a_long_holiday():
    """연휴 뒤 첫 실행에서도 직전 거래일 종가가 창에 들어와야 한다.

    실측(2026-08-18 화): 광복절 대체공휴일로 직전 거래일이 금요일 08-14였고, 4일 창은
    그 세션 종료(KST 15:30)를 놓쳐 국내 시세가 통째로 비었다. 설날·추석은 더 길다.
    """
    assert market.QUOTE_LOOKBACK >= timedelta(days=8)
    assert market.FLOW_LOOKBACK >= timedelta(days=8)


def test_every_row_carries_its_own_as_of_time():
    """행마다 기준 시각이 달라질 수 있다.

    실측(2026-08-18): KIS 심볼은 08-14, yahoo 심볼은 08-15 값이었는데 표에는 시각이 없고
    context에 가장 최신 하나만 있어 전부 같은 시점처럼 보였다. 어느 줄이 묵었는지는
    그 줄에 적혀야 안다.
    """
    stale = MIDDAY - timedelta(days=3)
    connection = FakeConnection(
        [
            ("kis", "KOSPI", "코스피", "index", "KR", Decimal("2687.45"), Decimal("2665.60"), stale),
            ("kis", "KOSDAQ", "코스닥", "index", "KR", Decimal("745.10"), Decimal("747.42"), MIDDAY),
        ],
        [], [], [], [], [], [], [], [], [],
    )
    blocks_out = market.render_blocks(market.collect_summary(connection, MIDDAY), MarketScope.KOREA)
    table = next(block for block in blocks_out if block["type"] == "table")
    rows = [[cell["text"] for cell in row] for row in table["rows"]]

    assert rows[0][-1] == "기준"  # 열 제목
    assert rows[1] == ["코스피", "2,687.45", "▲ +0.82%", "08/15 12:30"]  # 묵은 줄
    assert rows[2] == ["코스닥", "745.10", "▼ -0.31%", "08/18 12:30"]  # 최신 줄


def test_nxt_bars_are_labeled_so_they_do_not_read_as_krx_closes():
    """국내 종목 행은 거래소 열이 KRX·NXT를 밝힌다. NXT 봉이 KRX 마감값처럼 읽히면 안 된다.

    거래소를 아는 행이 하나라도 있으면 열이 생기고, 지수처럼 모르는 행은 `-`다.
    """
    after_hours = datetime(2026, 8, 18, 9, 59, tzinfo=UTC)  # KST 18:59 NXT 애프터마켓
    connection = FakeConnection(
        [("kis", "KOSPI", "코스피", "index", "KR", Decimal("2687.45"), Decimal("2665.60"), after_hours)],
        [
            ("kis", "005930", "삼성전자", "equity", "KR", Decimal(268500), Decimal(266000), after_hours, "NXT"),
            ("kis", "000660", "SK하이닉스", "equity", "KR", Decimal(298000), Decimal(295500), after_hours, "KRX"),
        ],
        [], [], [], [], [], [], [], [],
    )
    result = market.collect_summary(connection, after_hours)
    table = next(block for block in market.render_blocks(result, MarketScope.KOREA) if block["type"] == "table")
    rows = [[cell["text"] for cell in row] for row in table["rows"]]
    by_label = {row[0]: row for row in rows[1:]}

    assert rows[0] == ["구분", "종가", "등락", "거래소", "기준"]
    assert by_label["삼성전자"][3] == "NXT"
    assert by_label["SK하이닉스"][3] == "KRX"
    assert by_label["코스피"][3] == "-"


def test_tables_without_exchange_rows_do_not_grow_an_exchange_column():
    """해외·환율처럼 거래소를 모르는 표는 거래소 열 자체가 없어야 한다."""
    blocks_out = market.render_blocks(summary(), MarketScope.KOREA)
    tables = [block for block in blocks_out if block["type"] == "table"]
    overseas = next(
        table
        for table in tables
        if any(cell["text"] == "S&P500 선물" for row in table["rows"] for cell in row)
    )

    assert [cell["text"] for cell in overseas["rows"][0]] == ["구분", "종가", "등락", "기준"]


def test_the_context_flags_the_oldest_value():
    """행마다 시각이 붙어도 한눈에 "가장 묵은 게 언제냐"는 따로 보여야 한다."""
    context = market.render_blocks(summary(), MarketScope.KOREA)[-1]

    assert "가장 오래된" in context["elements"][0]["text"]


def test_yields_are_not_drawn_as_percent_moves():
    """금리 심볼(US10Y)은 시세 표에 넣지 않는다.

    4.65 → 4.70을 `+1.19%`로 그리면 5bp 움직임이 1% 넘게 뛴 것처럼 보인다. 금리는
    indicator_observation 쪽 표가 bp로 그린다.
    """
    connection = FakeConnection(
        [("yahoo", "US10Y", "미국 10년물 금리", "rate", "US", Decimal("4.70"), Decimal("4.65"), MIDDAY)],
        *([[]] * 9),
    )
    result = market.collect_summary(connection, MORNING)

    assert "미국 10년물 금리" not in _block_text(market.render_blocks(result, MarketScope.US))


def test_korea_report_shows_domestic_quotes_and_us_futures_only():
    text = _block_text(market.render_blocks(summary(), MarketScope.KOREA))

    assert "코스피" in text
    assert "S&P500 선물" in text
    # 미국 현물 지수는 한국장 시간에 멈춰 있다. 선물만 실시간이다.
    assert "필라델피아 반도체 지수" not in text
    # 금리는 미국장 아침 리포트가 그린다.
    assert "미국 10년물" not in text


def test_us_report_shows_overseas_rates_and_the_korean_recap():
    text = _block_text(market.render_blocks(summary(MORNING), MarketScope.US))

    assert "필라델피아 반도체 지수" in text
    assert "미국 10년물" in text
    # 조합 평가를 읽는 사람이 같은 화면에서 전일 한국장을 봐야 한다.
    assert "코스피" in text










def test_preopen_report_shows_premarket_stocks_and_skips_us_briefing_sections():
    """프리마켓 발송(08:10·09:00). NXT 프리마켓 종목·환율·전일 확정치만 그린다.

    08:00 미국장 리포트가 이미 보낸 것(미국 지수·선물, 금리, 전일 국내 지수, 수급)은 뺀다.
    """
    preopen = datetime(2026, 8, 18, 0, 0, tzinfo=UTC)  # KST 09:00 개장
    premarket_bar = datetime(2026, 8, 17, 23, 50, tzinfo=UTC)  # KST 08:50 프리마켓 마지막 봉
    connection = FakeConnection(
        QUOTE_ROWS,
        [
            ("kis", "005930", "삼성전자", "equity", "KR", Decimal(268500), Decimal(266000), premarket_bar, "NXT"),
            ("kis", "000660", "SK하이닉스", "equity", "KR", Decimal(298000), Decimal(295500), premarket_bar, "NXT"),
        ],
        RATE_ROWS,
        FLOW_ROWS,
        STOCK_FLOW_ROWS,
        STOCK_TRADE_ROWS,
        MOVEMENT_ROWS,
        FUNDS_ROWS,
        SHORT_POSITION_ROWS,
        SPREAD_ROWS,
    )
    result = market.collect_summary(connection, preopen)
    rendered = market.render_blocks(result, MarketScope.KOREA_PREOPEN)
    text = _block_text(rendered)

    assert "삼성전자" in text
    # 프리마켓 봉은 거래소 열이 NXT를 밝힌다.
    stock_table = next(block for block in rendered if block["type"] == "table")
    rows = [[cell["text"] for cell in row] for row in stock_table["rows"]]
    assert rows[0] == ["구분", "종가", "등락", "거래소", "기준"]
    assert {row[3] for row in rows[1:]} == {"NXT"}
    assert "원/달러(장외)" in text
    # 전일 확정치는 08:00 미국장 리포트에 없어서 실린다.
    assert "고객예탁금" in text
    assert "공매도" in text
    assert "등락 종목 수" in text
    # 08:00 미국장 리포트와 겹치는 섹션들.
    assert "코스피" not in text
    assert "S&P500 선물" not in text
    assert "미국 10년물" not in text
    assert "투자자 순매수" not in text
    assert "종목 추정 순매수" not in text
    assert "마감 확정" not in text


def test_preopen_fallback_text_leads_with_premarket_stocks():
    result = summary()
    text = market.render_text(result, MarketScope.KOREA_PREOPEN)

    assert text.startswith("한국장 프리마켓 브리핑 · ")
    assert "삼성전자" in text
    assert "코스피" not in text


def test_preopen_chart_window_opens_with_the_nxt_premarket():
    """프리마켓 발송의 차트 창은 09:00이 아니라 NXT 프리마켓 08:00에서 시작한다."""
    connection = FakeConnection([], [])
    market.collect_chart_series(connection, MIDDAY, open_hour=market.NXT_PREMARKET_OPEN_HOUR_KST)

    since = connection.cursors[0].calls[0][1][2]
    assert (since.hour, since.minute) == (8, 0)


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
    blocks = market.render_blocks(summary(), MarketScope.KOREA)

    header = blocks[0]["text"]["text"]
    assert "12:30" in header
    # 요일은 표에서 온다. strftime("%a")는 컨테이너 로케일을 타서 조용히 영어가 된다.
    assert "08/18(화)" in header


def test_fallback_text_is_one_line():
    text = market.render_text(summary(), MarketScope.KOREA)

    assert "\n" not in text
    assert "코스피" in text


def test_rows_are_ordered_for_reading_not_alphabetically():
    """가나다·알파벳 순서는 코스닥을 코스피 위로 올린다."""
    result = summary()

    # 목록에 없는 국내 종목(stock_bar 직접 조회)은 지수·선물 뒤로 밀린다.
    assert [quote.symbol for quote in market._korea_quotes(result)] == ["KOSPI", "KOSPI200_FUT", "005930", "000660"]
    # 크립토는 country가 XX라 나라 목록의 뒤로 밀린다.
    assert [quote.country for quote in market._intraday_overseas(result)] == ["US", "JP", "XX"]

    table = next(block for block in market.render_blocks(result, MarketScope.KOREA) if block["type"] == "table")
    assert [row[0]["text"] for row in table["rows"]] == ["구분", "코스피", "코스피200 선물", "삼성전자", "SK하이닉스"]


def test_the_korea_report_draws_the_realtime_fx_table():
    """환율은 장외 실시간(fx_bar)만 그린다. 하나은행 고시 수집은 2026-08에 끝났다."""
    korea = _block_text(market.render_blocks(summary(), MarketScope.KOREA))

    assert "환율(실시간·장외)" in korea
    assert "원/달러(장외)" in korea


def test_us_rows_are_grouped_by_kind():
    """정렬 없이 두면 SQL이 심볼 이름순으로 줘서 금·나스닥·비트코인이 섞인다."""
    kinds = [quote.kind for quote in market._us_quotes(summary())]

    assert kinds == sorted(kinds, key=market.US_KIND_ORDER.index)
    # 픽스처의 SOX, SP500_FUT, BTCUSD, ADR 두 개 순서
    assert kinds == ["index", "index_future", "crypto", "equity", "equity"]


def test_us_listed_adrs_are_drawn_in_the_us_table_only():
    """ADR의 country는 회사 국적(TW·KR)이지만 거래는 뉴욕 세션이다.

    국적으로 거르면 SK하이닉스 ADR이 국내 표에, TSMC ADR이 장중 해외 표에 섞여
    뉴욕 마감값이 장중 값처럼 보인다.
    """
    result = summary()

    us_symbols = [quote.symbol for quote in market._us_quotes(result)]
    assert "TSMC_ADR" in us_symbols
    assert "SK_HYNIX_ADR" in us_symbols
    assert all(quote.symbol not in ("TSMC_ADR", "SK_HYNIX_ADR") for quote in market._korea_quotes(result))
    assert all(quote.symbol not in ("TSMC_ADR", "SK_HYNIX_ADR") for quote in market._intraday_overseas(result))


def test_crypto_is_drawn_in_both_reports_despite_having_no_country():
    korea = _block_text(market.render_blocks(summary(), MarketScope.KOREA))
    us = _block_text(market.render_blocks(summary(MORNING), MarketScope.US))

    assert "비트코인" in korea
    assert "비트코인" in us


def test_market_funds_are_drawn_with_computed_deltas():
    """예탁금 증감은 API 값이고 신용융자·미수금 증감은 전일 행과의 차이다."""
    rendered = market.render_blocks(summary(), MarketScope.KOREA)
    table = next(
        block for block in rendered if block["type"] == "table" and block["rows"][1][0]["text"] == "고객예탁금"
    )
    rows = [[cell["text"] for cell in row] for row in table["rows"][1:]]

    assert rows[0] == ["고객예탁금", "512,345", "-2,345", "08/18"]
    assert rows[1] == ["신용융자 잔고", "205,000", "+1,500", "08/18"]
    assert rows[2] == ["미수금", "9,100", "-400", "08/18"]


def test_every_table_ends_with_a_reference_stamp():
    """모든 표의 마지막 열은 기준이다. 장중·확정·일별이 섞인 리포트라 기준 없는 표는
    어느 시점 값인지 알 수 없다."""
    for scope in (MarketScope.KOREA, MarketScope.US):
        for block in market.render_blocks(summary(MORNING if scope is MarketScope.US else MIDDAY), scope):
            if block["type"] == "table":
                assert block["rows"][0][-1]["text"] == "기준", block["rows"][0]


def test_short_sale_and_lending_share_one_table():
    rendered = market.render_blocks(summary(), MarketScope.KOREA)
    table = next(
        block for block in rendered if block["type"] == "table" and block["rows"][0][0]["text"] == "종목"
        and block["rows"][0][1]["text"] == "공매도 비중"
    )
    rows = [[cell["text"] for cell in row] for row in table["rows"][1:]]

    assert rows[0] == ["삼성전자", "3.42%", "1,200,000", "25,000,000", "-350,000", "08/18"]


def test_rate_spreads_are_measured_in_bp_and_show_inversion_as_negative():
    """스프레드는 bp이고 한미 역전(-116bp)의 음수가 그대로 보여야 한다."""
    result = summary(MORNING)

    spreads = {spread.label: spread for spread in result.spreads}
    assert spreads["미국 10년-2년"].spread_bp == pytest.approx(53.0)
    assert spreads["미국 10년-2년"].change_bp == pytest.approx(5.0)
    assert spreads["한미 10년"].spread_bp == pytest.approx(-116.0)

    us = _block_text(market.render_blocks(result, MarketScope.US))
    assert "금리 스프레드" in us
    assert "-116bp" in us
    # 스프레드는 미국장(아침) 리포트 소관이다. 금리와 같은 이유다.
    assert "금리 스프레드" not in _block_text(market.render_blocks(summary(), MarketScope.KOREA))


def test_chart_series_keep_the_symbol_order_and_skip_empty_ones():
    """봉이 없는 심볼은 계열이 없다. 개장 전이나 새 심볼로 리포트가 죽으면 안 된다."""
    view_rows = [
        ("kis", "KOSPI", "코스피", MIDDAY - timedelta(minutes=1), Decimal("2685.10")),
        ("kis", "KOSPI", "코스피", MIDDAY, Decimal("2687.45")),
    ]
    stock_rows = [
        ("kis", "005930", "삼성전자", MIDDAY - timedelta(minutes=1), Decimal(267500), "KRX"),
        ("kis", "005930", "삼성전자", MIDDAY, Decimal(268000), "KRX"),
    ]
    series = market.collect_chart_series(FakeConnection(view_rows, stock_rows), MIDDAY)

    assert [one.symbol for one in series] == ["KOSPI", "005930"]  # CHART_SYMBOLS 순서
    assert len(series[0].points) == 2
    assert series[0].label == "코스피"
    assert series[1].label == "삼성전자(KRX)"


def test_a_single_bar_is_also_skipped_as_an_empty_chart():
    """점 하나로는 선이 안 그려진다. 09:00 개장 직후 코스피·코스닥의 실제 상태다."""
    view_rows = [("kis", "KOSPI", "코스피", MIDDAY, Decimal("2687.45"))]
    stock_rows = [
        ("kis", "005930", "삼성전자", MIDDAY - timedelta(minutes=1), Decimal(267500), "KRX"),
        ("kis", "005930", "삼성전자", MIDDAY, Decimal(268000), "KRX"),
    ]
    series = market.collect_chart_series(FakeConnection(view_rows, stock_rows), MIDDAY)

    assert [one.symbol for one in series] == ["005930"]


def test_chart_labels_name_the_exchange_of_their_bars():
    """종목 차트 라벨은 어느 거래소 봉인지 밝힌다. 프리마켓은 (NXT), 하루가 섞이면 (KRX·NXT).

    거래소 개념이 없는 지수·환율 라벨은 그대로다.
    """
    premarket = [
        ("kis", "005930", "삼성전자", MIDDAY - timedelta(minutes=1), Decimal(267500), "NXT"),
        ("kis", "005930", "삼성전자", MIDDAY, Decimal(268000), "NXT"),
    ]
    mixed = [
        ("kis", "005930", "삼성전자", MIDDAY - timedelta(minutes=1), Decimal(267500), "KRX"),
        ("kis", "005930", "삼성전자", MIDDAY, Decimal(268000), "NXT"),
    ]

    nxt_series = market.collect_chart_series(FakeConnection([], premarket), MIDDAY)
    mixed_series = market.collect_chart_series(FakeConnection([], mixed), MIDDAY)

    assert nxt_series[0].label == "삼성전자(NXT)"
    assert mixed_series[0].label == "삼성전자(KRX·NXT)"


def test_each_chart_file_gets_its_own_image_block_after_the_domestic_table():
    with_chart = market.render_blocks(
        summary(), MarketScope.KOREA, chart_files=(("F0AAA", "코스피"), ("F0BBB", "삼성전자"))
    )

    images = [block for block in with_chart if block["type"] == "image"]
    assert [image["slack_file"]["id"] for image in images] == ["F0AAA", "F0BBB"]
    assert images[0]["alt_text"] == "코스피 당일 분봉 차트"
    assert with_chart[with_chart.index(images[0]) - 1]["type"] == "table"


def test_chart_failure_is_visible_and_absence_is_silent():
    """실패는 채널에 남고, 그릴 봉이 없는 것은 정상 흐름이라 아무 것도 남지 않는다."""
    failed = market.render_blocks(summary(), MarketScope.KOREA, chart_error="boom")
    silent = market.render_blocks(summary(), MarketScope.KOREA)

    assert "차트 생성 실패" in _block_text(failed)
    assert "차트" not in _block_text(silent)


def test_stock_estimates_are_counted_in_shares_not_won():
    """종목 수급은 추정 **수량**이다. 억원인 시장 수급과 한 표에 섞으면 자릿수가 뜻을 잃는다."""
    rendered = market.render_blocks(summary(), MarketScope.KOREA)
    titles = [block["text"]["text"] for block in rendered if block["type"] == "section"]
    table = next(
        block for block in rendered if block["type"] == "table" and block["rows"][0][0]["text"] == "종목"
    )

    assert "*종목 추정 순매수(주)*" in titles
    # 기준은 수집 시각이다. 장중 몇 차례 갱신되는 추정이라 날짜만으로는 아침 값과 마감 값이 같아 보인다.
    assert [cell["text"] for cell in table["rows"][1]] == [
        "삼성전자", "+1,971,000", "-648,000", "+1,323,000", "08/18 12:30",
    ]


@pytest.mark.parametrize(
    ("statement", "table", "columns"),
    [
        # LATEST_QUOTES는 quote_bar **뷰**를 읽는다. 뷰의 컬럼은
        # kind 테이블과 같으므로 대표로 IndexBar 모델과 대조한다.
        (market.LATEST_QUOTES, IndexBar.__table__, ("provider", "symbol", "close", "previous_close", "bar_at")),
        (market.LATEST_QUOTES, QuoteSymbol.__table__, ("label", "kind", "country")),
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
        (
            market.LATEST_STOCK_FLOWS,
            StockInvestorEstimateSnapshot.__table__,
            ("stock_code", "business_date", "source_time_code", "foreign_net_buy_qty"),
        ),
        (
            market.LATEST_STOCK_TRADES,
            StockInvestorTradeDaily.__table__,
            ("stock_code", "business_date", "close_price", "institution_net_buy_qty", "pension_fund_net_buy_qty"),
        ),
        (market.INTRADAY_SERIES, IndexBar.__table__, ("provider", "symbol", "bar_at", "close")),
        (market.INTRADAY_SERIES, QuoteSymbol.__table__, ("label",)),
        # 국내 종목 조회는 stock_bar 물리 테이블을 직접 읽는다(NXT 포함).
        (
            market.LATEST_DOMESTIC_STOCKS,
            StockBar.__table__,
            ("provider", "stock_code", "exchange", "close", "previous_close", "bar_at"),
        ),
        (market.LATEST_DOMESTIC_STOCKS, QuoteSymbol.__table__, ("label", "kind", "country")),
        (market.DOMESTIC_STOCK_SERIES, StockBar.__table__, ("provider", "stock_code", "exchange", "bar_at", "close")),
        (market.DOMESTIC_STOCK_SERIES, QuoteSymbol.__table__, ("label",)),
        (
            market.LATEST_MARKET_FUNDS,
            KrxMarketFundsDaily.__table__,
            ("business_date", "customer_deposit", "customer_deposit_change", "credit_loan_balance", "unsettled_amount"),
        ),
        (
            market.LATEST_SHORT_POSITIONS,
            KrxStockShortSaleDaily.__table__,
            ("stock_code", "business_date", "short_sale_quantity", "short_sale_volume_ratio"),
        ),
        (
            market.LATEST_SHORT_POSITIONS,
            KrxStockSecuritiesLendingDaily.__table__,
            ("balance_quantity", "balance_change_quantity"),
        ),
        (market.SPREAD_PAIRS, IndicatorObservation.__table__, ("provider", "series_id", "observation_date", "value")),
    ],
)
def test_queries_name_columns_that_exist(statement: str, table: Table, columns: tuple[str, ...]):
    """조회하는 컬럼이 모델에 실제로 있는지 대조한다. 수집기 테스트가 INSERT에 하는 것과 같다."""
    for column in columns:
        assert column in table.columns, f"{table.name}.{column}"
        assert column in statement


def _block_text(blocks) -> str:
    return json.dumps(blocks, ensure_ascii=False)
