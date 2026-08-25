"""시장 브리핑이 읽는 것 — SQL, 조회 결과 모델, `MarketBriefingReader`.

**조회와 표현을 파일로 가른다.** 리포트가 둘인데 파일이 하나였던 이유는 한국장과 미국장이
같은 표를 다른 시간대에 다른 조합으로 보여 주기 때문이고, 그건 지금도 같다. 나뉘는 축은
시장이 아니라 **무엇을 읽는가와 어떻게 보여 주는가**다. 조회 SQL 변경과 표시 변경이 한
파일에서 부딪히던 것을 여기서 끊는다. 표시 쪽은 `market.py`다.

조회는 한 번만 한다. 심볼이 수십 개, 계열이 십여 개라 리포트마다 쿼리를 좁히는 값어치가 없다.
`MarketBriefingReader`가 전부 받아 오고 렌더링이 고른다. 그 덕에 미국장 리포트는 밤사이
미국 값과 전일 한국 값을 **한 화면에서** 보여 준다. 그게 이 리포트를 만드는 이유다.

**조회는 클래스다.** 연결과 기준 시각은 한 번의 발송 동안 바뀌지 않는 상태라
`MarketBriefingReader`가 들고 돈다(`.codex/AGENTS.md`의 "클래스와 함수를 가르는 기준").
세션 시각 계산(`session_state`·`us_session_date`)은 날짜 하나를 받아 값 하나를 주는 순수
계산이라 모듈 함수로 둔다. 세션 상수와 같이 있어야 해서 표시 쪽이 아니라 여기다.

**시각은 UTC로 담는다.** KST 변환은 렌더링에서만 하고, 미국 세션 날짜만
`America/New_York` 기준으로 뽑는다.
"""

from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from pendulum import timezone
from pydantic import AwareDatetime, BaseModel, ConfigDict

from modules import technical
from modules.db import Connection
from modules.sql import read_sql
from modules.utility import KST_TIMEZONE

LATEST_QUOTES = read_sql("postgres", "quote_bar", "select_latest_briefing_bars.sql")
# 국내 종목은 quote_bar 뷰가 아니라 물리 테이블을 읽는다. 뷰는 NXT를 태우지 않아서
# 15:30 이후 애프터마켓 값이 안 보인다. 최신 봉 우선·동률 KRX 규칙은 SQL 주석 참고.
LATEST_DOMESTIC_STOCKS = read_sql("postgres", "stock_bar", "select_latest_briefing_bars.sql")
LATEST_RATES = read_sql("postgres", "indicator_observation", "select_latest_pair.sql")
LATEST_FLOWS = read_sql("postgres", "market_investor_flow_snapshot", "select_latest_pair.sql")
LATEST_MOVEMENTS = read_sql("postgres", "market_movement_snapshot", "select_latest_pair.sql")
LATEST_STOCK_FLOWS = read_sql("postgres", "stock_investor_estimate_snapshot", "select_latest_pair.sql")
LATEST_STOCK_TRADES = read_sql("postgres", "stock_investor_trade_daily", "select_latest.sql")
LATEST_MARKET_FUNDS = read_sql("postgres", "krx_market_funds_daily", "select_latest_pair.sql")
LATEST_SHORT_POSITIONS = read_sql("postgres", "krx_stock_short_sale_daily", "select_latest_with_lending.sql")
SPREAD_PAIRS = read_sql("postgres", "indicator_observation", "select_spread_pairs.sql")

INTRADAY_SERIES = read_sql("postgres", "quote_bar", "select_intraday_series.sql")
DOMESTIC_STOCK_SERIES = read_sql("postgres", "stock_bar", "select_intraday_series.sql")

# 기술지표는 추론 툴과 같은 조회를 쓴다. 지수는 여기 이름으로, 종목은 watched 목록으로 온다.
TECHNICAL_HISTORY = read_sql("postgres", "technical", "select_history.sql")
RECENT_SIGNALS = read_sql("postgres", "technical_signal", "select_recent.sql")
TECHNICAL_INDEXES: tuple[str, ...] = ("KOSPI", "KOSDAQ")

# 표에 실을 신호의 나이 상한. 달력일이다 — 표시용 칸이라 하루 이틀 경계가 흔들려도 읽는
# 사람의 판단이 달라지지 않는다. 채점(문서 12.6절)은 반대로 영업일로 센다.
SIGNAL_LOOKBACK = timedelta(days=30)

# 사건 이름. `매수`·`매도` 낱말을 쓰지 않는다 — 표는 무슨 일이 있었는지만 말하고
# 그것이 좋은 신호였는지는 사후 수익률이 답한다.
SIGNAL_LABELS: dict[tuple[str, str], str] = {
    ("sma_cross", "up"): "골든크로스",
    ("sma_cross", "down"): "데드크로스",
    ("macd_cross", "up"): "MACD↑",
    ("macd_cross", "down"): "MACD↓",
    ("rsi_reversal", "up"): "RSI 과매도 탈출",
    ("rsi_reversal", "down"): "RSI 과매수 이탈",
}
# 국내 종목의 하루 가격제한폭보다 큰 단절은 분할·병합이나 원천 이상을 의심한다(문서 5.1절).
DOMESTIC_MAX_DAILY_CHANGE_PCT = 35.0

