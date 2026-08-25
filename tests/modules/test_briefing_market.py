import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
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
from modules.briefing import market, market_data
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
    # KIS 해외지수로 받는 미국 현물. 선물(SP500_FUT) 옆에 놓여야 한다.
    ("kis", "SP500", "S&P500", "index", "US", Decimal("7674.37"), Decimal("7641.16"), MIDDAY),
    ("yahoo", "GOLD", "금", "commodity", "US", Decimal("3380.50"), Decimal("3350.00"), MIDDAY),
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

# 마지막 두 칸이 직전 관측값과 그 관측일이다. 관측이 매일 있는 것이 아니라 직전이 전일이
# 아닐 수 있어 날짜가 함께 온다.
RATE_ROWS = [
    (
        "fred",
        "DGS10",
        "US",
        "미국",
        "미국 10년물",
        date(2026, 8, 17),
        Decimal("4.21"),
        Decimal("4.18"),
        date(2026, 8, 14),
    ),
    (
        "ecos",
        "KTB10Y",
        "KR",
        "한국",
        "국고채 10년",
        date(2026, 8, 17),
        Decimal("3.05"),
        Decimal("3.09"),
        date(2026, 8, 14),
    ),
]

# 마지막 네 칸이 직전 거래일 마감 스냅샷이다(세션 날짜와 세 분류).
FLOW_ROWS = [
    (
        "KOSPI",
        MIDDAY,
        Decimal(-152300000000),
        Decimal(88400000000),
        Decimal(61200000000),
        date(2026, 8, 17),
        Decimal(-40000000000),
        Decimal(25000000000),
        Decimal(15000000000),
    )
]

STOCK_FLOW_ROWS = [
    (
        "005930",
        "삼성전자",
        date(2026, 8, 18),
        1_971_000,
        -648_000,
        1_323_000,
        MIDDAY,
        date(2026, 8, 17),
        800_000,
        -200_000,
        600_000,
    )
]

