"""시장 브리핑의 조회·렌더링.

리포트는 둘인데 파일은 하나다. 한국장과 미국장은 **같은 표를 다른 시간대에 다른 조합으로**
보여 주는 것이라 섹션 구성이 크게 겹친다. 파일을 나누면 사본만 생긴다. 무엇을 어느 리포트에
넣을지는 `MarketScope`가 정한다.

조회도 한 번만 한다. 심볼이 수십 개, 계열이 십여 개라 리포트마다 쿼리를 좁히는 값어치가 없다.
`MarketBriefingReader`가 전부 받아 오고 렌더링이 고른다. 그 덕에 미국장 리포트는 밤사이
미국 값과 전일 한국 값을 **한 화면에서** 보여 준다. 그게 이 리포트를 만드는 이유다.

**조회는 클래스이고 렌더링은 함수다.** 연결과 기준 시각은 한 번의 발송 동안 바뀌지 않는
상태라 `MarketBriefingReader`가 들고 돌고, 표·블록 조립은 감쌀 상태가 없어 모듈 함수로 둔다
(`.codex/AGENTS.md`의 "클래스와 함수를 가르는 기준").

LLM 요약은 넣지 않는다. 2026-08-19까지 표 위에 모델 요약을 붙였지만 표가 이미 말하는
것 이상을 쓰지 못해 뺐다(요약 입력을 만들던 `comment_input`과 추세 계산도 함께 걷어냈다).

**시각은 UTC로 담고 KST 변환은 렌더링에서만 한다.** Slack은 프론트엔드가 없는 출력이라
백엔드가 변환하는 자리이고, 미국 세션 날짜만 `America/New_York` 기준으로 뽑는다.
"""

from collections.abc import Callable, Iterable, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pendulum import timezone
from pydantic import AwareDatetime, BaseModel, ConfigDict

from modules import technical
from modules.briefing import blocks
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

# 한국장 시간에도 값이 움직이는 해외 시장. 미국 현물은 닫혀 있어 넣지 않는다.
ASIA_COUNTRIES = frozenset({"JP", "TW", "HK", "CN"})
INDEX_FUTURE = "index_future"

# 표에 그리는 순서. **정렬을 SQL에 맡기지 않는다.** 이름순으로 두면 코스닥이 코스피 위에
# 오고 통화가 CNY부터 시작한다. 읽는 사람이 먼저 보고 싶은 것과 가나다·알파벳 순서는 다르다.
# 목록에 없는 값은 뒤로 밀리고 자기들끼리는 원래 순서를 지킨다.
KOREA_SYMBOL_ORDER = ("KOSPI", "KOSPI200", "KOSPI200_FUT", "KOSDAQ", "KOSDAQ150_FUT")
OVERSEAS_COUNTRY_ORDER = ("US", "JP", "CN", "HK", "TW")

# 미국장 지수·선물 표의 줄 순서. 현물 옆에 그 선물을 놓는다. 목록에 없는 심볼은 뒤로 밀린다.
US_SYMBOL_ORDER = (
    "SP500",
    "SP500_FUT",
    "NASDAQ",
    "NASDAQ100_FUT",
    "DOW_FUT",
    "RUSSELL2000",
    "RUSSELL2000_FUT",
    "SOX",
    "VIX",
    "US10Y_FUT",
)

# 미국장 리포트의 시세 표. (제목, 그 표에 넣는 kind). 한 표에 섞어 두면 금·나스닥·비트코인이
# 한 덩어리로 보여서(2026-08-22 전) 표를 종류별로 가른다. 빈 표는 그리지 않는다.
# kind의 합집합은 QUOTED_KINDS와 같아야 한다 — 테스트가 대조한다.
US_SECTIONS = (
    ("미국 지수·선물", frozenset({"index", "index_future", "bond_future"})),
    ("원자재", frozenset({"commodity"})),
    ("크립토", frozenset({"crypto"})),
    ("ADR", frozenset({"equity"})),
)

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

# 실시간 환율 표의 줄 순서. 목록에 없는 fx 심볼(USDJPY 등)은 뒤로 밀린다.
FX_SYMBOL_ORDER = ("USDKRW", "JPYKRW", "DXY")

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