US_EASTERN = timezone("America/New_York")

# 국내 정규장. 장 상태 표시에만 쓴다.
SESSION_OPEN_HOUR_KST = 9
SESSION_CLOSE_MINUTE_KST = 15 * 60 + 45

# NXT 프리마켓 시작. 개장 발송의 차트 창이 여기서 시작해야 08:00~08:50 프리마켓 봉이 잡힌다.
NXT_PREMARKET_OPEN_HOUR_KST = 8

# 조회 구간. 봉은 휴일 연휴를 건너 마지막 값을 찾아야 하고, 금리는 월간 계열이 섞여 있어 넉넉히 본다.
#
# **연휴를 건너려면 10일이 필요하다.** 4일로 두었다가 2026-08-18(화) 실측에서 국내 시세가
# 통째로 비었다. 광복절 대체공휴일로 직전 거래일이 금요일이었고, 그 세션은 KST 15:30에
# 끝나는데 조회는 그보다 늦은 시각에 돌아 4일 창을 아슬하게 벗어났다. 설날·추석은 더 길다.
# 값이 오래됐다는 사실은 창을 좁혀 숨기는 것이 아니라 context 블록의 기준 시각이 알린다.
QUOTE_LOOKBACK = timedelta(days=10)
FLOW_LOOKBACK = timedelta(days=10)
RATE_LOOKBACK = timedelta(days=45)

# 국가 비교의 기준 만기. 나라마다 고시 만기가 달라 10년물만 두 나라 이상이 항상 갖는다.
TEN_YEAR_MONTHS = 120
GOVERNMENT_BOND = "government_bond"

# 장중 차트에 그리는 심볼. 이 순서가 이미지 순서다. 해외를 붙일 때도 이 목록만 늘린다.
# 지수·환율은 quote_bar 뷰로, DOMESTIC_STOCK_CHART_SYMBOLS에 있는 국내 종목은 NXT까지
# 보이도록 stock_bar 직접 조회로 나눠 읽는다(collect_chart_series).
CHART_SYMBOLS = (
    ("kis", "KOSPI"),
    ("kis", "KOSDAQ"),
    ("kis", "005930"),
    ("kis", "000660"),
    ("yahoo", "USDKRW"),
)

# CHART_SYMBOLS 중 stock_bar를 직접 읽어야 하는 국내 종목.
DOMESTIC_STOCK_CHART_SYMBOLS = frozenset({("kis", "005930"), ("kis", "000660")})

# 선을 그리는 데 필요한 최소 봉 수. 하나뿐이면 점만 찍혀 빈 차트나 다름없다.
MIN_CHART_POINTS = 2

# 일봉 보조지표 차트를 그리는 대상. **watched 목록을 그대로 쓰지 않는다** — 종목이 늘 때마다
# 이미지가 따라 늘어 한 통에 열 장이 실린다. 여기 적힌 것만 그린다.
#
# 환율(USDKRW)은 표에는 없고 차트에만 있다. 기술적 관측 표는 국내 지수·종목의 것이고,
# 환율은 일봉이 따로 쌓여(quote_daily의 fx_daily) 같은 봉 차트를 그릴 수 있다.
DAILY_CHART_SUBJECTS: tuple[str, ...] = ("KOSPI", "KOSDAQ", "005930", "000660", "USDKRW")

# 지표를 계산할 때 받는 봉 수. **표와 차트가 같은 값을 쓴다.** 둘이 다르면 같은 날 같은
# 지표가 표와 그림에서 소수점 아래부터 갈린다(120봉 대 240봉일 때 MACD 히스토그램이
# +6,816.85 대 +6,814.47이었다). EMA는 앞쪽 봉의 영향을 완전히 잃지 않기 때문이다.
#
# 500봉(약 2년)인 이유는 둘이다. 120일선이 표시 구간 왼쪽 끝부터 그려지려면 표시 봉 수의
# 두 배가 필요하고, 그보다 더 받는 만큼 EMA 초기값의 흔적이 옅어져 증권사 앱 값에 가까워진다.
#
# **`technical.TECHNICAL_LOOKBACK_BARS`(120)는 건드리지 않는다.** 그 값은 신호 DAG
# (`technical_signal_daily`)가 과거 교차를 훑는 창이라, 늘리면 이미 채점된 신호의 판정이
# 조용히 바뀐다. 브리핑이 보는 창과 신호가 세는 창은 목적이 다르다.
INDICATOR_HISTORY_BARS = 500

