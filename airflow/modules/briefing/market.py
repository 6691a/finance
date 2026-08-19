"""시장 브리핑의 조회·렌더링.

리포트는 둘인데 파일은 하나다. 한국장과 미국장은 **같은 표를 다른 시간대에 다른 조합으로**
보여 주는 것이라 섹션 구성이 크게 겹친다. 파일을 나누면 사본만 생긴다. 무엇을 어느 리포트에
넣을지는 `MarketScope`가 정한다.

조회도 한 번만 한다. 심볼이 수십 개, 계열이 십여 개라 리포트마다 쿼리를 좁히는 값어치가 없다.
`collect_summary`가 전부 받아 오고 렌더링이 고른다. 그 덕에 미국장 리포트의 요약은 밤사이
미국 값과 전일 한국 값을 **한 입력에서** 본다. 그게 이 리포트를 만드는 이유다.

**시각은 UTC로 담고 KST 변환은 렌더링에서만 한다.** Slack은 프론트엔드가 없는 출력이라
백엔드가 변환하는 자리이고, 미국 세션 날짜만 `America/New_York` 기준으로 뽑는다.
"""

import json
from collections.abc import Callable, Iterable, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol, Self

from pendulum import timezone
from pydantic import AwareDatetime, BaseModel, ConfigDict

from modules.briefing import blocks
from modules.briefing.trend import ChangeKind, Trend, sign_streak, summarize
from modules.sql import read_sql
from modules.utility import KST_TIMEZONE

LATEST_QUOTES = read_sql("postgres", "quote_bar", "select_latest_briefing_bars.sql")
LATEST_EXCHANGE_RATES = read_sql("postgres", "exchange_rate", "select_latest_with_previous.sql")
LATEST_RATES = read_sql("postgres", "indicator_observation", "select_latest_pair.sql")
LATEST_FLOWS = read_sql("postgres", "market_investor_flow_snapshot", "select_latest.sql")
LATEST_MOVEMENTS = read_sql("postgres", "market_movement_snapshot", "select_latest.sql")
LATEST_STOCK_FLOWS = read_sql("postgres", "stock_investor_estimate_snapshot", "select_latest.sql")
LATEST_STOCK_TRADES = read_sql("postgres", "stock_investor_trade_daily", "select_latest.sql")
LATEST_MARKET_FUNDS = read_sql("postgres", "krx_market_funds_daily", "select_latest_pair.sql")
LATEST_SHORT_POSITIONS = read_sql("postgres", "krx_stock_short_sale_daily", "select_latest_with_lending.sql")
SPREAD_PAIRS = read_sql("postgres", "indicator_observation", "select_spread_pairs.sql")

INTRADAY_SERIES = read_sql("postgres", "quote_bar", "select_intraday_series.sql")

QUOTE_TREND = read_sql("postgres", "quote_bar", "select_daily_trend.sql")
RATE_TREND = read_sql("postgres", "indicator_observation", "select_trend.sql")
EXCHANGE_RATE_TREND = read_sql("postgres", "exchange_rate", "select_trend.sql")
FLOW_TREND = read_sql("postgres", "market_investor_flow_snapshot", "select_daily_trend.sql")

US_EASTERN = timezone("America/New_York")

# 국내 정규장. 장 상태 표시에만 쓴다.
SESSION_OPEN_HOUR_KST = 9
SESSION_CLOSE_MINUTE_KST = 15 * 60 + 45

# 조회 구간. 봉은 휴일 연휴를 건너 마지막 값을 찾아야 하고, 금리는 월간 계열이 섞여 있어 넉넉히 본다.
#
# **연휴를 건너려면 10일이 필요하다.** 4일로 두었다가 2026-08-18(화) 실측에서 국내 시세가
# 통째로 비었다. 광복절 대체공휴일로 직전 거래일이 금요일이었고, 그 세션은 KST 15:30에
# 끝나는데 조회는 그보다 늦은 시각에 돌아 4일 창을 아슬하게 벗어났다. 설날·추석은 더 길다.
# 값이 오래됐다는 사실은 창을 좁혀 숨기는 것이 아니라 context 블록의 기준 시각이 알린다.
QUOTE_LOOKBACK = timedelta(days=10)
FLOW_LOOKBACK = timedelta(days=10)
EXCHANGE_RATE_LOOKBACK = timedelta(days=14)
RATE_LOOKBACK = timedelta(days=45)

# 브리핑에 그릴 통화. 하나은행이 고시하는 전부를 넣으면 표가 화면을 넘는다.
# 이 순서가 그대로 표의 줄 순서다.
BRIEFING_CURRENCIES = ("USD", "JPY", "EUR", "CNY")

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

# 미국장 표의 줄 순서는 종류로 묶는다. 정렬 없이 두면 SQL이 심볼 이름순으로 줘서
# 금·나스닥·비트코인이 섞인다. 같은 종류(원자재끼리, 크립토끼리)가 붙어야 비교가 된다.
US_KIND_ORDER = ("index", "index_future", "bond_future", "commodity", "crypto", "equity")