# 시세 표에 그리는 종류. 등락을 퍼센트로 그려도 뜻이 통하는 것만 넣는다.
#
# **`rate`를 넣지 않는다.** 금리를 퍼센트 변화로 그리면 4.65 → 4.70이 `+1.08%`가 되어
# 5bp 움직임이 1% 넘게 뛴 것처럼 보인다. 금리는 `indicator_observation` 쪽 표가 bp로 그린다.
# `fx`도 넣지 않는다. 환율은 실시간 환율 표(`_fx_quotes`)가 따로 그린다.
QUOTED_KINDS = frozenset({"index", "index_future", "equity", "commodity", "bond_future", "crypto"})


class MarketScope(StrEnum):
    """어느 리포트인가. 조회는 같고 무엇을 그릴지가 다르다.

    `KOREA_PREOPEN`은 개장 전 발송(08:10·09:00)이다. 08:00 미국장 리포트가 이미 보낸 것
    (미국 지수·선물, 금리·스프레드, 전일 국내 지수·선물, 수급)은 빼고, NXT 프리마켓
    (08:00~08:50)이 만든 종목 시세와 전일 확정치(증시자금·공매도·등락 종목 수)만 그린다.
    """

    KOREA = "korea"
    KOREA_PREOPEN = "korea_preopen"
    US = "us"
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
    def change_percent(self) -> float:
        if not self.previous_close:
            return 0.0
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


def render_blocks(
    summary: MarketSummary,
    scope: MarketScope,
    *,
    chart_files: Sequence[tuple[str, str]] | None = None,
    chart_error: str | None = None,
):
    """Slack 블록. 값은 Slack 기본 `table` 블록에 넣는다(`blocks` 모듈 docstring 참고)."""
    local = summary.generated_at.astimezone(KST_TIMEZONE)
    if scope is MarketScope.KOREA_PREOPEN:
        rendered = [
            blocks.header(f"🌅 한국장 프리마켓 브리핑 · {blocks.timestamp(local)}"),
            *_quote_section("국내 종목(프리마켓)", _domestic_stocks(summary)),
            *_chart_section(chart_files, chart_error),
            *_technical_section(summary),
            *_quote_section("환율(실시간·장외)", _fx_quotes(summary)),
            *_market_funds_section(summary),
            *_short_position_section(summary),
            *_movement_section(summary),
        ]
    elif scope is MarketScope.KOREA:
        rendered = [
            blocks.header(f"📈 한국장 브리핑 · {blocks.timestamp(local)} · {session_state(summary.generated_at)}"),
            *_quote_section("국내 지수·선물", _korea_quotes(summary)),
            *_chart_section(chart_files, chart_error),
            *_technical_section(summary),
            *_quote_section("장중 해외", _intraday_overseas(summary)),
            *_quote_section("환율(실시간·장외)", _fx_quotes(summary)),
            *_flow_section(summary),
            *_stock_flow_section(summary),
            *_stock_trade_sections(summary),
            *_market_funds_section(summary),
            *_short_position_section(summary),
            *_movement_section(summary),
        ]
    else:
        session = us_session_date(summary.generated_at)
        rendered = [
            blocks.header(f"🌙 미국장 마감 · {session:%m/%d}(현지) · {blocks.timestamp(local)}"),
            *[block for title, quotes in _us_quote_sections(summary) for block in _quote_section(title, quotes)],
            *_rate_section(summary),
            *_rate_spread_section(summary),
            *_quote_section("전일 국내", _korea_quotes(summary)),
            *_flow_section(summary),
            *_stock_flow_section(summary),
        ]
    rendered.append(blocks.context(_as_of(summary, scope)))
    return rendered


def render_text(summary: MarketSummary, scope: MarketScope) -> str:
    """블록을 못 그리는 자리에 뜨는 한 줄. 알림 미리보기가 이걸 읽는다."""
    quotes = _scope_quotes(summary, scope)
    parts = [f"{quote.label} {_number(quote.close)} {_percent(quote.change_percent)}" for quote in quotes[:2]]
    if scope is MarketScope.US:
        parts += [f"{rate.label} {_rate(rate.value)}" for rate in summary.rates if rate.country == "US"][:1]
    titles = {
        MarketScope.KOREA: "한국장 브리핑",
        MarketScope.KOREA_PREOPEN: "한국장 프리마켓 브리핑",
        MarketScope.US: "미국장 마감",
    }
    title = titles[scope]
    return f"{title} · " + " · ".join(parts) if parts else f"{title} · 값 없음"