# 마감 확정: 종가·전일종가와 12분류 중 브리핑이 읽는 몫. 거래일이 실행일과 같아야 그려진다.
STOCK_TRADE_ROWS = [
    (
        "005930",
        "삼성전자",
        date(2026, 8, 18),
        Decimal(268500),
        Decimal(281500),
        # 직전 거래일(08/17)의 날짜와 수급 세 칸. 종가 다음에 온다.
        date(2026, 8, 17),
        -300_000,
        150_000,
        150_000,
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

MOVEMENT_ROWS = [("KOSPI", MIDDAY, 512, 61, 341, date(2026, 8, 17), 430, 70, 414)]

# 증시자금 최신 2영업일. 증감은 세 항목 모두 전일 행과의 차이로 계산된다.
FUNDS_ROWS = [
    (date(2026, 8, 18), Decimal("512345.00"), Decimal("-2345.00"), Decimal("205000.00"), Decimal("9100.00")),
    (date(2026, 8, 17), Decimal("514690.00"), Decimal("1000.00"), Decimal("203500.00"), Decimal("9500.00")),
]

# 직전 수집일(08/17)의 값은 SQL의 LAG가 준다. 공매도·대차 둘 다 그 행에서 온다.
SHORT_POSITION_ROWS = [
    (
        "005930",
        "삼성전자",
        date(2026, 8, 18),
        date(2026, 8, 17),
        1_200_000,
        1_050_000,
        Decimal("3.42"),
        25_000_000,
        25_350_000,
    ),
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
        # 결과를 다 쓰면 빈 목록이다. 조회가 하나 늘 때마다 모든 테스트의 픽스처를 늘리지
        # 않으려는 것이고, "그 조회에 행이 없었다"는 실제로 일어나는 상태이기도 하다.
        return self.results.pop(0) if self.results else []


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
    return market_data.MarketBriefingReader(connection, now).summary()


def test_change_is_computed_from_the_stored_previous_close():
    quotes = {quote.symbol: quote for quote in summary().quotes}

    assert quotes["KOSPI"].change_percent == pytest.approx(0.82, abs=0.01)
    assert quotes["NIKKEI225"].change_percent < 0


def test_the_quote_window_survives_a_long_holiday():
    """연휴 뒤 첫 실행에서도 직전 거래일 종가가 창에 들어와야 한다.

    실측(2026-08-18 화): 광복절 대체공휴일로 직전 거래일이 금요일 08-14였고, 4일 창은
    그 세션 종료(KST 15:30)를 놓쳐 국내 시세가 통째로 비었다. 설날·추석은 더 길다.
    """
    assert market_data.QUOTE_LOOKBACK >= timedelta(days=8)
    assert market_data.FLOW_LOOKBACK >= timedelta(days=8)


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
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [],
    )
    blocks_out = market.render_blocks(market_data.MarketBriefingReader(connection, MIDDAY).summary(), MarketScope.KOREA)
    table = next(block for block in blocks_out if block["type"] == "table")
    rows = [[cell["text"] for cell in row] for row in table["rows"]]

    assert rows[0][-1] == "기준"  # 열 제목
    assert rows[1] == ["코스피", "2,687.45", "2,665.60", "▲ +0.82%", "08/15 12:30"]  # 묵은 줄
    assert rows[2] == ["코스닥", "745.10", "747.42", "▼ -0.31%", "08/18 12:30"]  # 최신 줄


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
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [],
    )
    result = market_data.MarketBriefingReader(connection, after_hours).summary()
    table = next(block for block in market.render_blocks(result, MarketScope.KOREA) if block["type"] == "table")
    rows = [[cell["text"] for cell in row] for row in table["rows"]]
    by_label = {row[0]: row for row in rows[1:]}

    assert rows[0] == ["구분", "종가", "전일 종가", "등락", "거래소", "기준"]
    assert by_label["삼성전자"][4] == "NXT"
    assert by_label["SK하이닉스"][4] == "KRX"
    assert by_label["코스피"][4] == "-"


def test_tables_without_exchange_rows_do_not_grow_an_exchange_column():
    """해외·환율처럼 거래소를 모르는 표는 거래소 열 자체가 없어야 한다."""
    blocks_out = market.render_blocks(summary(), MarketScope.KOREA)
    tables = [block for block in blocks_out if block["type"] == "table"]
    overseas = next(
        table for table in tables if any(cell["text"] == "S&P500 선물" for row in table["rows"] for cell in row)
    )

    assert [cell["text"] for cell in overseas["rows"][0]] == ["구분", "종가", "전일 종가", "등락", "기준"]


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
    result = market_data.MarketBriefingReader(connection, MORNING).summary()

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
    result = market_data.MarketBriefingReader(connection, preopen).summary()
    rendered = market.render_blocks(result, MarketScope.KOREA_PREOPEN)
    text = _block_text(rendered)

    assert "삼성전자" in text
    # 프리마켓 봉은 거래소 열이 NXT를 밝힌다.
    stock_table = next(block for block in rendered if block["type"] == "table")
    rows = [[cell["text"] for cell in row] for row in stock_table["rows"]]
    assert rows[0] == ["구분", "종가", "전일 종가", "등락", "거래소", "기준"]
    assert {row[4] for row in rows[1:]} == {"NXT"}
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
    market_data.MarketBriefingReader(connection, MIDDAY).chart_series(open_hour=market_data.NXT_PREMARKET_OPEN_HOUR_KST)

    since = connection.cursors[0].calls[0][1][2]
    assert (since.hour, since.minute) == (8, 0)


def test_us_session_date_follows_new_york_not_seoul():
    """KST 화요일 아침에 보는 미국 세션은 뉴욕 월요일이다."""
    assert market_data.us_session_date(MORNING) == date(2026, 8, 17)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 8, 18, 3, 30, tzinfo=UTC), "장중"),  # KST 12:30
        (datetime(2026, 8, 17, 23, 30, tzinfo=UTC), "개장 전"),  # KST 08:30
        (datetime(2026, 8, 18, 7, 30, tzinfo=UTC), "마감 후"),  # KST 16:30
    ],
)
def test_session_state_uses_the_korean_clock(now, expected):
    assert market_data.session_state(now) == expected


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