# 장중 차트에 그리는 심볼. 이 순서가 이미지 순서다. 해외를 붙일 때도 이 목록만 늘린다.
# quote_bar 뷰를 읽으므로 종목(stock_bar)·지수(index_bar)·환율(fx_bar)이 한 쿼리로 나온다.
CHART_SYMBOLS = (
    ("kis", "KOSPI"),
    ("kis", "KOSDAQ"),
    ("kis", "005930"),
    ("kis", "000660"),
    ("yahoo", "USDKRW"),
)

# 실시간 환율 표의 줄 순서. 목록에 없는 fx 심볼(USDJPY 등)은 뒤로 밀린다.
FX_SYMBOL_ORDER = ("USDKRW", "JPYKRW", "DXY")

# 금리 스프레드. (라벨, 왼쪽 다리, 오른쪽 다리)이고 값은 왼쪽-오른쪽을 bp로 그린다.
# 장단기 역전이면 음수가 그대로 보인다.
SPREAD_LEGS = (
    ("미국 10년-2년", ("fred", "DGS10"), ("fred", "DGS2")),
    ("한국 10년-2년", ("ecos", "KTB10Y"), ("ecos", "KTB2Y")),
    ("한미 10년", ("ecos", "KTB10Y"), ("fred", "DGS10")),
)

# 시세 표에 그리는 종류. 등락을 퍼센트로 그려도 뜻이 통하는 것만 넣는다.
#
# **`rate`를 넣지 않는다.** 금리를 퍼센트 변화로 그리면 4.65 → 4.70이 `+1.08%`가 되어
# 5bp 움직임이 1% 넘게 뛴 것처럼 보인다. 금리는 `indicator_observation` 쪽 표가 bp로 그린다.
# `fx`도 넣지 않는다. 환율은 하나은행 고시 표가 따로 있어 같은 값이 두 표에 다르게 나온다.
QUOTED_KINDS = frozenset({"index", "index_future", "equity", "commodity", "bond_future", "crypto"})

# 추세를 재는 구간. 거래일 20일을 담으려면 주말·공휴일을 감안해 달력으로 이만큼 봐야 한다.
# 더 늘리면 "요즘"이 아니라 "지난 분기"를 재게 되고, 오늘의 움직임이 늘 평범해 보인다.
TREND_LOOKBACK = timedelta(days=30)


class MarketScope(StrEnum):
    """어느 리포트인가. 조회는 같고 무엇을 그릴지가 다르다."""

    KOREA = "korea"
    US = "us"