def _scope_quotes(summary: MarketSummary, scope: MarketScope) -> tuple[QuoteChange, ...]:
    """리포트 첫 표에 실리는 시세. 미리보기 한 줄과 footer의 '가장 오래된 값'이 같은 것을 본다."""
    if scope is MarketScope.US:
        return _us_quotes(summary)
    if scope is MarketScope.KOREA_PREOPEN:
        return _domestic_stocks(summary)
    return _korea_quotes(summary)


def _us_listed_adr(quote: QuoteChange) -> bool:
    """미국 상장 ADR인가. Yahoo로 받는 종목(equity)은 전부 미국 상장 ADR이다(수집기 주석 참고).

    `country`는 회사 국적(TSMC=TW, SK하이닉스=KR)이라 거래 세션을 말해 주지 않는다.
    국적으로 거르면 ADR이 국내 표나 아시아 표에 섞여 뉴욕 마감값이 장중 값처럼 보인다.
    """
    return quote.provider == "yahoo" and quote.kind == "equity"


def _korea_quotes(summary: MarketSummary) -> tuple[QuoteChange, ...]:
    return _ordered(
        (
            quote
            for quote in summary.quotes
            if quote.country == "KR" and quote.kind in QUOTED_KINDS and not _us_listed_adr(quote)
        ),
        lambda quote: quote.symbol,
        KOREA_SYMBOL_ORDER,
    )


def _us_quote_sections(summary: MarketSummary) -> tuple[tuple[str, tuple[QuoteChange, ...]], ...]:
    """미국장 리포트의 시세 표들. `US_SECTIONS` 순서로 (제목, 줄)을 돌려준다.

    미국 심볼·크립토·미국 상장 ADR이 대상이다. 크립토는 country가 `XX`(나라 없음)라 종류로
    넣는다. 지수·선물 표만 `US_SYMBOL_ORDER`로 줄을 세우고 나머지는 SQL 순서(심볼 이름순)다.
    """
    candidates = [
        quote
        for quote in summary.quotes
        if quote.kind in QUOTED_KINDS and (quote.country == "US" or quote.kind == "crypto" or _us_listed_adr(quote))
    ]
    return tuple(
        (
            title,
            _ordered(
                (quote for quote in candidates if quote.kind in kinds),
                lambda quote: quote.symbol,
                US_SYMBOL_ORDER,
            ),
        )
        for title, kinds in US_SECTIONS
    )


def _us_quotes(summary: MarketSummary) -> tuple[QuoteChange, ...]:
    """미국장 표 전부를 표 순서대로 편 것. 미리보기 한 줄과 footer가 이걸 본다."""
    return tuple(quote for _title, quotes in _us_quote_sections(summary) for quote in quotes)


def _intraday_overseas(summary: MarketSummary) -> tuple[QuoteChange, ...]:
    """한국장 시간에도 값이 움직이는 해외 심볼.

    미국은 선물만 넣는다. 현물 지수는 이 시간에 닫혀 있어 어제 종가를 오늘 값처럼 보이게 한다.
    미국 상장 ADR도 같은 이유로 뺀다. country가 아시아(TW·KR)라도 거래는 뉴욕이다.
    크립토는 24시간 거래라 항상 실시간이다. country가 나라가 아닌 `XX`라 뒤로 밀린다.
    """
    return _ordered(
        (
            quote
            for quote in summary.quotes
            if quote.kind in QUOTED_KINDS
            and not _us_listed_adr(quote)
            and (
                (quote.country == "US" and quote.kind == INDEX_FUTURE)
                or quote.country in ASIA_COUNTRIES
                or quote.kind == "crypto"
            )
        ),
        lambda quote: quote.country,
        OVERSEAS_COUNTRY_ORDER,
    )