def _us_tables(result) -> list[tuple[str, list[str]]]:
    """미국장 리포트의 (표 제목, 첫 열) 목록. 제목 section 바로 뒤에 table이 온다."""
    rendered = market.render_blocks(result, MarketScope.US)
    tables = []
    for title_block, table in pairwise(rendered):
        if table.get("type") == "table":
            title = title_block["text"]["text"].strip("*")
            tables.append((title, [row[0]["text"] for row in table["rows"][1:]]))
    return tables


def test_us_report_splits_sections_by_kind_and_pairs_spot_with_its_future():
    """한 표에 두면 금·나스닥·비트코인이 한 덩어리로 보인다. 현물 옆에는 그 선물이 온다."""
    tables = dict(_us_tables(summary(MORNING)))

    assert [title for title, _ in _us_tables(summary(MORNING))][:4] == ["미국 지수·선물", "원자재", "크립토", "ADR"]
    assert tables["미국 지수·선물"] == ["S&P500", "S&P500 선물", "필라델피아 반도체 지수"]
    assert tables["원자재"] == ["금"]
    assert tables["크립토"] == ["비트코인"]
    assert tables["ADR"] == ["TSMC ADR", "SK하이닉스 ADR"]


def test_empty_us_sections_are_not_drawn():
    connection = FakeConnection(
        [row for row in QUOTE_ROWS if row[3] not in ("commodity", "crypto")],
        DOMESTIC_STOCK_ROWS,
        *([[]] * 8),
    )
    result = market_data.MarketBriefingReader(connection, MORNING).summary()

    titles = [title for title, _ in _us_tables(result)]
    assert "미국 지수·선물" in titles
    assert "원자재" not in titles
    assert "크립토" not in titles


def test_us_sections_cover_every_quoted_kind():
    """섹션에 빠진 kind는 조용히 사라진다. QUOTED_KINDS를 늘리면 섹션도 늘려야 한다."""
    assert frozenset().union(*(kinds for _, kinds in market.US_SECTIONS)) == market.QUOTED_KINDS


def test_us_fallback_text_leads_with_the_spot_index_and_its_future():
    text = market.render_text(summary(MORNING), MarketScope.US)

    assert text.startswith("미국장 마감 · S&P500 7,674.37 ")
    assert " · S&P500 선물 " in text


def test_kis_us_spot_indexes_stay_out_of_the_korean_reports():
    """미국 현물은 한국장 시간에 닫혀 있다. 부분일치로 보면 `S&P500 선물`에 가려지므로 셀로 본다."""
    for scope in (MarketScope.KOREA, MarketScope.KOREA_PREOPEN):
        cells = [
            cell["text"]
            for block in market.render_blocks(summary(), scope)
            if block.get("type") == "table"
            for row in block["rows"]
            for cell in row
        ]
        assert "S&P500" not in cells
        assert scope is MarketScope.KOREA_PREOPEN or "S&P500 선물" in cells


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
    """세 항목 모두 전일 행과의 차이이고, 등락률은 그 차이를 전일 잔고로 나눈 값이다."""
    rendered = market.render_blocks(summary(), MarketScope.KOREA)
    table = next(
        block for block in rendered if block["type"] == "table" and block["rows"][1][0]["text"] == "고객예탁금"
    )
    rows = [[cell["text"] for cell in row] for row in table["rows"][1:]]

    assert rows[0] == ["고객예탁금", "512,345", "514,690", "-2,345", "-0.46%", "08/17", "08/18"]
    assert rows[1] == ["신용융자 잔고", "205,000", "203,500", "+1,500", "+0.74%", "08/17", "08/18"]
    assert rows[2] == ["미수금", "9,100", "9,500", "-400", "-4.21%", "08/17", "08/18"]