class Cursor(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(self, *args: object) -> bool | None: ...

    def execute(self, statement: str, parameters: Any) -> object: ...

    def fetchall(self) -> Any: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...


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

    @property
    def change_percent(self) -> float:
        if not self.previous_close:
            return 0.0
        return float((self.close - self.previous_close) / self.previous_close * 100)


class FxChange(BaseModel):
    model_config = ConfigDict(frozen=True)

    currency: str
    posted_on: date
    round: int
    rate: Decimal
    previous_rate: Decimal | None = None

    @property
    def change_percent(self) -> float | None:
        if not self.previous_rate:
            return None
        return float((self.rate - self.previous_rate) / self.previous_rate * 100)


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

    @property
    def change_bp(self) -> float | None:
        if self.previous_value is None:
            return None
        return float((self.value - self.previous_value) * 100)


class FlowSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    market_code: str
    observed_at: AwareDatetime
    foreign_net_buy_amount: Decimal
    institution_net_buy_amount: Decimal
    individual_net_buy_amount: Decimal


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
    model_config = ConfigDict(frozen=True)

    market_code: str
    observed_at: AwareDatetime
    rising_count: int
    unchanged_count: int
    falling_count: int


class MarketFundsSnapshot(BaseModel):
    """증시자금 최신 영업일. 단위는 KIS 표기 그대로 억원이다.

    고객예탁금 전일대비는 API가 준 값이고, 신용융자·미수금의 전일대비는 전일 행이
    있을 때만 계산한다. 수집 첫날에는 `None`이다.
    """

    model_config = ConfigDict(frozen=True)

    business_date: date
    customer_deposit: Decimal
    customer_deposit_change: Decimal
    credit_loan_balance: Decimal
    credit_loan_change: Decimal | None = None
    unsettled_amount: Decimal
    unsettled_change: Decimal | None = None


class StockShortPositionSnapshot(BaseModel):
    """종목 하나의 공매도와 같은 날 대차 잔고. 대차는 공매도의 재고라 한 표에 그린다.

    대차 행이 아직 없으면 `None`이다. 비중은 KIS 표기 그대로의 퍼센트다.
    """

    model_config = ConfigDict(frozen=True)

    stock_code: str
    label: str
    business_date: date
    short_sale_quantity: int
    short_sale_volume_ratio: Decimal
    lending_balance_quantity: int | None = None
    lending_balance_change: int | None = None


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


class MarketSummary(BaseModel):
    """두 리포트가 함께 쓰는 집계 결과. 시각은 전부 UTC다.

    `*_trends`는 계열 좌표별 추세다. 이력이 없는 계열은 아예 키가 없다. 새로 붙인 심볼
    때문에 리포트가 죽으면 안 된다.
    """

    model_config = ConfigDict(frozen=True)

    generated_at: AwareDatetime
    quotes: tuple[QuoteChange, ...] = ()
    exchange_rates: tuple[FxChange, ...] = ()
    rates: tuple[RateChange, ...] = ()
    flows: tuple[FlowSnapshot, ...] = ()
    stock_flows: tuple[StockFlowSnapshot, ...] = ()
    stock_trades: tuple[StockTradeSnapshot, ...] = ()
    movements: tuple[MovementSnapshot, ...] = ()
    funds: MarketFundsSnapshot | None = None
    short_positions: tuple[StockShortPositionSnapshot, ...] = ()
    spreads: tuple[RateSpread, ...] = ()
    quote_trends: dict[str, Trend] = {}
    """`provider:symbol` → 추세."""

    rate_trends: dict[str, Trend] = {}
    """`provider:series_id` → 추세. 변화는 bp다."""

    exchange_rate_trends: dict[str, Trend] = {}
    """통화 → 추세."""

    foreign_streaks: dict[str, int] = {}
    """시장 코드 → 외국인 순매수가 며칠째 같은 편인가. 부호가 방향이다(`-5`는 5일 연속 순매도)."""


class ChartSeries(BaseModel):
    """장중 차트의 계열 하나. 당일 정규장 분봉 종가만 담는다."""

    model_config = ConfigDict(frozen=True)

    provider: str
    symbol: str
    label: str
    points: tuple[tuple[AwareDatetime, Decimal], ...]


def collect_summary(connection: Connection, now: datetime) -> MarketSummary:
    """브리핑 한 통에 들어갈 값을 전부 읽는다."""
    quotes = _fetch(
        connection,
        LATEST_QUOTES,
        (now - QUOTE_LOOKBACK,),
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
    exchange_rates = _fetch(
        connection,
        LATEST_EXCHANGE_RATES,
        (list(BRIEFING_CURRENCIES), (now - EXCHANGE_RATE_LOOKBACK).date()),
        lambda row: FxChange(
            currency=row[0],
            posted_on=row[1],
            round=row[2],
            rate=row[3],
            previous_rate=row[4],
        ),
    )
    rates = _fetch(
        connection,
        LATEST_RATES,
        (GOVERNMENT_BOND, TEN_YEAR_MONTHS, (now - RATE_LOOKBACK).date()),
        lambda row: RateChange(
            provider=row[0],
            series_id=row[1],
            country=row[2],
            country_name=row[3],
            label=row[4],
            observation_date=row[5],
            value=row[6],
            previous_value=row[7],
        ),
    )
    flows = _fetch(
        connection,
        LATEST_FLOWS,
        (now - FLOW_LOOKBACK,),
        lambda row: FlowSnapshot(
            market_code=row[0],
            observed_at=row[1],
            foreign_net_buy_amount=row[2],
            institution_net_buy_amount=row[3],
            individual_net_buy_amount=row[4],
        ),
    )
    stock_flows = _fetch(
        connection,
        LATEST_STOCK_FLOWS,
        ((now - FLOW_LOOKBACK).date(),),
        lambda row: StockFlowSnapshot(
            stock_code=row[0],
            label=row[1],
            business_date=row[2],
            foreign_net_buy_qty=row[3],
            institution_net_buy_qty=row[4],
            total_net_buy_qty=row[5],
            collected_at=row[6],
        ),
    )
    stock_trades = _fetch(
        connection,
        LATEST_STOCK_TRADES,
        ((now - FLOW_LOOKBACK).date(),),
        lambda row: StockTradeSnapshot(
            stock_code=row[0],
            label=row[1],
            business_date=row[2],
            close=row[3],
            previous_close=row[4],
            foreign_net_buy_qty=row[5],
            institution_net_buy_qty=row[6],
            individual_net_buy_qty=row[7],
            securities_net_buy_qty=row[8],
            investment_trust_net_buy_qty=row[9],
            private_equity_net_buy_qty=row[10],
            bank_net_buy_qty=row[11],
            insurance_net_buy_qty=row[12],
            merchant_bank_net_buy_qty=row[13],
            pension_fund_net_buy_qty=row[14],
        ),
    )
    movements = _fetch(
        connection,
        LATEST_MOVEMENTS,
        (now - FLOW_LOOKBACK,),
        lambda row: MovementSnapshot(
            market_code=row[0],
            observed_at=row[1],
            rising_count=row[2],
            unchanged_count=row[3],
            falling_count=row[4],
        ),
    )
    funds = _market_funds(connection)
    # 당일(KST)은 제외한다. KIS가 장중에 당일 공매도 행을 0으로 보낸다(SQL 주석 참고).
    short_positions = _fetch(
        connection,
        LATEST_SHORT_POSITIONS,
        ((now - FLOW_LOOKBACK).date(), now.astimezone(KST_TIMEZONE).date()),
        lambda row: StockShortPositionSnapshot(
            stock_code=row[0],
            label=row[1],
            business_date=row[2],
            short_sale_quantity=row[3],
            short_sale_volume_ratio=row[4],
            lending_balance_quantity=row[5],
            lending_balance_change=row[6],
        ),
    )
    spreads = _rate_spreads(connection, (now - RATE_LOOKBACK).date())
    # 추세는 값 자체가 아니라 "이게 큰 값인가"에 답하는 근거다. 요약을 쓰는 모델이 비교
    # 기준 없이는 +0.82%가 큰지 알 수 없어서 미리 계산해 넣는다.
    trend_since = (now - TREND_LOOKBACK).date()
    quote_trends = _trends(connection, QUOTE_TREND, (trend_since, now - TREND_LOOKBACK), ChangeKind.RELATIVE, 2)
    rate_trends = _trends(
        connection,
        RATE_TREND,
        (GOVERNMENT_BOND, TEN_YEAR_MONTHS, trend_since),
        ChangeKind.ABSOLUTE,
        2,
    )
    exchange_rate_trends = _trends(
        connection,
        EXCHANGE_RATE_TREND,
        (list(BRIEFING_CURRENCIES), trend_since),
        ChangeKind.RELATIVE,
        1,
    )
    foreign_streaks = _sign_streaks(connection, FLOW_TREND, (now - TREND_LOOKBACK,), 1)

    return MarketSummary(
        generated_at=now,
        quotes=quotes,
        exchange_rates=exchange_rates,
        rates=rates,
        flows=flows,
        stock_flows=stock_flows,
        stock_trades=stock_trades,
        movements=movements,
        funds=funds,
        short_positions=short_positions,
        spreads=spreads,
        quote_trends=quote_trends,
        rate_trends=rate_trends,
        exchange_rate_trends=exchange_rate_trends,
        foreign_streaks=foreign_streaks,
    )


def collect_chart_series(connection: Connection, now: datetime) -> tuple[ChartSeries, ...]:
    """장중 차트에 그릴 당일 분봉. `CHART_SYMBOLS` 순서를 지킨다.

    봉이 없는 심볼은 계열 자체를 만들지 않는다. 개장 전이나 갓 붙인 심볼 때문에
    리포트가 죽으면 안 된다(`*_trends`와 같은 원칙). 전부 비면 차트를 생략한다.
    """
    local = now.astimezone(KST_TIMEZONE)
    session_open = local.replace(hour=SESSION_OPEN_HOUR_KST, minute=0, second=0, microsecond=0)
    providers = sorted({provider for provider, _ in CHART_SYMBOLS})
    symbols = [symbol for _, symbol in CHART_SYMBOLS]
    with connection.cursor() as cursor:
        cursor.execute(INTRADAY_SERIES, (providers, symbols, session_open))
        rows = cursor.fetchall()

    grouped: dict[tuple[str, str], list[tuple[datetime, Decimal]]] = {}
    labels: dict[tuple[str, str], str] = {}
    for provider, symbol, label, bar_at, close in rows:
        grouped.setdefault((provider, symbol), []).append((bar_at, close))
        labels[(provider, symbol)] = label

    series = []
    for provider, symbol in CHART_SYMBOLS:
        points = grouped.get((provider, symbol))
        if not points:
            continue
        series.append(
            ChartSeries(provider=provider, symbol=symbol, label=labels[(provider, symbol)], points=tuple(points))
        )
    return tuple(series)


def _market_funds(connection: Connection) -> MarketFundsSnapshot | None:
    """증시자금 최신 영업일. 전일 행이 있으면 신용융자·미수금 증감도 계산한다."""
    with connection.cursor() as cursor:
        cursor.execute(LATEST_MARKET_FUNDS, ())
        rows = cursor.fetchall()
    if not rows:
        return None
    latest = rows[0]
    previous = rows[1] if len(rows) > 1 else None
    return MarketFundsSnapshot(
        business_date=latest[0],
        customer_deposit=latest[1],
        customer_deposit_change=latest[2],
        credit_loan_balance=latest[3],
        credit_loan_change=latest[3] - previous[3] if previous else None,
        unsettled_amount=latest[4],
        unsettled_change=latest[4] - previous[4] if previous else None,
    )


def _rate_spreads(connection: Connection, since: date) -> tuple[RateSpread, ...]:
    """`SPREAD_LEGS`의 스프레드. 다리 한쪽이라도 관측값이 없으면 그 스프레드는 만들지 않는다."""
    providers = sorted({provider for _, *legs in SPREAD_LEGS for provider, _ in legs})
    series_ids = sorted({series_id for _, *legs in SPREAD_LEGS for _, series_id in legs})
    with connection.cursor() as cursor:
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
    comment: str | None,
    error: str | None = None,
    *,
    chart_files: Sequence[tuple[str, str]] | None = None,
    chart_error: str | None = None,
):
    """Slack 블록. 값은 Slack 기본 `table` 블록에 넣는다(`blocks` 모듈 docstring 참고)."""
    local = summary.generated_at.astimezone(KST_TIMEZONE)
    if scope is MarketScope.KOREA:
        rendered = [
            blocks.header(f"📈 한국장 브리핑 · {blocks.timestamp(local)} · {session_state(summary.generated_at)}"),
            *_quote_section("국내 지수·선물", _korea_quotes(summary)),
            *_chart_section(chart_files, chart_error),
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
            *_quote_section("미국 지수·선물", _us_quotes(summary)),
            *_rate_section(summary),
            *_rate_spread_section(summary),
            *_quote_section("전일 국내", _korea_quotes(summary)),
            *_exchange_rate_section(summary),
            *_flow_section(summary),
            *_stock_flow_section(summary),
        ]
    rendered += blocks.comment_blocks(comment, error)
    rendered.append(blocks.context(_as_of(summary, scope)))
    return rendered


def render_text(summary: MarketSummary, scope: MarketScope) -> str:
    """블록을 못 그리는 자리에 뜨는 한 줄. 알림 미리보기가 이걸 읽는다."""
    quotes = _korea_quotes(summary) if scope is MarketScope.KOREA else _us_quotes(summary)
    parts = [f"{quote.label} {_number(quote.close)} {_percent(quote.change_percent)}" for quote in quotes[:2]]
    if scope is not MarketScope.KOREA:
        parts += [f"{rate.label} {_rate(rate.value)}" for rate in summary.rates if rate.country == "US"][:1]
    title = "한국장 브리핑" if scope is MarketScope.KOREA else "미국장 마감"
    return f"{title} · " + " · ".join(parts) if parts else f"{title} · 값 없음"


def comment_input(summary: MarketSummary, scope: MarketScope) -> str:
    """LLM에 줄 입력. **집계가 끝난 값만 넣는다.** 원시 행도 SQL도 주지 않는다.

    값마다 `trend`를 함께 싣는다. 그것이 없으면 모델은 `+0.82%`가 큰 값인지 알 방법이
    없어 "올랐다"밖에 못 쓴다. 이력이 없는 계열은 `trend`가 `null`이다.
    """
    quotes = _korea_quotes(summary) + (
        _intraday_overseas(summary) + _fx_quotes(summary)
        if scope is MarketScope.KOREA
        else _us_quotes(summary)
    )
    payload: dict[str, Any] = {
        "as_of_kst": summary.generated_at.astimezone(KST_TIMEZONE).isoformat(),
        "trend_window_days": TREND_LOOKBACK.days,
        "quotes": [
            {
                "label": quote.label,
                "country": quote.country,
                "close": float(quote.close),
                "change_percent": round(quote.change_percent, 2),
                "trend": _trend_payload(summary.quote_trends.get(f"{quote.provider}:{quote.symbol}")),
            }
            for quote in quotes
        ],
        # 한국장 브리핑에는 환율 표가 없다. 블록에 없는 값을 입력에 주면 모델이
        # 화면에 없는 숫자를 쓴다. 미국장(아침) 리포트만 전일 고시를 싣는다.
        "exchange_rates": []
        if scope is MarketScope.KOREA
        else [
            {
                "currency": fx.currency,
                "rate": float(fx.rate),
                "change_percent": round(fx.change_percent, 2) if fx.change_percent is not None else None,
                "trend": _trend_payload(summary.exchange_rate_trends.get(fx.currency)),
            }
            for fx in summary.exchange_rates
        ],
        "investor_flows": [
            {
                "market": flow.market_code,
                "foreign_net_buy_billion_krw": _billion(flow.foreign_net_buy_amount),
                "institution_net_buy_billion_krw": _billion(flow.institution_net_buy_amount),
                "individual_net_buy_billion_krw": _billion(flow.individual_net_buy_amount),
                "foreign_streak_days": summary.foreign_streaks.get(flow.market_code),
                # 장중 스냅샷이라 시세보다 며칠 묵어 있을 수 있다. 모델이 그걸 모르면
                # 나흘 지난 순매수를 오늘 것으로 읽고 "오늘 수급은…"이라고 쓴다.
                "as_of_kst": flow.observed_at.astimezone(KST_TIMEZONE).isoformat(),
            }
            for flow in summary.flows
        ],
        "stock_confirmed_trades": [
            {
                "stock": trade.label,
                "code": trade.stock_code,
                "close": float(trade.close),
                "change_percent": round(trade.change_percent, 2) if trade.change_percent is not None else None,
                "foreign_net_buy_shares": trade.foreign_net_buy_qty,
                "institution_net_buy_shares": trade.institution_net_buy_qty,
                "individual_net_buy_shares": trade.individual_net_buy_qty,
                "pension_fund_net_buy_shares": trade.pension_fund_net_buy_qty,
                # 마감 확정치다. 추정(estimate) 스냅샷과 단위는 같아도 성격이 다르다.
                "estimate": False,
                "business_date": trade.business_date.isoformat(),
            }
            for trade in _closed_trades(summary)
        ],
        "stock_investor_estimates": [
            {
                "stock": flow.label,
                "code": flow.stock_code,
                # 시장 수급과 단위가 다르다. 모델이 억원으로 읽으면 자릿수를 그대로 옮겨 쓴다.
                "foreign_net_buy_shares": flow.foreign_net_buy_qty,
                "institution_net_buy_shares": flow.institution_net_buy_qty,
                "estimate": True,
                "business_date": flow.business_date.isoformat(),
            }
            for flow in summary.stock_flows
        ],
    }
    if scope is MarketScope.KOREA:
        payload["movements"] = [
            {
                "market": movement.market_code,
                "rising": movement.rising_count,
                "falling": movement.falling_count,
                "as_of_kst": movement.observed_at.astimezone(KST_TIMEZONE).isoformat(),
            }
            for movement in summary.movements
        ]
        if summary.funds is not None:
            payload["market_funds_billion_krw"] = {
                "business_date": summary.funds.business_date.isoformat(),
                "customer_deposit": float(summary.funds.customer_deposit),
                "customer_deposit_change": float(summary.funds.customer_deposit_change),
                "credit_loan_balance": float(summary.funds.credit_loan_balance),
                "credit_loan_change": (
                    float(summary.funds.credit_loan_change) if summary.funds.credit_loan_change is not None else None
                ),
            }
        payload["short_positions"] = [
            {
                "stock": position.label,
                "code": position.stock_code,
                "short_sale_volume_ratio_percent": float(position.short_sale_volume_ratio),
                "short_sale_shares": position.short_sale_quantity,
                "lending_balance_shares": position.lending_balance_quantity,
                "lending_balance_change_shares": position.lending_balance_change,
                "business_date": position.business_date.isoformat(),
            }
            for position in summary.short_positions
        ]
    else:
        # 미국장 리포트의 요약은 밤사이 금리와 전일 한국 값을 함께 읽는다.
        payload["session_date"] = us_session_date(summary.generated_at).isoformat()
        payload["rates"] = [
            {
                "label": rate.label,
                "country": rate.country,
                "percent": float(rate.value),
                "change_bp": round(rate.change_bp, 1) if rate.change_bp is not None else None,
                "trend": _trend_payload(summary.rate_trends.get(f"{rate.provider}:{rate.series_id}")),
            }
            for rate in summary.rates
        ]
        payload["rate_spreads_bp"] = [
            {
                "label": spread.label,
                "spread_bp": round(spread.spread_bp, 1),
                "change_bp": round(spread.change_bp, 1) if spread.change_bp is not None else None,
                "observed_on": spread.observed_on.isoformat(),
            }
            for spread in summary.spreads
        ]
    return json.dumps(payload, ensure_ascii=False)


def _trend_payload(trend: Trend | None) -> dict[str, Any] | None:
    if trend is None:
        return None
    return {
        "observations": trend.observations,
        "change": round(trend.change, 2),
        "move_percentile": round(trend.move_percentile),
        "streak_days": trend.streak,
        "window_low": round(trend.window_low, 2),
        "window_high": round(trend.window_high, 2),
        "thin": trend.thin,
    }


def _fetch(connection: Connection, statement: str, parameters: tuple, build: Callable[[Any], Any]) -> tuple:
    with connection.cursor() as cursor:
        cursor.execute(statement, parameters)
        return tuple(build(row) for row in cursor.fetchall())


def _daily_series(
    connection: Connection,
    statement: str,
    parameters: tuple,
    key_columns: int,
) -> dict[str, list[tuple[date, float]]]:
    """일별 이력 조회 결과를 계열별로 접는다.

    행 모양은 `(키..., 날짜, 값)`이고 앞 `key_columns`개가 계열을 가른다. 조회는 날짜
    오름차순으로 정렬해 두었으므로 여기서 다시 정렬하지 않는다.
    """
    series: dict[str, list[tuple[date, float]]] = {}
    with connection.cursor() as cursor:
        cursor.execute(statement, parameters)
        for row in cursor.fetchall():
            key = ":".join(str(part) for part in row[:key_columns])
            series.setdefault(key, []).append((row[key_columns], float(row[key_columns + 1])))
    return series


def _trends(
    connection: Connection,
    statement: str,
    parameters: tuple,
    kind: ChangeKind,
    key_columns: int,
) -> dict[str, Trend]:
    """계열별 추세.

    이력이 없거나 하루뿐인 계열은 키 자체를 만들지 않는다. 새로 붙인 심볼 때문에 리포트가
    죽으면 안 되고, 없는 것과 0을 구분해야 프롬프트가 "모른다"를 말할 수 있다.
    """
    series = _daily_series(connection, statement, parameters, key_columns)
    trends = {key: summarize(points, kind) for key, points in series.items()}
    return {key: trend for key, trend in trends.items() if trend is not None}


def _sign_streaks(
    connection: Connection,
    statement: str,
    parameters: tuple,
    key_columns: int,
) -> dict[str, int]:
    """계열별 "며칠째 같은 편인가".

    수급에는 `_trends`를 쓰지 않는다. 순매수 금액은 값 자체에 부호가 있어 "5일 연속
    순매도"가 뜻하는 것은 금액이 마이너스로 이어졌다는 것이지 매일 줄었다는 것이 아니다.
    """
    series = _daily_series(connection, statement, parameters, key_columns)
    return {key: sign_streak([value for _, value in points]) for key, points in series.items()}


def _korea_quotes(summary: MarketSummary) -> tuple[QuoteChange, ...]:
    return _ordered(
        (quote for quote in summary.quotes if quote.country == "KR" and quote.kind in QUOTED_KINDS),
        lambda quote: quote.symbol,
        KOREA_SYMBOL_ORDER,
    )


def _us_quotes(summary: MarketSummary) -> tuple[QuoteChange, ...]:
    """미국 심볼과 크립토. 크립토는 country가 `XX`(나라 없음)라 종류로 넣는다."""
    return _ordered(
        (
            quote
            for quote in summary.quotes
            if quote.kind in QUOTED_KINDS and (quote.country == "US" or quote.kind == "crypto")
        ),
        lambda quote: quote.kind,
        US_KIND_ORDER,
    )


def _intraday_overseas(summary: MarketSummary) -> tuple[QuoteChange, ...]:
    """한국장 시간에도 값이 움직이는 해외 심볼.

    미국은 선물만 넣는다. 현물 지수는 이 시간에 닫혀 있어 어제 종가를 오늘 값처럼 보이게 한다.
    크립토는 24시간 거래라 항상 실시간이다. country가 나라가 아닌 `XX`라 뒤로 밀린다.
    """
    return _ordered(
        (
            quote
            for quote in summary.quotes
            if quote.kind in QUOTED_KINDS
            and (
                (quote.country == "US" and quote.kind == INDEX_FUTURE)
                or quote.country in ASIA_COUNTRIES
                or quote.kind == "crypto"
            )
        ),
        lambda quote: quote.country,
        OVERSEAS_COUNTRY_ORDER,
    )


def _fx_quotes(summary: MarketSummary) -> tuple[QuoteChange, ...]:
    """장외 실시간 환율. 하나은행 고시(전일)와 달리 장중에 움직이는 값이다."""
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
    """
    if not quotes:
        return []
    rows = [
        (quote.label, _number(quote.close), _percent(quote.change_percent), _day_stamp(quote.bar_at))
        for quote in quotes
    ]
    return blocks.table_section(title, ("구분", "종가", "등락", "기준"), rows)


def _chart_section(files: Sequence[tuple[str, str]] | None, error: str | None) -> list[dict[str, Any]]:
    """당일 분봉 차트. 계열마다 image 블록 하나다(`(file_id, label)`). **실패는 채널에 남긴다.**

    조용히 빠지면 차트가 원래 없는 리포트와 구분되지 않는다(요약 실패와 같은 원칙).
    둘 다 없으면 개장 전처럼 그릴 봉이 없는 정상 흐름이라 아무 것도 그리지 않는다.
    """
    if files:
        return [blocks.image(file_id, f"{label} 당일 분봉 차트") for file_id, label in files]
    if error:
        return [blocks.context([f"⚠️ 차트 생성 실패: {error}"])]
    return []


def _exchange_rate_section(summary: MarketSummary) -> list[dict[str, Any]]:
    """미국장(아침) 리포트 전용. 하나은행 고시는 하루 한 번 수집이라 늘 전일 최종 회차다.

    그래서 한국장(장중) 브리핑에는 넣지 않고 제목에도 전일임을 밝힌다.
    """
    if not summary.exchange_rates:
        return []
    rows = [
        (fx.currency, _number(fx.rate), _percent(fx.change_percent), f"{fx.posted_on:%m/%d} {fx.round}회차")
        for fx in _ordered(summary.exchange_rates, lambda fx: fx.currency, BRIEFING_CURRENCIES)
    ]
    return blocks.table_section("전일 환율(하나은행 고시)", ("통화", "매매기준율", "전일 대비", "기준"), rows)


def _rate_section(summary: MarketSummary) -> list[dict[str, Any]]:
    if not summary.rates:
        return []
    rows = [
        (rate.label, _rate(rate.value), _basis_points(rate.change_bp), f"{rate.observation_date:%m/%d}")
        for rate in summary.rates
    ]
    return blocks.table_section("주요국 10년 금리", ("국가", "금리", "전일 대비", "기준"), rows)


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
    rows = [
        ("고객예탁금", f"{funds.customer_deposit:,.0f}", _signed_amount(funds.customer_deposit_change), stamp),
        ("신용융자 잔고", f"{funds.credit_loan_balance:,.0f}", _signed_amount(funds.credit_loan_change), stamp),
        ("미수금", f"{funds.unsettled_amount:,.0f}", _signed_amount(funds.unsettled_change), stamp),
    ]
    return blocks.table_section("증시자금(억원)", ("구분", "잔고", "전일 대비", "기준"), rows)


def _short_position_section(summary: MarketSummary) -> list[dict[str, Any]]:
    """종목 공매도와 대차 잔고. 대차는 공매도의 재고라 한 표에 그린다. 단위는 주다."""
    if not summary.short_positions:
        return []
    rows = [
        (
            position.label,
            f"{position.short_sale_volume_ratio:.2f}%",
            f"{position.short_sale_quantity:,}",
            f"{position.lending_balance_quantity:,}" if position.lending_balance_quantity is not None else "-",
            _signed_amount(position.lending_balance_change),
            f"{position.business_date:%m/%d}",
        )
        for position in summary.short_positions
    ]
    return blocks.table_section(
        "공매도·대차(주)", ("종목", "공매도 비중", "공매도 수량", "대차 잔고", "잔고 증감", "기준"), rows
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
            _amount(flow.institution_net_buy_amount),
            _amount(flow.individual_net_buy_amount),
            _day_stamp(flow.observed_at),
        )
        for flow in summary.flows
    ]
    return blocks.table_section("투자자 순매수(억원)", ("시장", "외국인", "기관", "개인", "기준"), rows)


def _stock_flow_section(summary: MarketSummary) -> list[dict[str, Any]]:
    """추적 종목의 추정 순매수.

    시장 수급과 표를 나눈다. 저쪽은 억원이고 이쪽은 주 수라 한 표에 넣으면 자릿수가 뜻을
    잃는다. **추정치라는 것도 제목에 적는다.** 확정 수급은 장 마감 뒤에야 나온다.
    """
    if not summary.stock_flows:
        return []
    rows = [
        (
            flow.label,
            f"{flow.foreign_net_buy_qty:+,}",
            f"{flow.institution_net_buy_qty:+,}",
            f"{flow.total_net_buy_qty:+,}",
            f"{flow.business_date:%m/%d}",
        )
        for flow in summary.stock_flows
    ]
    return blocks.table_section("종목 추정 순매수(주)", ("종목", "외국인", "기관", "합계", "기준"), rows)


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
    """
    trades = _closed_trades(summary)
    if not trades:
        return []
    closing_rows = [
        (
            trade.label,
            _number(trade.close),
            _percent(trade.change_percent),
            f"{trade.foreign_net_buy_qty:+,}",
            f"{trade.institution_net_buy_qty:+,}",
            f"{trade.individual_net_buy_qty:+,}",
            f"{trade.business_date:%m/%d}",
        )
        for trade in trades
    ]
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
        )
        for trade in trades
    ]
    return [
        *blocks.table_section(
            "종목 마감 확정(주)",
            ("종목", "종가", "등락", "외국인", "기관", "개인", "기준"),
            closing_rows,
        ),
        *blocks.table_section(
            "기관 세부(주)",
            ("종목", "금융투자", "투신", "사모", "은행", "보험", "종금", "연기금"),
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
            f"{movement.unchanged_count:,}",
            f"{movement.falling_count:,}",
            _day_stamp(movement.observed_at),
        )
        for movement in summary.movements
    ]
    return blocks.table_section("등락 종목 수", ("시장", "상승", "보합", "하락", "기준"), rows)