def _domestic_stocks(summary: MarketSummary) -> tuple[QuoteChange, ...]:
    """국내 개별 종목만. `collect_summary`가 stock_bar 직접 조회로 바꿔 둔 행이라 NXT 봉을 담는다."""
    return tuple(quote for quote in summary.quotes if quote.provider == "kis" and quote.kind == "equity")


def _fx_quotes(summary: MarketSummary) -> tuple[QuoteChange, ...]:
    """장외 실시간 환율. 은행 고시가 아니라 장중에 움직이는 시장 값이다."""
    return _ordered(
        (quote for quote in summary.quotes if quote.kind == "fx"),
        lambda quote: quote.symbol,
        FX_SYMBOL_ORDER,
    )


def _quote_section(title: str, quotes: Sequence[QuoteChange]) -> list[dict[str, Any]]:
    """시세 표.

    **기준 시각을 행마다 적는다.** 심볼마다 마지막 봉 시각이 다르다. 국내는 KRX 마감,
    해외 선물은 몇 분 전 값이라 한 표 안에서 며칠 차이가 나기도 한다. 표 밖에 대표 시각
    하나만 두면 묵은 줄이 최신처럼 보인다.

    **거래소를 아는 행이 하나라도 있으면 거래소 열을 넣는다.** 국내 종목은 같은 분에도
    KRX·NXT 값이 다르므로 어느 거래소 봉인지 행에 보여야 한다(차트 라벨과 같은 이유).
    거래소 개념이 없는 지수·환율·해외 표는 열 자체가 없다.
    """
    if not quotes:
        return []
    # 전일 종가는 봉에 실려 오는 값이라 그 자체에 날짜가 없다. 그래서 `직전 기준` 열을 두지
    # 않고 열 이름으로 뜻을 닫는다 — 다른 표의 `직전`이 직전 **수집일**인 것과 다르다.
    if any(quote.exchange for quote in quotes):
        rows = [
            (
                quote.label,
                _number(quote.close),
                _number(quote.previous_close),
                _percent(quote.change_percent),
                quote.exchange or "-",
                _day_stamp(quote.bar_at),
            )
            for quote in quotes
        ]
        return blocks.table_section(title, ("구분", "종가", "전일 종가", "등락", "거래소", "기준"), rows)
    rows = [
        (
            quote.label,
            _number(quote.close),
            _number(quote.previous_close),
            _percent(quote.change_percent),
            _day_stamp(quote.bar_at),
        )
        for quote in quotes
    ]
    return blocks.table_section(title, ("구분", "종가", "전일 종가", "등락", "기준"), rows)


def _chart_section(files: Sequence[tuple[str, str]] | None, error: str | None) -> list[dict[str, Any]]:
    """차트 이미지. 계열마다 image 블록 하나다(`(file_id, label)`). **실패는 채널에 남긴다.**

    당일 분봉과 확정 일봉 보조지표가 이 목록에 섞여 온다. 어느 쪽인지는 부르는 쪽이 라벨에
    담는다 — 블록 모양이 같아 여기서 가를 것이 없다.

    조용히 빠지면 차트가 원래 없는 리포트와 구분되지 않는다(요약 실패와 같은 원칙).
    둘 다 없으면 개장 전처럼 그릴 봉이 없는 정상 흐름이라 아무 것도 그리지 않는다.
    """
    if files:
        return [blocks.image(file_id, f"{label} 차트") for file_id, label in files]
    if error:
        return [blocks.context([f"⚠️ 차트 생성 실패: {error}"])]
    return []


def _rate_section(summary: MarketSummary) -> list[dict[str, Any]]:
    if not summary.rates:
        return []
    # 금리는 등락률(%)을 쓰지 않는다. 4.65 → 4.70이 `+1.08%`가 되어 5bp 움직임이 1% 넘게
    # 뛴 것처럼 보인다(QUOTED_KINDS 주석과 같은 이유). 대신 직전 값과 그 관측일을 적는다.
    rows = [
        (
            rate.label,
            _rate(rate.value),
            "-" if rate.previous_value is None else _rate(rate.previous_value),
            _basis_points(rate.change_bp),
            "-" if rate.previous_observation_date is None else f"{rate.previous_observation_date:%m/%d}",
            f"{rate.observation_date:%m/%d}",
        )
        for rate in summary.rates
    ]
    return blocks.table_section("주요국 10년 금리", ("국가", "금리", "직전", "직전 대비", "직전 기준", "기준"), rows)