# 값이 어느 시장 것인지를 밝히는 표기. 거래소 열이 있는 국내 종목은 그 값(KRX·NXT)을 쓰고,
# 거래소 개념이 없는 지수·환율은 제공처를 적는다. **차트와 표는 값의 출처를 숨기지 않는다** —
# KRX 정규장 확정값과 NXT 애프터마켓 값이 같은 자리에 그려지면 읽는 사람이 가를 방법이 없다.
PROVIDER_VENUES: dict[str, str] = {"kis": "KRX", "yahoo": "Yahoo"}

# 금리 스프레드. (라벨, 왼쪽 다리, 오른쪽 다리)이고 값은 왼쪽-오른쪽을 bp로 그린다.
# 장단기 역전이면 음수가 그대로 보인다.
SPREAD_LEGS = (
    ("미국 10년-2년", ("fred", "DGS10"), ("fred", "DGS2")),
    ("한국 10년-2년", ("ecos", "KTB10Y"), ("ecos", "KTB2Y")),
    ("한미 10년", ("ecos", "KTB10Y"), ("fred", "DGS10")),
    ("미국 30년-10년", ("fred", "DGS30"), ("fred", "DGS10")),
    ("한국 30년-10년", ("ecos", "KTB30Y"), ("ecos", "KTB10Y")),
    ("한미 30년", ("ecos", "KTB30Y"), ("fred", "DGS30")),
)