def test_the_first_collection_day_falls_back_to_the_api_delta():
    """전일 행이 없으면 예탁금만 API 전일대비로 그리고 나머지 둘은 `-`다."""
    funds = market_data.MarketBriefingReader(FakeConnection(FUNDS_ROWS[:1]), MIDDAY)._market_funds()

    assert funds.customer_deposit_change == Decimal("-2345.00")
    assert funds.credit_loan_change is None
    assert funds.unsettled_change is None


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
        block
        for block in rendered
        if block["type"] == "table"
        and block["rows"][0][0]["text"] == "종목"
        and block["rows"][0][1]["text"] == "공매도 비중"
    )
    rows = [[cell["text"] for cell in row] for row in table["rows"][1:]]

    assert rows[0] == [
        "삼성전자",
        "3.42%",
        "1,200,000",
        "1,050,000",
        "+14.29%",
        "25,000,000",
        "25,350,000",
        "-1.38%",
        "08/17",
        "08/18",
    ]


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
    series = market_data.MarketBriefingReader(FakeConnection(view_rows, stock_rows), MIDDAY).chart_series()

    assert [one.symbol for one in series] == ["KOSPI", "005930"]  # CHART_SYMBOLS 순서
    assert len(series[0].points) == 2
    assert series[0].label == "코스피"
    assert series[0].venue == "KRX"  # 거래소 열이 없는 지수는 제공처(kis)의 시장을 적는다
    assert series[1].label == "삼성전자"
    assert series[1].venue == "KRX"


def test_a_single_bar_is_also_skipped_as_an_empty_chart():
    """점 하나로는 선이 안 그려진다. 09:00 개장 직후 코스피·코스닥의 실제 상태다."""
    view_rows = [("kis", "KOSPI", "코스피", MIDDAY, Decimal("2687.45"))]
    stock_rows = [
        ("kis", "005930", "삼성전자", MIDDAY - timedelta(minutes=1), Decimal(267500), "KRX"),
        ("kis", "005930", "삼성전자", MIDDAY, Decimal(268000), "KRX"),
    ]
    series = market_data.MarketBriefingReader(FakeConnection(view_rows, stock_rows), MIDDAY).chart_series()

    assert [one.symbol for one in series] == ["005930"]


def test_chart_series_name_the_exchange_of_their_bars():
    """계열은 어느 거래소 봉인지 밝힌다. 프리마켓은 NXT, 하루가 섞이면 KRX·NXT다.

    거래소 열이 없는 지수·환율은 제공처의 시장을 적는다. 차트 제목이 이 값을 그대로 찍는다.
    """
    premarket = [
        ("kis", "005930", "삼성전자", MIDDAY - timedelta(minutes=1), Decimal(267500), "NXT"),
        ("kis", "005930", "삼성전자", MIDDAY, Decimal(268000), "NXT"),
    ]
    mixed = [
        ("kis", "005930", "삼성전자", MIDDAY - timedelta(minutes=1), Decimal(267500), "KRX"),
        ("kis", "005930", "삼성전자", MIDDAY, Decimal(268000), "NXT"),
    ]

    nxt_series = market_data.MarketBriefingReader(FakeConnection([], premarket), MIDDAY).chart_series()
    mixed_series = market_data.MarketBriefingReader(FakeConnection([], mixed), MIDDAY).chart_series()

    assert nxt_series[0].venue == "NXT"
    assert mixed_series[0].venue == "KRX·NXT"
    assert nxt_series[0].label == "삼성전자"


def test_each_chart_file_gets_its_own_image_block_after_the_domestic_table():
    with_chart = market.render_blocks(
        summary(), MarketScope.KOREA, chart_files=(("F0AAA", "코스피"), ("F0BBB", "삼성전자"))
    )

    images = [block for block in with_chart if block["type"] == "image"]
    assert [image["slack_file"]["id"] for image in images] == ["F0AAA", "F0BBB"]
    assert images[0]["alt_text"] == "코스피 차트"
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
    table = next(block for block in rendered if block["type"] == "table" and block["rows"][0][0]["text"] == "종목")

    assert "*종목 추정 순매수(주)*" in titles
    # 기준은 수집 시각이다. 장중 몇 차례 갱신되는 추정이라 날짜만으로는 아침 값과 마감 값이 같아 보인다.
    assert [cell["text"] for cell in table["rows"][1]] == [
        "삼성전자",
        "+1,971,000",
        "+800,000",
        "-648,000",
        "-200,000",
        "+1,323,000",
        "+600,000",
        "08/17",
        "08/18 12:30",
    ]