def _rate_spread_section(summary: MarketSummary) -> list[dict[str, Any]]:
    """금리 스프레드. 역전(음수)이 그대로 보이도록 부호를 항상 붙인다."""
    if not summary.spreads:
        return []
    rows = [
        (
            spread.label,
            f"{spread.spread_bp:+,.0f}bp",
            _basis_points(spread.change_bp),
            f"{spread.observed_on:%m/%d}",
        )
        for spread in summary.spreads
    ]
    return blocks.table_section("금리 스프레드", ("구분", "스프레드", "전일 대비", "기준"), rows)


def _market_funds_section(summary: MarketSummary) -> list[dict[str, Any]]:
    """증시자금. 단위는 KIS 표기 그대로 억원이다. 전일 확정치라 기준일을 행마다 적는다."""
    if summary.funds is None:
        return []
    funds = summary.funds
    stamp = f"{funds.business_date:%m/%d}"
    previous_day = funds.previous_business_date
    entries = (
        ("고객예탁금", funds.customer_deposit, funds.customer_deposit_change),
        ("신용융자 잔고", funds.credit_loan_balance, funds.credit_loan_change),
        ("미수금", funds.unsettled_amount, funds.unsettled_change),
    )
    rows = []
    for name, value, change in entries:
        delta = _delta(value, _previous(value, change), previous_day)
        rows.append((name, f"{value:,.0f}", delta.previous, delta.change, delta.rate, delta.stamp, stamp))
    return blocks.table_section(
        "증시자금(억원)", ("구분", "잔고", "직전", "직전 대비", "등락률", "직전 기준", "기준"), rows
    )


def _technical_section(summary: MarketSummary) -> list[dict[str, Any]]:
    """확정 일봉 기술지표. **수치와 기준일만 말한다.**

    상승·하락·매수·매도 같은 판정 열을 두지 않는다. 방향은 `thesis`가 확률로 내고 채점을
    받는다. 여기서 판정을 흉내 내면 채점 없는 신호가 브리핑에 실린다.
    """
    if not summary.technicals:
        return []
    latest = {signal.symbol: signal for signal in summary.signals}
    rows = [
        (
            snapshot.label,
            _ratio_percent(snapshot.close, snapshot.sma20),
            _ratio_percent(snapshot.sma20, snapshot.sma60),
            f"{snapshot.rsi14:.1f}",
            f"{snapshot.macd_histogram:+,.2f}",
            "-" if snapshot.volume_ratio20 is None else f"{snapshot.volume_ratio20:.2f}x",
            _signal_label(latest.get(snapshot.subject_code)),
            f"{snapshot.as_of_date:%m/%d}",
        )
        for snapshot in summary.technicals
    ]
    return blocks.table_section(
        "기술적 관측(확정 일봉·KRX)",
        ("대상", "종가/20일선", "20일선/60일선", "RSI(14일)", "MACD 히스토그램", "거래량/20일평균", "신호", "기준"),
        rows,
    )


def _signal_label(signal: RecentSignal | None) -> str:
    """사건 이름과 발생일. 최근 창에 아무 것도 없으면 `-`다."""
    if signal is None:
        return "-"
    return f"{signal.label} {signal.signal_date:%m/%d}"


def _ratio_percent(numerator: float, denominator: float) -> str:
    """`(왼쪽 / 오른쪽 - 1) × 100`. 이동평균 위인지 아래인지를 한 칸으로 읽는다."""
    if denominator == 0:
        return "-"
    return f"{(numerator / denominator - 1) * 100:+.2f}%"