class QuoteChange(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    symbol: str
    label: str
    kind: str
    country: str
    close: Decimal
    previous_close: Decimal
    bar_at: AwareDatetime
    # 봉이 체결된 거래소. 국내 종목(stock_bar 직접 조회)만 채우고 뷰에서 온 행은 None이다.
    # NXT 값이면 표 라벨에 밝힌다 — KRX 마감값과 애프터마켓 값이 구분돼야 한다.
    exchange: str | None = None

    @property
    def change_percent(self) -> float | None:
        # 분모가 0이면 등락을 계산할 수 없다. 0.0%로 지어내면 표에서 보합과 구별되지 않는다.
        # `_percent`가 `None`을 `-`로 찍어 "모른다"를 밝힌다(`FlowSnapshot`과 같은 형태다).
        if not self.previous_close:
            return None
        return float((self.close - self.previous_close) / self.previous_close * 100)


class RateChange(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    series_id: str
    country: str
    country_name: str
    label: str
    observation_date: date
    value: Decimal
    previous_value: Decimal | None = None
    previous_observation_date: date | None = None

    @property
    def change_bp(self) -> float | None:
        if self.previous_value is None:
            return None
        return float((self.value - self.previous_value) * 100)


class FlowSnapshot(BaseModel):
    """시장 하나의 투자자 순매수. `previous_*`는 **직전 거래일 마감 스냅샷**이다.

    같은 날 앞 슬롯이 아니다 — 이 값은 그날의 누적이라 앞 슬롯과 비교하면 "장중에 얼마나
    더 샀나"가 되고, 표가 말하려는 "어제와 견줘 어떤가"와 뜻이 다르다.
    """

    model_config = ConfigDict(frozen=True)

    market_code: str
    observed_at: AwareDatetime
    foreign_net_buy_amount: Decimal
    institution_net_buy_amount: Decimal
    individual_net_buy_amount: Decimal
    previous_session_date: date | None = None
    previous_foreign_net_buy_amount: Decimal | None = None
    previous_institution_net_buy_amount: Decimal | None = None
    previous_individual_net_buy_amount: Decimal | None = None


class StockFlowSnapshot(BaseModel):
    """종목 하나의 추정 순매수. **금액이 아니라 수량(주)이고 추정치다.**

    시장 수급(`FlowSnapshot`)과 단위가 달라 한 표에 섞지 않는다.
    """

    model_config = ConfigDict(frozen=True)

    stock_code: str
    label: str
    business_date: date
    foreign_net_buy_qty: int
    institution_net_buy_qty: int
    total_net_buy_qty: int
    collected_at: AwareDatetime
    previous_business_date: date | None = None
    previous_foreign_net_buy_qty: int | None = None
    previous_institution_net_buy_qty: int | None = None
    previous_total_net_buy_qty: int | None = None


class StockTradeSnapshot(BaseModel):
    """종목 하나의 장 마감 확정값. 종가와 12분류 수급 중 브리핑이 그리는 몫이다.

    추정(`StockFlowSnapshot`)과 달리 거래소가 마감 뒤 고시한 확정치다. 단위는 주(수량)이고
    KIS `kis_investor_trade_daily`가 KST 18:10에 채운다.
    """

    model_config = ConfigDict(frozen=True)

    stock_code: str
    label: str
    business_date: date
    close: Decimal
    previous_close: Decimal | None = None
    previous_business_date: date | None = None
    previous_foreign_net_buy_qty: int | None = None
    previous_institution_net_buy_qty: int | None = None
    previous_individual_net_buy_qty: int | None = None
    foreign_net_buy_qty: int
    institution_net_buy_qty: int
    individual_net_buy_qty: int
    securities_net_buy_qty: int
    investment_trust_net_buy_qty: int
    private_equity_net_buy_qty: int
    bank_net_buy_qty: int
    insurance_net_buy_qty: int
    merchant_bank_net_buy_qty: int
    pension_fund_net_buy_qty: int
    # 기관계 밖이다. 기관 세부 일곱의 합에 넣으면 기관계와 어긋난다.
    other_corporation_net_buy_qty: int
    other_organization_net_buy_qty: int
    previous_securities_net_buy_qty: int | None = None
    previous_investment_trust_net_buy_qty: int | None = None
    previous_private_equity_net_buy_qty: int | None = None
    previous_bank_net_buy_qty: int | None = None
    previous_insurance_net_buy_qty: int | None = None
    previous_merchant_bank_net_buy_qty: int | None = None
    previous_pension_fund_net_buy_qty: int | None = None
    previous_other_corporation_net_buy_qty: int | None = None
    previous_other_organization_net_buy_qty: int | None = None

    @property
    def change_percent(self) -> float | None:
        if self.previous_close is None or not self.previous_close:
            return None
        return float((self.close - self.previous_close) / self.previous_close * 100)


class MovementSnapshot(BaseModel):
    """시장 하나의 등락 종목 수. `previous_*`는 직전 거래일 마감 스냅샷이다(`FlowSnapshot`과 같다)."""

    model_config = ConfigDict(frozen=True)

    market_code: str
    observed_at: AwareDatetime
    rising_count: int
    unchanged_count: int
    falling_count: int
    previous_session_date: date | None = None
    previous_rising_count: int | None = None
    previous_unchanged_count: int | None = None
    previous_falling_count: int | None = None


class MarketFundsSnapshot(BaseModel):
    """증시자금 최신 영업일. 단위는 KIS 표기 그대로 억원이다.

    셋 다 전일 행과의 차이다. 고객예탁금은 API도 전일대비를 주지만 그 값이 저장된 두
    행의 차이와 1억원 어긋나는 날이 있어(반올림) 표가 `잔고 - 전일 대비 ≠ 전일`이 된다.
    전일 행이 없는 수집 첫날에만 API 값으로 떨어지고, 나머지 둘은 그때 `None`이다.
    """

    model_config = ConfigDict(frozen=True)

    business_date: date
    previous_business_date: date | None = None
    customer_deposit: Decimal
    customer_deposit_change: Decimal
    credit_loan_balance: Decimal
    credit_loan_change: Decimal | None = None
    unsettled_amount: Decimal
    unsettled_change: Decimal | None = None


class StockShortPositionSnapshot(BaseModel):
    """종목 하나의 공매도와 같은 날 대차 잔고. 대차는 공매도의 재고라 한 표에 그린다.

    대차 행이 아직 없으면 `None`이다. 비중은 KIS 표기 그대로의 퍼센트다.
    직전 값은 조회 창 안에 직전 **수집일** 행이 있을 때만 채워진다. 수집이 매일 도는
    것이 아니라 그 날짜가 전일이 아닐 수 있어 `previous_business_date`를 함께 들고 다닌다.
    """

    model_config = ConfigDict(frozen=True)

    stock_code: str
    label: str
    business_date: date
    short_sale_quantity: int
    short_sale_volume_ratio: Decimal
    previous_business_date: date | None = None
    previous_short_sale_quantity: int | None = None
    lending_balance_quantity: int | None = None
    previous_lending_balance_quantity: int | None = None


class RateSpread(BaseModel):
    """금리 스프레드 하나. 값은 bp이고 역전이면 음수다.

    전일 대비는 각 다리의 직전 관측 기준이라 두 나라 휴장이 어긋난 날에는 하루가
    밀린 값과 비교될 수 있다. `observed_on`은 두 다리 중 오래된 쪽 날짜다.
    """

    model_config = ConfigDict(frozen=True)

    label: str
    spread_bp: float
    change_bp: float | None = None
    observed_on: date


class RecentSignal(BaseModel):
    """대상 하나의 가장 최근 매매 신호. **사건이지 판정이 아니다.**"""

    model_config = ConfigDict(frozen=True)

    symbol: str
    signal_date: date
    kind: str
    direction: str

    @property
    def label(self) -> str:
        """표에 찍는 사건 이름. 모르는 조합은 원본 값을 그대로 보인다."""
        return SIGNAL_LABELS.get((self.kind, self.direction), f"{self.kind} {self.direction}")


class MarketSummary(BaseModel):
    """두 리포트가 함께 쓰는 집계 결과. 시각은 전부 UTC다."""

    model_config = ConfigDict(frozen=True)

    generated_at: AwareDatetime
    quotes: tuple[QuoteChange, ...] = ()
    rates: tuple[RateChange, ...] = ()
    flows: tuple[FlowSnapshot, ...] = ()
    stock_flows: tuple[StockFlowSnapshot, ...] = ()
    stock_trades: tuple[StockTradeSnapshot, ...] = ()
    movements: tuple[MovementSnapshot, ...] = ()
    funds: MarketFundsSnapshot | None = None
    short_positions: tuple[StockShortPositionSnapshot, ...] = ()
    spreads: tuple[RateSpread, ...] = ()
    technicals: tuple[technical.TechnicalSnapshot, ...] = ()
    signals: tuple[RecentSignal, ...] = ()


class ChartSeries(BaseModel):
    """장중 차트의 계열 하나. 당일 분봉 종가만 담는다.

    `venue`는 이 봉이 어느 시장 것인지다(`KRX`·`NXT`·`KRX·NXT`, 또는 거래소가 없는
    지수·환율의 제공처). 차트 제목이 그대로 찍는다 — 값만 있고 시장이 없으면 NXT
    애프터마켓 봉이 KRX 마감값처럼 읽힌다.
    """

    model_config = ConfigDict(frozen=True)

    provider: str
    symbol: str
    label: str
    venue: str
    points: tuple[tuple[AwareDatetime, Decimal], ...]


class DailyChartSeries(BaseModel):
    """일봉 차트 한 장에 들어갈 확정 일봉. 지표 계산은 그리는 쪽이 한다.

    `kind`는 `quote_symbol.kind`(`index`·`equity`·`fx` …)다. 무엇을 그릴지가 종류마다
    달라서 들고 다닌다 — 환율은 봉을 그리지 않는다(`chart.CANDLE_KINDS`).

    `venue`는 이 일봉이 어느 시장 것인지다. 국내 종목·지수 일봉은 KIS가 **KRX 정규장**
    기준으로 준다(NXT와 시간외는 들어 있지 않다). 차트 제목이 그대로 찍는다.
    """

    model_config = ConfigDict(frozen=True)

    subject_code: str
    label: str
    kind: str
    venue: str
    bars: tuple[technical.DailyBar, ...]


class MarketBriefingReader:
    """브리핑 한 통에 들어갈 값을 읽는다. 연결과 기준 시각을 들고 도는 것이 이 클래스의 상태다.

    **렌더링은 여기 없다.** `render_blocks`·`render_text`와 표 조립은 감쌀 상태가 없어 모듈
    함수로 남는다. 이 클래스가 쥐는 것은 DB 연결과 기준 시각뿐이고, 그 둘은 한 번의 발송
    동안 바뀌지 않는다.

    **생성자는 그 발송 동안 안 변하는 것만 받는다.** 창의 시작 시각(`open_hour`)이나 조회
    구간의 시작(`since`)처럼 호출마다 달라지는 값은 메서드 인자다.
    """

    def __init__(self, connection: Connection, now: datetime) -> None:
        self.connection = connection
        self.now = now

    def summary(self) -> MarketSummary:
        """브리핑 한 통에 들어갈 값을 전부 읽는다."""
        quotes = self._fetch(
            LATEST_QUOTES,
            (self.now - QUOTE_LOOKBACK,),
            lambda row: QuoteChange(
                provider=row[0],
                symbol=row[1],
                label=row[2],
                kind=row[3],
                country=row[4],
                close=row[5],
                previous_close=row[6],
                bar_at=row[7],
            ),
        )
        domestic_stocks = self._fetch(
            LATEST_DOMESTIC_STOCKS,
            (self.now - QUOTE_LOOKBACK,),
            lambda row: QuoteChange(
                provider=row[0],
                symbol=row[1],
                label=row[2],
                kind=row[3],
                country=row[4],
                close=row[5],
                previous_close=row[6],
                bar_at=row[7],
                exchange=row[8],
            ),
        )
        # 뷰가 준 국내 종목 행(KRX만)을 버리고 stock_bar 직접 조회(NXT 포함)로 바꾼다.
        quotes = (
            tuple(quote for quote in quotes if not (quote.provider == "kis" and quote.kind == "equity"))
            + domestic_stocks
        )
        rates = self._fetch(
            LATEST_RATES,
            (GOVERNMENT_BOND, TEN_YEAR_MONTHS, (self.now - RATE_LOOKBACK).date()),
            lambda row: RateChange(
                provider=row[0],
                series_id=row[1],
                country=row[2],
                country_name=row[3],
                label=row[4],
                observation_date=row[5],
                value=row[6],
                previous_value=row[7],
                previous_observation_date=row[8],
            ),
        )
        flows = self._fetch(
            LATEST_FLOWS,
            (self.now - FLOW_LOOKBACK,),
            lambda row: FlowSnapshot(
                market_code=row[0],
                observed_at=row[1],
                foreign_net_buy_amount=row[2],
                institution_net_buy_amount=row[3],
                individual_net_buy_amount=row[4],
                previous_session_date=row[5],
                previous_foreign_net_buy_amount=row[6],
                previous_institution_net_buy_amount=row[7],
                previous_individual_net_buy_amount=row[8],
            ),
        )
        stock_flows = self._fetch(
            LATEST_STOCK_FLOWS,
            ((self.now - FLOW_LOOKBACK).date(),),
            lambda row: StockFlowSnapshot(
                stock_code=row[0],
                label=row[1],
                business_date=row[2],
                foreign_net_buy_qty=row[3],
                institution_net_buy_qty=row[4],
                total_net_buy_qty=row[5],
                collected_at=row[6],
                previous_business_date=row[7],
                previous_foreign_net_buy_qty=row[8],
                previous_institution_net_buy_qty=row[9],
                previous_total_net_buy_qty=row[10],
            ),
        )
        stock_trades = self._fetch(
            LATEST_STOCK_TRADES,
            ((self.now - FLOW_LOOKBACK).date(),),
            lambda row: StockTradeSnapshot(
                stock_code=row[0],
                label=row[1],
                business_date=row[2],
                close=row[3],
                previous_close=row[4],
                previous_business_date=row[5],
                previous_foreign_net_buy_qty=row[6],
                previous_institution_net_buy_qty=row[7],
                previous_individual_net_buy_qty=row[8],
                foreign_net_buy_qty=row[9],
                institution_net_buy_qty=row[10],
                individual_net_buy_qty=row[11],
                securities_net_buy_qty=row[12],
                investment_trust_net_buy_qty=row[13],
                private_equity_net_buy_qty=row[14],
                bank_net_buy_qty=row[15],
                insurance_net_buy_qty=row[16],
                merchant_bank_net_buy_qty=row[17],
                pension_fund_net_buy_qty=row[18],
                other_corporation_net_buy_qty=row[19],
                other_organization_net_buy_qty=row[20],
                previous_securities_net_buy_qty=row[21],
                previous_investment_trust_net_buy_qty=row[22],
                previous_private_equity_net_buy_qty=row[23],
                previous_bank_net_buy_qty=row[24],
                previous_insurance_net_buy_qty=row[25],
                previous_merchant_bank_net_buy_qty=row[26],
                previous_pension_fund_net_buy_qty=row[27],
                previous_other_corporation_net_buy_qty=row[28],
                previous_other_organization_net_buy_qty=row[29],
            ),
        )
        movements = self._fetch(
            LATEST_MOVEMENTS,
            (self.now - FLOW_LOOKBACK,),
            lambda row: MovementSnapshot(
                market_code=row[0],
                observed_at=row[1],
                rising_count=row[2],
                unchanged_count=row[3],
                falling_count=row[4],
                previous_session_date=row[5],
                previous_rising_count=row[6],
                previous_unchanged_count=row[7],
                previous_falling_count=row[8],
            ),
        )
        funds = self._market_funds()
        # 당일(KST)은 제외한다. KIS가 장중에 당일 공매도 행을 0으로 보낸다(SQL 주석 참고).
        short_positions = self._fetch(
            LATEST_SHORT_POSITIONS,
            ((self.now - FLOW_LOOKBACK).date(), self.now.astimezone(KST_TIMEZONE).date()),
            lambda row: StockShortPositionSnapshot(
                stock_code=row[0],
                label=row[1],
                business_date=row[2],
                previous_business_date=row[3],
                short_sale_quantity=row[4],
                previous_short_sale_quantity=row[5],
                short_sale_volume_ratio=row[6],
                lending_balance_quantity=row[7],
                previous_lending_balance_quantity=row[8],
            ),
        )
        spreads = self._rate_spreads((self.now - RATE_LOOKBACK).date())
        technicals = self._technicals()
        signals = self._fetch(
            RECENT_SIGNALS,
            {"since_date": (self.now - SIGNAL_LOOKBACK).astimezone(KST_TIMEZONE).date()},
            lambda row: RecentSignal(symbol=row[0], signal_date=row[1], kind=row[2], direction=row[3]),
        )

        return MarketSummary(
            generated_at=self.now,
            quotes=quotes,
            rates=rates,
            flows=flows,
            stock_flows=stock_flows,
            stock_trades=stock_trades,
            movements=movements,
            funds=funds,
            short_positions=short_positions,
            spreads=spreads,
            technicals=technicals,
            signals=signals,
        )

    def chart_series(self, open_hour: int = SESSION_OPEN_HOUR_KST) -> tuple[ChartSeries, ...]:
        """장중 차트에 그릴 당일 분봉. `CHART_SYMBOLS` 순서를 지킨다.

        봉이 없는 심볼은 계열 자체를 만들지 않는다. 개장 전이나 갓 붙인 심볼 때문에
        리포트가 죽으면 안 된다(`*_trends`와 같은 원칙). 전부 비면 차트를 생략한다.

        **봉이 하나뿐이어도 뺀다.** 점 하나로는 선을 그릴 수 없어 빈 것과 다름없다.
        09:00 개장 발송 직후의 코스피·코스닥이 그 경우다 — 장이 막 열려 분봉이 하나뿐이고,
        나머지 시세(종가·등락)는 위의 시세 표가 이미 보여 준다.

        `open_hour`는 창의 시작 시각(KST)이다. 개장 발송은 `NXT_PREMARKET_OPEN_HOUR_KST`를
        넘겨 프리마켓 봉을 잡는다. 그 시각에 봉이 없는 지수는 위 규칙대로 빠진다.
        """
        local = self.now.astimezone(KST_TIMEZONE)
        session_open = local.replace(hour=open_hour, minute=0, second=0, microsecond=0)
        # 국내 종목은 NXT 봉까지 보이도록 stock_bar를 직접 읽고 나머지는 뷰로 읽는다.
        view_symbols = [pair for pair in CHART_SYMBOLS if pair not in DOMESTIC_STOCK_CHART_SYMBOLS]
        stock_symbols = [pair for pair in CHART_SYMBOLS if pair in DOMESTIC_STOCK_CHART_SYMBOLS]
        # 국내 종목 행만 거래소 열이 하나 더 있다. 뷰 행은 None으로 맞춰 한 모양으로 다룬다.
        rows = []
        with self.connection.cursor() as cursor:
            for statement, pairs in ((INTRADAY_SERIES, view_symbols), (DOMESTIC_STOCK_SERIES, stock_symbols)):
                if not pairs:
                    continue
                providers = sorted({provider for provider, _ in pairs})
                symbols = [symbol for _, symbol in pairs]
                cursor.execute(statement, (providers, symbols, session_open))
                fetched = cursor.fetchall()
                if statement is INTRADAY_SERIES:
                    fetched = [(*row, None) for row in fetched]
                rows.extend(fetched)

        grouped: dict[tuple[str, str], list[tuple[datetime, Decimal]]] = {}
        labels: dict[tuple[str, str], str] = {}
        exchanges: dict[tuple[str, str], set[str]] = {}
        for provider, symbol, label, bar_at, close, exchange in rows:
            grouped.setdefault((provider, symbol), []).append((bar_at, close))
            labels[(provider, symbol)] = label
            if exchange:
                exchanges.setdefault((provider, symbol), set()).add(exchange)

        series = []
        for provider, symbol in CHART_SYMBOLS:
            points = grouped.get((provider, symbol))
            if not points or len(points) < MIN_CHART_POINTS:
                continue
            # 어느 거래소 봉인지 밝힌다. 프리마켓·야간은 NXT, 정규장은 KRX, 하루가 섞이면
            # KRX·NXT다. 거래소 개념이 없는 지수·환율은 제공처를 적는다.
            marks = exchanges.get((provider, symbol))
            venue = "·".join(sorted(marks)) if marks else PROVIDER_VENUES.get(provider, provider)
            series.append(
                ChartSeries(
                    provider=provider,
                    symbol=symbol,
                    label=labels[(provider, symbol)],
                    venue=venue,
                    points=tuple(points),
                )
            )
        return tuple(series)

    def daily_chart_series(self) -> tuple[DailyChartSeries, ...]:
        """일봉 보조지표 차트에 그릴 계열. `DAILY_CHART_SUBJECTS` 순서를 지킨다.

        지표를 낼 만큼 봉이 없는 대상은 뺀다. 표가 그 대상을 그리지 않는 것과 같은 기준이다
        (`technical.TECHNICAL_MIN_BARS`).
        """
        subjects = self._daily_bars(DAILY_CHART_SUBJECTS, include_watched=False, limit=INDICATOR_HISTORY_BARS)
        available = {subject.subject_code: subject for subject in subjects}
        return tuple(
            subject
            for code in DAILY_CHART_SUBJECTS
            if (subject := available.get(code)) is not None and len(subject.bars) >= technical.TECHNICAL_MIN_BARS
        )

    def _daily_bars(self, symbols: Sequence[str], *, include_watched: bool, limit: int) -> tuple[DailyChartSeries, ...]:
        """확정 일봉을 대상별로 묶어 돌려준다. 표(기술지표)와 일봉 차트가 같은 조회를 쓴다.

        무엇을 받을지는 부르는 쪽이 정한다. 표는 지수 + watched 종목이고, 차트는
        `DAILY_CHART_SUBJECTS`에 적힌 것뿐이다(환율이 거기 더 있다).
        """
        with self.connection.cursor() as cursor:
            cursor.execute(
                TECHNICAL_HISTORY,
                {
                    "symbols": list(symbols),
                    "include_watched": include_watched,
                    "as_of_at": self.now,
                    "limit": limit,
                },
            )
            rows = list(cursor.fetchall())

        grouped: dict[str, list[Any]] = {}
        for row in rows:
            grouped.setdefault(row[1], []).append(row)

        subjects = []
        for symbol, subject_rows in grouped.items():
            # 조회는 최신순이고 계산기는 오름차순을 받는다.
            ascending = list(reversed(subject_rows))
            subjects.append(
                DailyChartSeries(
                    subject_code=symbol,
                    label=str(ascending[0][2] or symbol),
                    kind=str(ascending[0][3]),
                    venue=PROVIDER_VENUES.get(str(ascending[0][0]), str(ascending[0][0])),
                    bars=tuple(
                        technical.DailyBar(
                            business_date=row[5],
                            open=float(row[6]),
                            high=float(row[7]),
                            low=float(row[8]),
                            close=float(row[9]),
                            volume=None if row[10] is None else int(row[10]),
                        )
                        for row in ascending
                    ),
                )
            )
        return tuple(subjects)

    def _technicals(self) -> tuple[technical.TechnicalSnapshot, ...]:
        """지수와 watched 종목의 기술지표. 조회 한 번으로 전부 받아 대상별로 계산한다.

        지표를 못 내는 대상은 결과에서 빠진다. **0으로 채우지 않는다** — 표에 줄이 없는 것이
        "아직 표본이 모자라다"를 말하는 방법이다.
        """
        snapshots = []
        for subject in self._daily_bars(TECHNICAL_INDEXES, include_watched=True, limit=INDICATOR_HISTORY_BARS):
            snapshot = technical.summarize(
                subject.subject_code,
                subject.label,
                subject.bars,
                max_abs_daily_change_pct=DOMESTIC_MAX_DAILY_CHANGE_PCT,
            )
            if snapshot is not None:
                snapshots.append(snapshot)
        return tuple(snapshots)

    def _market_funds(self) -> MarketFundsSnapshot | None:
        """증시자금 최신 영업일. 전일 행이 있으면 세 항목의 증감을 전일 행과의 차이로 계산한다."""
        with self.connection.cursor() as cursor:
            cursor.execute(LATEST_MARKET_FUNDS, ())
            rows = cursor.fetchall()
        if not rows:
            return None
        latest = rows[0]
        previous = rows[1] if len(rows) > 1 else None
        return MarketFundsSnapshot(
            business_date=latest[0],
            previous_business_date=previous[0] if previous else None,
            customer_deposit=latest[1],
            # 전일 행이 있으면 그 차이를 쓴다. API가 준 전일대비(latest[2])는 수집 첫날의 대비책이다.
            customer_deposit_change=latest[1] - previous[1] if previous else latest[2],
            credit_loan_balance=latest[3],
            credit_loan_change=latest[3] - previous[3] if previous else None,
            unsettled_amount=latest[4],
            unsettled_change=latest[4] - previous[4] if previous else None,
        )

    def _rate_spreads(self, since: date) -> tuple[RateSpread, ...]:
        """`SPREAD_LEGS`의 스프레드. 다리 한쪽이라도 관측값이 없으면 그 스프레드는 만들지 않는다."""
        providers = sorted({provider for _, *legs in SPREAD_LEGS for provider, _ in legs})
        series_ids = sorted({series_id for _, *legs in SPREAD_LEGS for _, series_id in legs})
        with self.connection.cursor() as cursor:
            cursor.execute(SPREAD_PAIRS, (providers, series_ids, since))
            rows = cursor.fetchall()
        observed = {(row[0], row[1]): (row[2], row[3], row[4]) for row in rows}

        spreads = []
        for label, left_key, right_key in SPREAD_LEGS:
            left = observed.get(left_key)
            right = observed.get(right_key)
            if left is None or right is None:
                continue
            spread_bp = float((left[1] - right[1]) * 100)
            change_bp = None
            if left[2] is not None and right[2] is not None:
                change_bp = spread_bp - float((left[2] - right[2]) * 100)
            spreads.append(
                RateSpread(label=label, spread_bp=spread_bp, change_bp=change_bp, observed_on=min(left[0], right[0]))
            )
        return tuple(spreads)

    def _fetch(self, statement: str, parameters: tuple | dict[str, Any], build: Callable[[Any], Any]) -> tuple:
        """psycopg는 위치(tuple)와 이름(dict) 파라미터를 둘 다 받는다. 문장이 쓰는 쪽을 그대로 넘긴다."""
        with self.connection.cursor() as cursor:
            cursor.execute(statement, parameters)
            return tuple(build(row) for row in cursor.fetchall())


def session_state(now: datetime) -> str:
    """국내 정규장 기준 장 상태. 표시용이다."""
    local = now.astimezone(KST_TIMEZONE)
    minutes = local.hour * 60 + local.minute
    if minutes < SESSION_OPEN_HOUR_KST * 60:
        return "개장 전"
    return "장중" if minutes <= SESSION_CLOSE_MINUTE_KST else "마감 후"


def us_session_date(now: datetime) -> date:
    """이 시각에 막 끝난 미국 세션의 날짜.

    **KST 날짜로 물으면 안 된다.** 미국 정규장은 KST로 전날 22:30에 시작해 당일 05:00에
    끝나므로, 뉴욕 시계로 봐야 세션 하나가 한 날짜에 담긴다.
    """
    return now.astimezone(US_EASTERN).date()
