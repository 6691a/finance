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
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol, Self

from pendulum import timezone
from pydantic import AwareDatetime, BaseModel, ConfigDict

from modules.briefing import blocks
from modules.sql import read_sql
from modules.utility import KST_TIMEZONE

LATEST_QUOTES = read_sql("postgres", "quote_bar", "select_latest_briefing_bars.sql")
LATEST_EXCHANGE_RATES = read_sql("postgres", "exchange_rate", "select_latest_with_previous.sql")
LATEST_RATES = read_sql("postgres", "indicator_observation", "select_latest_pair.sql")
LATEST_FLOWS = read_sql("postgres", "market_investor_flow_snapshot", "select_latest.sql")
LATEST_MOVEMENTS = read_sql("postgres", "market_movement_snapshot", "select_latest.sql")

US_EASTERN = timezone("America/New_York")

# 국내 정규장. 장 상태 표시에만 쓴다.
SESSION_OPEN_HOUR_KST = 9
SESSION_CLOSE_MINUTE_KST = 15 * 60 + 45

# 조회 구간. 봉은 휴일 연휴를 건너 마지막 값을 찾아야 하고, 금리는 월간 계열이 섞여 있어 넉넉히 본다.
QUOTE_LOOKBACK = timedelta(days=4)
FLOW_LOOKBACK = timedelta(days=4)
EXCHANGE_RATE_LOOKBACK = timedelta(days=14)
RATE_LOOKBACK = timedelta(days=45)

# 브리핑에 그릴 통화. 하나은행이 고시하는 전부를 넣으면 표가 화면을 넘는다.
BRIEFING_CURRENCIES = ("USD", "JPY", "EUR", "CNY")

# 국가 비교의 기준 만기. 나라마다 고시 만기가 달라 10년물만 두 나라 이상이 항상 갖는다.
TEN_YEAR_MONTHS = 120
GOVERNMENT_BOND = "government_bond"

# 한국장 시간에도 값이 움직이는 해외 시장. 미국 현물은 닫혀 있어 넣지 않는다.
ASIA_COUNTRIES = frozenset({"JP", "TW", "HK", "CN"})
INDEX_FUTURE = "index_future"


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


class MovementSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    market_code: str
    observed_at: AwareDatetime
    rising_count: int
    unchanged_count: int
    falling_count: int


class MarketSummary(BaseModel):
    """두 리포트가 함께 쓰는 집계 결과. 시각은 전부 UTC다."""

    model_config = ConfigDict(frozen=True)

    generated_at: AwareDatetime
    quotes: tuple[QuoteChange, ...] = ()
    exchange_rates: tuple[FxChange, ...] = ()
    rates: tuple[RateChange, ...] = ()
    flows: tuple[FlowSnapshot, ...] = ()
    movements: tuple[MovementSnapshot, ...] = ()


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
    return MarketSummary(
        generated_at=now,
        quotes=quotes,
        exchange_rates=exchange_rates,
        rates=rates,
        flows=flows,
        movements=movements,
    )


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


def render_blocks(summary: MarketSummary, scope: MarketScope, comment: str | None, error: str | None = None):
    """Slack 블록. 값은 코드 블록 안 고정폭 표로 그린다."""
    local = summary.generated_at.astimezone(KST_TIMEZONE)
    if scope is MarketScope.KOREA:
        rendered = [
            blocks.header(f"📈 한국장 브리핑 · {blocks.timestamp(local)} · {session_state(summary.generated_at)}"),
            *_quote_section("국내 지수·선물", _korea_quotes(summary)),
            *_quote_section("장중 해외", _intraday_overseas(summary)),
            *_exchange_rate_section(summary),
            *_flow_section(summary),
            *_movement_section(summary),
        ]
    else:
        session = us_session_date(summary.generated_at)
        rendered = [
            blocks.header(f"🌙 미국장 마감 · {session:%m/%d}(현지) · {blocks.timestamp(local)}"),
            *_quote_section("미국 지수·선물", _us_quotes(summary)),
            *_rate_section(summary),
            *_quote_section("전일 국내", _korea_quotes(summary)),
            *_exchange_rate_section(summary),
            *_flow_section(summary),
        ]
    rendered += blocks.comment_blocks(comment, error)
    rendered.append(blocks.context(_as_of(summary, scope)))
    return rendered