@pytest.mark.parametrize(
    ("statement", "table", "columns"),
    [
        # LATEST_QUOTES는 quote_bar **뷰**를 읽는다. 뷰의 컬럼은
        # kind 테이블과 같으므로 대표로 IndexBar 모델과 대조한다.
        (market_data.LATEST_QUOTES, IndexBar.__table__, ("provider", "symbol", "close", "previous_close", "bar_at")),
        (market_data.LATEST_QUOTES, QuoteSymbol.__table__, ("label", "kind", "country")),
        (
            market_data.LATEST_RATES,
            IndicatorObservation.__table__,
            ("provider", "series_id", "observation_date", "value"),
        ),
        (market_data.LATEST_RATES, IndicatorSeries.__table__, ("country_name", "maturity_months", "kind")),
        (
            market_data.LATEST_FLOWS,
            MarketInvestorFlowSnapshot.__table__,
            ("market_code", "observed_at", "foreign_net_buy_amount"),
        ),
        (market_data.LATEST_MOVEMENTS, MarketMovementSnapshot.__table__, ("rising_count", "falling_count")),
        (
            market_data.LATEST_STOCK_FLOWS,
            StockInvestorEstimateSnapshot.__table__,
            ("stock_code", "business_date", "source_time_code", "foreign_net_buy_qty"),
        ),
        (
            market_data.LATEST_STOCK_TRADES,
            StockInvestorTradeDaily.__table__,
            ("stock_code", "business_date", "close_price", "institution_net_buy_qty", "pension_fund_net_buy_qty"),
        ),
        (market_data.INTRADAY_SERIES, IndexBar.__table__, ("provider", "symbol", "bar_at", "close")),
        (market_data.INTRADAY_SERIES, QuoteSymbol.__table__, ("label",)),
        # 국내 종목 조회는 stock_bar 물리 테이블을 직접 읽는다(NXT 포함).
        (
            market_data.LATEST_DOMESTIC_STOCKS,
            StockBar.__table__,
            ("provider", "stock_code", "exchange", "close", "previous_close", "bar_at"),
        ),
        (market_data.LATEST_DOMESTIC_STOCKS, QuoteSymbol.__table__, ("label", "kind", "country")),
        (market_data.DOMESTIC_STOCK_SERIES, StockBar.__table__, ("provider", "stock_code", "exchange", "bar_at", "close")),
        (market_data.DOMESTIC_STOCK_SERIES, QuoteSymbol.__table__, ("label",)),
        (
            market_data.LATEST_MARKET_FUNDS,
            KrxMarketFundsDaily.__table__,
            ("business_date", "customer_deposit", "customer_deposit_change", "credit_loan_balance", "unsettled_amount"),
        ),
        (
            market_data.LATEST_SHORT_POSITIONS,
            KrxStockShortSaleDaily.__table__,
            ("stock_code", "business_date", "short_sale_quantity", "short_sale_volume_ratio"),
        ),
        (
            market_data.LATEST_SHORT_POSITIONS,
            KrxStockSecuritiesLendingDaily.__table__,
            ("balance_quantity", "balance_change_quantity"),
        ),
        (market_data.SPREAD_PAIRS, IndicatorObservation.__table__, ("provider", "series_id", "observation_date", "value")),
    ],
)
def test_queries_name_columns_that_exist(statement: str, table: Table, columns: tuple[str, ...]):
    """조회하는 컬럼이 모델에 실제로 있는지 대조한다. 수집기 테스트가 INSERT에 하는 것과 같다."""
    for column in columns:
        assert column in table.columns, f"{table.name}.{column}"
        assert column in statement


def _block_text(blocks) -> str:
    return json.dumps(blocks, ensure_ascii=False)


# --- 기술적 관측 (docs/market-technical-indicators.md 7.2절) --------------------