def _short_position_section(summary: MarketSummary) -> list[dict[str, Any]]:
    """종목 공매도와 대차 잔고. 대차는 공매도의 재고라 한 표에 그린다. 단위는 주다."""
    if not summary.short_positions:
        return []
    rows = []
    for position in summary.short_positions:
        short = _delta(
            position.short_sale_quantity, position.previous_short_sale_quantity, position.previous_business_date
        )
        lending = _delta(
            position.lending_balance_quantity,
            position.previous_lending_balance_quantity,
            position.previous_business_date,
        )
        rows.append(
            (
                position.label,
                f"{position.short_sale_volume_ratio:.2f}%",
                f"{position.short_sale_quantity:,}",
                short.previous,
                short.rate,
                f"{position.lending_balance_quantity:,}" if position.lending_balance_quantity is not None else "-",
                lending.previous,
                lending.rate,
                short.stamp,
                f"{position.business_date:%m/%d}",
            )
        )
    return blocks.table_section(
        "공매도·대차(주·KRX)",
        (
            "종목",
            "공매도 비중",
            "공매도 수량",
            "직전 공매도",
            "공매도 등락률",
            "대차 잔고",
            "직전 대차",
            "대차 등락률",
            "직전 기준",
            "기준",
        ),
        rows,
    )


# `market_investor_flow_snapshot`의 순매수 대금은 **백만원 단위**다(KIS `*_ntby_tr_pbmn`).
# 억원으로 줄이려면 1억이 아니라 100으로 나눈다. 원 단위로 착각해 1억으로 나누면 수천억짜리
# 값이 전부 `-0`이 되어 표와 요약 입력이 동시에 거짓말을 한다.
MILLIONS_PER_HUNDRED_MILLION = 100


def _flow_section(summary: MarketSummary) -> list[dict[str, Any]]:
    if not summary.flows:
        return []
    rows = [
        (
            flow.market_code,
            _amount(flow.foreign_net_buy_amount),
            _amount(flow.previous_foreign_net_buy_amount),
            _amount(flow.institution_net_buy_amount),
            _amount(flow.previous_institution_net_buy_amount),
            _amount(flow.individual_net_buy_amount),
            _amount(flow.previous_individual_net_buy_amount),
            _session_stamp(flow.previous_session_date),
            _day_stamp(flow.observed_at),
        )
        for flow in summary.flows
    ]
    return blocks.table_section(
        "투자자 순매수(억원)",
        ("시장", "외국인", "직전 외국인", "기관", "직전 기관", "개인", "직전 개인", "직전 기준", "기준"),
        rows,
    )


def _stock_flow_section(summary: MarketSummary) -> list[dict[str, Any]]:
    """추적 종목의 추정 순매수.

    시장 수급과 표를 나눈다. 저쪽은 억원이고 이쪽은 주 수라 한 표에 넣으면 자릿수가 뜻을
    잃는다. **추정치라는 것도 제목에 적는다.** 확정 수급은 장 마감 뒤에야 나온다.

    기준은 날짜가 아니라 수집 시각이다. KIS가 장중 몇 차례 갱신하는 값이라 날짜만 적으면
    아침 추정과 마감 추정이 같은 줄로 보인다(시장 수급 표와 같은 이유).
    """
    if not summary.stock_flows:
        return []
    rows = [
        (
            flow.label,
            f"{flow.foreign_net_buy_qty:+,}",
            _quantity(flow.previous_foreign_net_buy_qty),
            f"{flow.institution_net_buy_qty:+,}",
            _quantity(flow.previous_institution_net_buy_qty),
            f"{flow.total_net_buy_qty:+,}",
            _quantity(flow.previous_total_net_buy_qty),
            _session_stamp(flow.previous_business_date),
            _day_stamp(flow.collected_at),
        )
        for flow in summary.stock_flows
    ]
    return blocks.table_section(
        "종목 추정 순매수(주)",
        ("종목", "외국인", "직전 외국인", "기관", "직전 기관", "합계", "직전 합계", "직전 기준", "기준"),
        rows,
    )


def _closed_trades(summary: MarketSummary) -> tuple[StockTradeSnapshot, ...]:
    """오늘(KST) 거래일의 확정값만 고른다.

    조회 창은 연휴를 건너려고 열흘이지만, 그대로 그리면 12:30 발송에 어제 마감이 오늘
    것처럼 실린다. 확정값은 KST 18:10에 오므로 이 섹션은 저녁 발송에만 나타난다.
    """
    today = summary.generated_at.astimezone(KST_TIMEZONE).date()
    return tuple(trade for trade in summary.stock_trades if trade.business_date == today)