def render_text(summary: MarketSummary, scope: MarketScope) -> str:
    """블록을 못 그리는 자리에 뜨는 한 줄. 알림 미리보기가 이걸 읽는다."""
    quotes = _korea_quotes(summary) if scope is MarketScope.KOREA else _us_quotes(summary)
    parts = [f"{quote.label} {_number(quote.close)} {_percent(quote.change_percent)}" for quote in quotes[:2]]
    if scope is MarketScope.KOREA:
        parts += [f"{fx.currency} {_number(fx.rate)}" for fx in summary.exchange_rates if fx.currency == "USD"]
    else:
        parts += [f"{rate.label} {rate.value}%" for rate in summary.rates if rate.country == "US"][:1]
    title = "한국장 브리핑" if scope is MarketScope.KOREA else "미국장 마감"
    return f"{title} · " + " · ".join(parts) if parts else f"{title} · 값 없음"


def comment_input(summary: MarketSummary, scope: MarketScope) -> str:
    """LLM에 줄 입력. **집계가 끝난 값만 넣는다.** 원시 행도 SQL도 주지 않는다."""
    quotes = _korea_quotes(summary) + (
        _intraday_overseas(summary) if scope is MarketScope.KOREA else _us_quotes(summary)
    )
    payload: dict[str, Any] = {
        "as_of_kst": summary.generated_at.astimezone(KST_TIMEZONE).isoformat(),
        "quotes": [
            {
                "label": quote.label,
                "country": quote.country,
                "close": float(quote.close),
                "change_percent": round(quote.change_percent, 2),
            }
            for quote in quotes
        ],
        "exchange_rates": [
            {
                "currency": fx.currency,
                "rate": float(fx.rate),
                "change_percent": round(fx.change_percent, 2) if fx.change_percent is not None else None,
            }
            for fx in summary.exchange_rates
        ],
        "investor_flows": [
            {
                "market": flow.market_code,
                "foreign_net_buy_billion_krw": _billion(flow.foreign_net_buy_amount),
                "institution_net_buy_billion_krw": _billion(flow.institution_net_buy_amount),
                "individual_net_buy_billion_krw": _billion(flow.individual_net_buy_amount),
            }
            for flow in summary.flows
        ],
    }
    if scope is MarketScope.KOREA:
        payload["movements"] = [
            {
                "market": movement.market_code,
                "rising": movement.rising_count,
                "falling": movement.falling_count,
            }
            for movement in summary.movements
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
            }
            for rate in summary.rates
        ]
    return json.dumps(payload, ensure_ascii=False)


def _fetch(connection: Connection, statement: str, parameters: tuple, build: Callable[[Any], Any]) -> tuple:
    with connection.cursor() as cursor:
        cursor.execute(statement, parameters)
        return tuple(build(row) for row in cursor.fetchall())


def _korea_quotes(summary: MarketSummary) -> tuple[QuoteChange, ...]:
    return tuple(quote for quote in summary.quotes if quote.country == "KR")


def _us_quotes(summary: MarketSummary) -> tuple[QuoteChange, ...]:
    return tuple(quote for quote in summary.quotes if quote.country == "US")


def _intraday_overseas(summary: MarketSummary) -> tuple[QuoteChange, ...]:
    """한국장 시간에도 값이 움직이는 해외 심볼.

    미국은 선물만 넣는다. 현물 지수는 이 시간에 닫혀 있어 어제 종가를 오늘 값처럼 보이게 한다.
    """
    return tuple(
        quote
        for quote in summary.quotes
        if (quote.country == "US" and quote.kind == INDEX_FUTURE) or quote.country in ASIA_COUNTRIES
    )