def _as_of(summary: MarketSummary, scope: MarketScope) -> list[str]:
    """리포트 시각과 **가장 묵은 값**.

    값마다의 기준 시각은 이제 표 안에 있다. 여기서는 한눈에 볼 것 하나만 남긴다 —
    이 리포트에서 제일 오래된 값이 언제 것인가. 그게 최신이면 전체가 최신이다.
    """
    lines = [f"작성 {blocks.timestamp(summary.generated_at.astimezone(KST_TIMEZONE))}"]
    quotes = _korea_quotes(summary) if scope is MarketScope.KOREA else _us_quotes(summary)
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


def _amount(value: Decimal) -> str:
    """수급 대금을 억원으로 줄인다. 조 단위 숫자를 그대로 두면 표가 화면을 넘는다."""
    return f"{value / MILLIONS_PER_HUNDRED_MILLION:+,.0f}"


def _signed_amount(value: Decimal | int | None) -> str:
    """부호를 항상 붙인 증감. 전일 행이 없어 계산하지 못한 값은 `-`다."""
    if value is None:
        return "-"
    return f"{value:+,.0f}"


def _billion(value: Decimal) -> float:
    return round(float(value) / MILLIONS_PER_HUNDRED_MILLION, 1)


def _arrow(change: float) -> str:
    if change > 0:
        return "▲"
    return "▼" if change < 0 else "－"