def _stock_trade_sections(summary: MarketSummary) -> list[dict[str, Any]]:
    """종목 마감 확정: 종가·등락과 확정 수급, 그리고 기관 세부.

    추정(장중) 표와 나눈다. 하나는 장중 스냅샷이고 하나는 마감 확정치라 같은 표에 섞으면
    어느 쪽인지 알 수 없다. 기관 세부 일곱은 열이 많아 표를 따로 그린다.

    제목에 KRX를 밝힌다. 시세 표가 15:30 이후 NXT 봉을 보이므로, 여기 종가가 그와 다른
    이유(KRX 정규장 확정치이고 NXT 체결은 이 집계에 없음)가 제목에서 보여야 한다.
    """
    trades = _closed_trades(summary)
    if not trades:
        return []
    closing_rows = [
        (
            trade.label,
            _number(trade.close),
            "-" if trade.previous_close is None else _number(trade.previous_close),
            _percent(trade.change_percent),
            f"{trade.foreign_net_buy_qty:+,}",
            _quantity(trade.previous_foreign_net_buy_qty),
            f"{trade.institution_net_buy_qty:+,}",
            _quantity(trade.previous_institution_net_buy_qty),
            f"{trade.individual_net_buy_qty:+,}",
            _quantity(trade.previous_individual_net_buy_qty),
            _session_stamp(trade.previous_business_date),
            f"{trade.business_date:%m/%d}",
        )
        for trade in trades
    ]
    # 모든 표에는 기준이 있어야 한다. 확정 일별 수급이라 시각이 아니라 거래일이다.
    detail_rows = [
        (
            trade.label,
            f"{trade.securities_net_buy_qty:+,}",
            f"{trade.investment_trust_net_buy_qty:+,}",
            f"{trade.private_equity_net_buy_qty:+,}",
            f"{trade.bank_net_buy_qty:+,}",
            f"{trade.insurance_net_buy_qty:+,}",
            f"{trade.merchant_bank_net_buy_qty:+,}",
            f"{trade.pension_fund_net_buy_qty:+,}",
            f"{trade.business_date:%m/%d}",
        )
        for trade in trades
    ]
    return [
        *blocks.table_section(
            "종목 마감 확정(주·KRX)",
            (
                "종목",
                "종가",
                "직전 종가",
                "등락",
                "외국인",
                "직전 외국인",
                "기관",
                "직전 기관",
                "개인",
                "직전 개인",
                "직전 기준",
                "기준",
            ),
            closing_rows,
        ),
        *blocks.table_section(
            "기관 세부(주·KRX)",
            ("종목", "금융투자", "투신", "사모", "은행", "보험", "종금", "연기금", "기준"),
            detail_rows,
        ),
    ]


def _ordered[T](items: Iterable[T], key: Callable[[T], str], order: Sequence[str]) -> tuple[T, ...]:
    """`order`에 적힌 차례로 줄을 세운다. 목록에 없는 값은 뒤로 밀린다."""
    rank = {value: index for index, value in enumerate(order)}
    return tuple(sorted(items, key=lambda item: rank.get(key(item), len(rank))))


def _movement_section(summary: MarketSummary) -> list[dict[str, Any]]:
    if not summary.movements:
        return []
    rows = [
        (
            movement.market_code,
            f"{movement.rising_count:,}",
            _count(movement.previous_rising_count),
            f"{movement.unchanged_count:,}",
            _count(movement.previous_unchanged_count),
            f"{movement.falling_count:,}",
            _count(movement.previous_falling_count),
            _session_stamp(movement.previous_session_date),
            _day_stamp(movement.observed_at),
        )
        for movement in summary.movements
    ]
    return blocks.table_section(
        "등락 종목 수",
        ("시장", "상승", "직전 상승", "보합", "직전 보합", "하락", "직전 하락", "직전 기준", "기준"),
        rows,
    )