def technical_history_rows() -> list[tuple]:
    """`technical/select_history.sql` 결과. 두 대상의 최신순 일봉을 한 응답에 담는다."""
    rows = []
    for symbol, label, kind, base in (("KOSPI", "코스피", "index", 3000.0), ("005930", "삼성전자", "equity", 70000.0)):
        cursor = date(2026, 1, 5)
        made = 0
        while made < 120:
            if cursor.weekday() < 5:
                close = Decimal(str(base + made + 1))
                rows.append(
                    (
                        "kis",
                        symbol,
                        label,
                        kind,
                        "KR",
                        cursor,
                        close,
                        close,
                        close,
                        close,
                        1000 + made,
                    )
                )
                made += 1
            cursor += timedelta(days=1)
    return list(reversed(rows))


SIGNAL_ROWS = [
    ("KOSPI", date(2026, 6, 17), "sma_cross", "up"),
    ("005930", date(2026, 6, 15), "macd_cross", "down"),
]


def summary_with_technicals(
    now: datetime = MIDDAY,
    technical_rows: list[tuple] | None = None,
    signal_rows: list[tuple] | None = None,
):
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
        technical_history_rows() if technical_rows is None else technical_rows,
        SIGNAL_ROWS if signal_rows is None else signal_rows,
    )
    return market_data.MarketBriefingReader(connection, now).summary()


def test_daily_chart_series_follows_the_subject_list_not_the_query():
    """일봉 차트 대상은 `DAILY_CHART_SUBJECTS`가 정한다. 조회에는 그 밖의 대상도 온다."""
    connection = FakeConnection(technical_history_rows())

    series = market_data.MarketBriefingReader(connection, MIDDAY).daily_chart_series()

    assert [one.subject_code for one in series] == ["KOSPI", "005930"]
    # 계산기는 오름차순을 받는다. 조회는 최신순이라 여기서 뒤집혀 있어야 한다.
    assert series[0].bars[0].business_date < series[0].bars[-1].business_date


def test_the_chart_query_asks_only_for_the_chart_subjects():
    """차트 조회는 watched를 켜지 않는다. 종목이 늘어도 이미지가 따라 늘면 안 된다."""
    connection = FakeConnection(technical_history_rows())

    market_data.MarketBriefingReader(connection, MIDDAY).daily_chart_series()

    _, parameters = connection.cursors[0].calls[0]
    assert parameters["symbols"] == list(market_data.DAILY_CHART_SUBJECTS)
    assert parameters["include_watched"] is False
    # 환율은 표에 없고 차트에만 있다.
    assert "USDKRW" in parameters["symbols"]


def test_a_subject_without_enough_bars_is_not_drawn():
    """봉이 모자란 대상은 표에서 빠지듯 차트에서도 빠진다. 짧은 선을 그리지 않는다."""
    rows = [row for row in technical_history_rows() if row[1] == "KOSPI"][:10]
    connection = FakeConnection(rows)

    assert market_data.MarketBriefingReader(connection, MIDDAY).daily_chart_series() == ()


def test_the_technical_query_asks_for_the_watched_stocks_too():
    """watched 종목이 늘어도 브리핑 코드를 바꾸지 않는다."""
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
        technical_history_rows(),
    )
    market_data.MarketBriefingReader(connection, MIDDAY).summary()

    statement, parameters = next(
        (statement, parameters)
        for cursor in connection.cursors
        for statement, parameters in cursor.calls
        if "WITH requested AS" in statement
    )
    assert parameters["symbols"] == list(market_data.TECHNICAL_INDEXES)
    assert parameters["include_watched"] is True
    assert "instrument" in statement


def test_technicals_are_computed_per_subject():
    technicals = {snapshot.subject_code: snapshot for snapshot in summary_with_technicals().technicals}

    assert set(technicals) == {"KOSPI", "005930"}
    assert technicals["KOSPI"].label == "코스피"
    assert technicals["KOSPI"].observations == 120
    assert technicals["005930"].sma20 > technicals["005930"].sma60


def test_the_korea_report_shows_the_technical_table():
    rendered = market.render_blocks(summary_with_technicals(), MarketScope.KOREA)
    text = _block_text(rendered)

    assert "기술적 관측" in text
    assert "20일선/60일선" in text
    assert "RSI(14일)" in text
    # 판정 열은 두지 않는다. 표는 수치와 기준일만 말한다.
    table = json.dumps(_technical_table_rows(rendered), ensure_ascii=False)
    assert "매수" not in table
    assert "매도" not in table
    assert "상승" not in table