def _quote_section(title: str, quotes: Sequence[QuoteChange]) -> list[dict[str, Any]]:
    if not quotes:
        return []
    rows = [(quote.label, _number(quote.close), _percent(quote.change_percent)) for quote in quotes]
    return [blocks.table_section(title, ("구분", "종가", "등락"), rows)]


def _exchange_rate_section(summary: MarketSummary) -> list[dict[str, Any]]:
    if not summary.exchange_rates:
        return []
    rows = [(fx.currency, _number(fx.rate), _percent(fx.change_percent)) for fx in summary.exchange_rates]
    return [blocks.table_section("환율(하나은행 고시)", ("통화", "매매기준율", "전일 대비"), rows)]


def _rate_section(summary: MarketSummary) -> list[dict[str, Any]]:
    if not summary.rates:
        return []
    rows = [(rate.label, f"{rate.value}%", _basis_points(rate.change_bp)) for rate in summary.rates]
    return [blocks.table_section("주요국 10년 금리", ("국가", "금리", "전일 대비"), rows)]


def _flow_section(summary: MarketSummary) -> list[dict[str, Any]]:
    if not summary.flows:
        return []
    rows = [
        (
            flow.market_code,
            _amount(flow.foreign_net_buy_amount),
            _amount(flow.institution_net_buy_amount),
            _amount(flow.individual_net_buy_amount),
        )
        for flow in summary.flows
    ]
    return [blocks.table_section("투자자 순매수(억원)", ("시장", "외국인", "기관", "개인"), rows)]


def _movement_section(summary: MarketSummary) -> list[dict[str, Any]]:
    if not summary.movements:
        return []
    rows = [
        (
            movement.market_code,
            str(movement.rising_count),
            str(movement.unchanged_count),
            str(movement.falling_count),
        )
        for movement in summary.movements
    ]
    return [blocks.table_section("등락 종목 수", ("시장", "상승", "보합", "하락"), rows)]


def _as_of(summary: MarketSummary, scope: MarketScope) -> list[str]:
    """각 값이 언제 것인지. 어느 섹션이 묵었는지 보이지 않으면 조용히 옛 값을 읽는다."""
    lines = []
    quotes = _korea_quotes(summary) if scope is MarketScope.KOREA else _us_quotes(summary)
    if quotes:
        latest = max(quote.bar_at for quote in quotes)
        lines.append(f"시세 {blocks.timestamp(latest.astimezone(KST_TIMEZONE))}")
    if summary.exchange_rates:
        newest = max(summary.exchange_rates, key=lambda fx: (fx.posted_on, fx.round))
        lines.append(f"환율 {newest.posted_on:%m/%d} {newest.round}회차")
    if scope is MarketScope.US and summary.rates:
        lines.append(f"금리 {max(rate.observation_date for rate in summary.rates):%m/%d}")
    return lines or ["표시할 값이 없다"]


def _number(value: Decimal) -> str:
    return f"{value:,.2f}"


def _percent(change: float | None) -> str:
    if change is None:
        return "-"
    return f"{_arrow(change)} {change:+.2f}%"


def _basis_points(change: float | None) -> str:
    if change is None:
        return "-"
    return f"{_arrow(change)} {change:+.1f}bp"


def _amount(value: Decimal) -> str:
    """원 단위 금액을 억원으로 줄인다. 조 단위 숫자를 그대로 두면 표가 화면을 넘는다."""
    return f"{value / 100_000_000:+,.0f}"


def _billion(value: Decimal) -> float:
    return round(float(value) / 100_000_000, 1)


def _arrow(change: float) -> str:
    if change > 0:
        return "▲"
    return "▼" if change < 0 else "－"