def _as_of(summary: MarketSummary, scope: MarketScope) -> list[str]:
    """리포트 시각과 **가장 묵은 값**.

    값마다의 기준 시각은 이제 표 안에 있다. 여기서는 한눈에 볼 것 하나만 남긴다 —
    이 리포트에서 제일 오래된 값이 언제 것인가. 그게 최신이면 전체가 최신이다.
    """
    lines = [f"작성 {blocks.timestamp(summary.generated_at.astimezone(KST_TIMEZONE))}"]
    quotes = _scope_quotes(summary, scope)
    observed = [quote.bar_at for quote in quotes]
    observed += [flow.observed_at for flow in summary.flows]
    observed += [movement.observed_at for movement in summary.movements]
    if observed:
        lines.append(f"가장 오래된 값 {_day_stamp(min(observed))}")
    return lines


def _day_stamp(moment: datetime) -> str:
    local = moment.astimezone(KST_TIMEZONE)
    return f"{local:%m/%d} {local:%H:%M}"


def _number(value: Decimal) -> str:
    return f"{value:,.2f}"


def _rate(value: Decimal) -> str:
    """금리는 소수 셋째 자리까지다. `Numeric(18,8)`을 그대로 찍으면 `4.68000000%`가 나온다."""
    return f"{value:,.3f}%"


def _percent(change: float | None) -> str:
    if change is None:
        return "-"
    return f"{_arrow(change)} {change:+.2f}%"


def _basis_points(change: float | None) -> str:
    if change is None:
        return "-"
    return f"{_arrow(change)} {change:+.1f}bp"


def _amount(value: Decimal | None) -> str:
    """수급 대금을 억원으로 줄인다. 조 단위 숫자를 그대로 두면 표가 화면을 넘는다."""
    if value is None:
        return "-"
    return f"{value / MILLIONS_PER_HUNDRED_MILLION:+,.0f}"


def _quantity(value: int | None) -> str:
    """순매수 수량. 부호가 곧 방향이라 항상 붙인다."""
    return "-" if value is None else f"{value:+,}"


def _count(value: int | None) -> str:
    """종목 수처럼 부호가 없는 값."""
    return "-" if value is None else f"{value:,}"


def _session_stamp(session_date: date | None) -> str:
    """직전 세션 날짜. 직전 세션이 없으면 `-`다."""
    return "-" if session_date is None else f"{session_date:%m/%d}"


def _previous(value: Decimal | int | None, change: Decimal | int | None) -> Decimal | int | None:
    """증감만 있는 값의 전일 잔고를 역산한다. 증감이 없으면 전일 행이 없다는 뜻이다."""
    if value is None or change is None:
        return None
    return value - change


class Delta(BaseModel):
    """직전 값과의 비교를 표의 칸으로 편 것. 값이 없으면 네 칸이 모두 `-`다."""

    model_config = ConfigDict(frozen=True)

    previous: str
    stamp: str
    change: str
    rate: str


def _delta(value: Decimal | int | None, previous: Decimal | int | None, previous_date: date | None = None) -> Delta:
    """`직전`, `직전 기준`, `직전 대비`, `등락률`.

    **날짜는 값과 같은 칸에 넣지 않고 따로 돌려준다.** 숫자 칸은 우측 정렬이라 뒤에 날짜가
    붙으면 자릿수가 세로로 맞지 않고, 정렬해서 읽는 이점이 사라진다.

    직전 기준일을 함께 내는 이유는 이 표들의 원천이 매일 도는 수집이 아니어서다. 날짜가
    없으면 사흘 전 값과의 차이를 전일 대비로 읽는다.

    등락률은 직전 값의 절대값으로 나눈다. 나누는 쪽의 부호가 등락률의 부호를 뒤집는 일이
    없어야 한다. 직전이 0이면 등락률은 `-`다 — 0에서 늘어난 비율은 정의되지 않는다.
    """
    if value is None or previous is None:
        return Delta(previous="-", stamp="-", change="-", rate="-")
    stamp = "-" if previous_date is None else f"{previous_date:%m/%d}"
    change = value - previous
    rate = "-" if previous == 0 else f"{float(change) / abs(float(previous)) * 100:+.2f}%"
    return Delta(previous=f"{previous:,.0f}", stamp=stamp, change=f"{change:+,.0f}", rate=rate)


def _arrow(change: float) -> str:
    if change > 0:
        return "▲"
    return "▼" if change < 0 else "－"