def test_the_preopen_report_shows_the_technical_table():
    rendered = market.render_blocks(summary_with_technicals(MORNING), MarketScope.KOREA_PREOPEN)

    assert "기술적 관측" in _block_text(rendered)


def test_the_us_report_has_no_technical_table():
    """미국장 리포트는 국내 지표를 그리지 않는다."""
    rendered = market.render_blocks(summary_with_technicals(MORNING), MarketScope.US)

    assert "기술적 관측" not in _block_text(rendered)


def test_no_snapshot_drops_the_whole_table():
    """짧은 표본을 0으로 채우지 않는다. 표 자체가 없어야 그것이 드러난다."""
    rendered = market.render_blocks(summary_with_technicals(technical_rows=[]), MarketScope.KOREA)

    assert "기술적 관측" not in _block_text(rendered)


def _technical_table_rows(rendered) -> list[list[str]]:
    """ "기술적 관측" 섹션 바로 뒤 table 블록의 셀 값."""
    for index, block in enumerate(rendered):
        if block.get("type") == "section" and "기술적 관측" in json.dumps(block, ensure_ascii=False):
            return [[cell["text"] for cell in row] for row in rendered[index + 1]["rows"]]
    raise AssertionError("기술적 관측 표가 없다")


def test_a_missing_volume_ratio_shows_a_dash():
    """거래량 비율을 못 내면 1.00x로 꾸미지 않는다."""
    rows = [row[:10] + (None,) for row in technical_history_rows()]
    rendered = market.render_blocks(summary_with_technicals(technical_rows=rows), MarketScope.KOREA)

    header, *body_rows = _technical_table_rows(rendered)
    column = header.index("거래량/20일평균")
    assert {row[column] for row in body_rows} == {"-"}


def test_the_table_shows_ratios_and_the_reference_day():
    rendered = market.render_blocks(summary_with_technicals(), MarketScope.KOREA)

    header, *body_rows = _technical_table_rows(rendered)
    assert header == [
        "대상",
        "종가/20일선",
        "20일선/60일선",
        "RSI(14일)",
        "MACD 히스토그램",
        "거래량/20일평균",
        "신호",
        "기준",
    ]
    row = next(row for row in body_rows if row[0] == "코스피")
    assert row[1].endswith("%")
    assert row[3].replace(".", "").isdigit()
    # 기준은 그 대상의 마지막 확정 일봉 날짜다. 브리핑 시각이 아니다.
    latest = max(snapshot.as_of_date for snapshot in summary_with_technicals().technicals)
    assert row[-1] == f"{latest:%m/%d}"


def test_the_signal_column_names_the_event_not_a_verdict():
    rendered = market.render_blocks(summary_with_technicals(), MarketScope.KOREA)

    header, *body_rows = _technical_table_rows(rendered)
    column = header.index("신호")
    by_label = {row[0]: row[column] for row in body_rows}
    assert by_label["코스피"] == "골든크로스 06/17"
    assert by_label["삼성전자"] == "MACD↓ 06/15"


def test_no_recent_signal_shows_a_dash():
    """신호가 없는 것과 신호를 못 낸 것을 같은 칸으로 말한다. 빈칸으로 두지 않는다."""
    rendered = market.render_blocks(summary_with_technicals(signal_rows=[]), MarketScope.KOREA)

    header, *body_rows = _technical_table_rows(rendered)
    column = header.index("신호")
    assert {row[column] for row in body_rows} == {"-"}


def test_the_signal_window_is_asked_for_in_days():
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
        technical_history_rows(),
        SIGNAL_ROWS,
    )
    market_data.MarketBriefingReader(connection, MIDDAY).summary()

    statement, parameters = next(
        (statement, parameters)
        for cursor in connection.cursors
        for statement, parameters in cursor.calls
        if "FROM technical_signal" in statement
    )
    assert parameters["since_date"] == (MIDDAY - market_data.SIGNAL_LOOKBACK).astimezone(market.KST_TIMEZONE).date()
    assert "DISTINCT ON (symbol)" in statement
