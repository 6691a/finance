"""시세 봉 조회. **kind가 곧 물리 테이블이다.**

2026-08-18에 `quote_bar` 하나가 kind별 테이블 여덟으로 갈렸다. `quote_bar`·`quote_daily`
뷰가 그것을 UNION ALL 해서 남아 있지만 **여기서는 뷰를 쓰지 않는다.**

## 뷰를 쓰지 않는 이유

뷰의 종목 갈래는 KRX·NYSE·NASDAQ만 태운다(뷰 주석). **NXT가 통째로 빠진다** —
2026-08-27 실측으로 `stock_bar`의 NXT가 337,079행, KRX가 190,355행이라 **빠지는 쪽이 더
많다.** 그래서 종목은 `stock_bar`를 직접 읽고 거래소를 필수 인자로 받는다.

매크로 kind도 뷰를 거칠 이유가 없다. kind를 알면 테이블이 하나로 정해지고, 뷰로 읽으면
UNION ALL 여덟 갈래를 planner가 훑는다.

## 재집계

`interval`은 요청 인자다. **서버가 임의로 점을 솎지 않는다.** `date_bin`으로 구간을 접고
open은 첫 값, close는 마지막 값, high·low는 극값, volume은 합이다. Postgres에 first/last
집계가 없어 `array_agg(... ORDER BY ...)`의 첫 원소를 쓴다.

**일봉 간격(`1d`)을 분봉 쪽에 두지 않는다.** 분봉의 축은 UTC 시각이고 일봉의 축은 그 시장의
거래일이라, UTC 하루로 접으면 미국 지수의 하루가 이틀에 걸쳐 쪼개진다. 일봉은 이미 확정
테이블이 따로 있다.
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

from pydantic import Field
from sqlalchemy import DateTime, Select, Text, cast, func, literal, null, select, text, union_all
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.repository.common import DEFAULT_LIMIT, RowBundle, page_slice
from apps.models.market import (
    BondFutureBar,
    BondFutureDaily,
    CommodityBar,
    CommodityDaily,
    CryptoBar,
    CryptoDaily,
    FxBar,
    FxDaily,
    IndexBar,
    IndexDaily,
    IndexFutureBar,
    IndexFutureDaily,
    RateBar,
    RateDaily,
    StockBar,
    StockDaily,
    StockInvestorTradeDaily,
)
from apps.models.reference import QuoteSymbol, QuoteSymbolKind

# 한 응답이 낼 수 있는 점의 상한. 넘으면 조용히 솎지 않고 실패시킨다 —
# 값이 거짓이 되는 것보다 "구간을 좁히거나 간격을 넓혀라"가 낫다.
MAX_POINTS = 5_000

# kind → 분봉 테이블. **`equity`는 여기 없다** — 축이 달라서(거래소가 자연키에 있다)
# 같은 조회문으로 다루면 거래소 인자가 매크로 쪽에도 새어 든다.
BAR_TABLES: dict[QuoteSymbolKind, Any] = {
    QuoteSymbolKind.INDEX: IndexBar,
    QuoteSymbolKind.INDEX_FUTURE: IndexFutureBar,
    QuoteSymbolKind.FX: FxBar,
    QuoteSymbolKind.RATE: RateBar,
    QuoteSymbolKind.BOND_FUTURE: BondFutureBar,
    QuoteSymbolKind.COMMODITY: CommodityBar,
    QuoteSymbolKind.CRYPTO: CryptoBar,
}

DAILY_TABLES: dict[QuoteSymbolKind, Any] = {
    QuoteSymbolKind.INDEX: IndexDaily,
    QuoteSymbolKind.INDEX_FUTURE: IndexFutureDaily,
    QuoteSymbolKind.FX: FxDaily,
    QuoteSymbolKind.RATE: RateDaily,
    QuoteSymbolKind.BOND_FUTURE: BondFutureDaily,
    QuoteSymbolKind.COMMODITY: CommodityDaily,
    QuoteSymbolKind.CRYPTO: CryptoDaily,
}

# 허용 간격과 그 SQL interval. 문자열을 그대로 SQL에 넣지 않으려고 표로 둔다.
INTERVALS: dict[str, str] = {
    "1m": "1 minute",
    "5m": "5 minutes",
    "15m": "15 minutes",
    "1h": "1 hour",
}

# `date_bin`의 기준점. **UTC 자정이다** — 여기가 어긋나면 5분봉이 :02·:07로 떨어진다.
# 월요일을 고른 것은 나중에 주 단위 버킷을 더할 때 같은 원점을 쓰기 위해서다.
BIN_ORIGIN = datetime(2000, 1, 3, 0, 0, 0, tzinfo=UTC)


class SymbolRows(RowBundle):
    symbols: tuple[QuoteSymbol, ...] = ()
    has_more: bool = False
    # (kind, symbol) → (행 수, 처음, 마지막)
    bars: dict[tuple[str, str], tuple[int, datetime, datetime]] = Field(default_factory=dict)
    daily: dict[tuple[str, str], tuple[int, date, date]] = Field(default_factory=dict)
    # (kind, symbol) → 봉이 실제로 쌓인 거래소들
    exchanges: dict[tuple[str, str], tuple[str, ...]] = Field(default_factory=dict)


class QuoteReadRepository:
    """시세 봉을 읽는다. 쓰기 경로는 없다."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    # --- 조회문 (테스트가 컴파일해서 본다) ---------------------------------------

    @staticmethod
    def bar_statement(
        *,
        kind: QuoteSymbolKind,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
        exchange: str | None = None,
        limit: int = MAX_POINTS,
    ) -> Select[Any]:
        """분봉 재집계 조회문. **끝 경계가 열려 있다**(`< end`).

        `limit + 1`을 읽어 상한 초과를 판단한다. 구간과 간격으로 점 수를 미리 계산하지
        않는 이유는 그것이 **거래가 없던 밤까지 세기** 때문이다 — 한 달치 1분봉을 달라는
        요청이 실제로는 8천 점인데 4만 3천 점으로 세어 거절되면 안 된다.
        """
        stride = text(f"interval '{INTERVALS[interval]}'")
        table = StockBar if kind is QuoteSymbolKind.EQUITY else BAR_TABLES[kind]
        # **원점도 timezone-aware여야 한다.** 기본 `DateTime`은 `TIMESTAMP WITHOUT TIME ZONE`으로
        # 나가고, `bar_at`이 aware라 Postgres가 둘을 못 뺀다(2026-08-27 운영 DB에서 잡았다).
        origin = literal(BIN_ORIGIN, DateTime(timezone=True))
        bucket = func.date_bin(stride, table.bar_at, origin).label("bucket")
        statement = select(
            bucket,
            # Postgres에 first/last 집계가 없다. 정렬한 배열의 양 끝을 쓴다.
            func.array_agg(aggregate_order_by(table.open, table.bar_at.asc()))[1].label("open"),
            func.max(table.high).label("high"),
            func.min(table.low).label("low"),
            func.array_agg(aggregate_order_by(table.close, table.bar_at.desc()))[1].label("close"),
            func.sum(table.volume).label("volume"),
        ).where(table.bar_at >= start, table.bar_at < end)

        if kind is QuoteSymbolKind.EQUITY:
            # **거래소가 필수다.** 빼면 KRX와 NXT 체결이 한 봉에 섞여 어느 쪽 값도 아니게 된다.
            statement = statement.where(StockBar.stock_code == symbol, StockBar.exchange == exchange)
        else:
            statement = statement.where(table.symbol == symbol)

        return statement.group_by(bucket).order_by(bucket).limit(limit + 1)

    @staticmethod
    def daily_statement(
        *,
        kind: QuoteSymbolKind,
        symbol: str,
        start: date,
        end: date,
        exchange: str | None = None,
        limit: int = MAX_POINTS,
    ) -> Select[Any]:
        """일봉 조회문. 양끝 포함이다.

        **국내 종목 일봉은 `stock_daily`에 없다.** `stock_investor_trade_daily`가 수급과
        함께 갖고 있어 다시 받지 않기로 했고(그 모델 docstring), 컬럼 이름도
        `open_price`처럼 달라 여기서 갈라 준다. `stock_daily`는 해외 상장 종목용이다.
        """
        if kind is not QuoteSymbolKind.EQUITY:
            table = DAILY_TABLES[kind]
            columns = [table.business_date, table.open, table.high, table.low, table.close, table.volume]
            # **월물은 지수선물에만 있다.** 다른 kind의 테이블에는 그 칸이 없어서 조건 없이
            # 넣으면 조회가 죽는다. 부르는 쪽은 배열이 비었는지로 구분한다.
            if kind is QuoteSymbolKind.INDEX_FUTURE:
                columns.append(IndexFutureDaily.contract_code)
            return (
                select(*columns)
                .where(table.symbol == symbol, table.business_date >= start, table.business_date <= end)
                .order_by(table.business_date)
                .limit(limit + 1)
            )
        if exchange == "KRX":
            row = StockInvestorTradeDaily
            return (
                select(
                    row.business_date,
                    row.open_price,
                    row.high_price,
                    row.low_price,
                    row.close_price,
                    row.accumulated_volume,
                )
                .where(row.stock_code == symbol, row.business_date >= start, row.business_date <= end)
                .order_by(row.business_date)
                .limit(limit + 1)
            )
        return (
            select(
                StockDaily.business_date,
                StockDaily.open,
                StockDaily.high,
                StockDaily.low,
                StockDaily.close,
                StockDaily.volume,
            )
            .where(
                StockDaily.stock_code == symbol,
                StockDaily.exchange == exchange,
                StockDaily.business_date >= start,
                StockDaily.business_date <= end,
            )
            .order_by(StockDaily.business_date)
            .limit(limit + 1)
        )

    @staticmethod
    def bar_coverage_statement() -> Select[Any]:
        """kind별 분봉의 행 수와 구간. **여덟 테이블을 UNION ALL로 한 왕복에 묶는다.**

        뷰(`quote_bar`)를 쓰지 않는 이유는 그것이 종목의 NXT를 빼기 때문이다.
        """
        parts = [
            select(
                literal(kind.value).label("kind"),
                table.symbol.label("symbol"),
                cast(null(), Text).label("exchange"),
                func.count().label("rows"),
                func.min(table.bar_at).label("oldest"),
                func.max(table.bar_at).label("newest"),
            ).group_by(table.symbol)
            for kind, table in BAR_TABLES.items()
        ]
        parts.append(
            select(
                literal(QuoteSymbolKind.EQUITY.value).label("kind"),
                StockBar.stock_code.label("symbol"),
                cast(StockBar.exchange, Text).label("exchange"),
                func.count().label("rows"),
                func.min(StockBar.bar_at).label("oldest"),
                func.max(StockBar.bar_at).label("newest"),
            ).group_by(StockBar.stock_code, StockBar.exchange)
        )
        return select(union_all(*parts).subquery())

    @staticmethod
    def daily_coverage_statement() -> Select[Any]:
        """kind별 일봉의 행 수와 구간. 국내 종목은 수급 테이블이 원본이다."""
        parts = [
            select(
                literal(kind.value).label("kind"),
                table.symbol.label("symbol"),
                func.count().label("rows"),
                func.min(table.business_date).label("oldest"),
                func.max(table.business_date).label("newest"),
            ).group_by(table.symbol)
            for kind, table in DAILY_TABLES.items()
        ]
        parts.append(
            select(
                literal(QuoteSymbolKind.EQUITY.value).label("kind"),
                StockDaily.stock_code.label("symbol"),
                func.count().label("rows"),
                func.min(StockDaily.business_date).label("oldest"),
                func.max(StockDaily.business_date).label("newest"),
            ).group_by(StockDaily.stock_code)
        )
        parts.append(
            select(
                literal(QuoteSymbolKind.EQUITY.value).label("kind"),
                StockInvestorTradeDaily.stock_code.label("symbol"),
                func.count().label("rows"),
                func.min(StockInvestorTradeDaily.business_date).label("oldest"),
                func.max(StockInvestorTradeDaily.business_date).label("newest"),
            ).group_by(StockInvestorTradeDaily.stock_code)
        )
        return select(union_all(*parts).subquery())

    # --- 공개 조회 -----------------------------------------------------------

    async def symbol_rows(self, *, limit: int = DEFAULT_LIMIT, offset: int = 0) -> SymbolRows:
        """마스터 한 쪽에 **실제 쌓인 것**을 붙인다. 왕복 셋이고 한 세션 안이다."""
        async with self._session_factory() as session:
            found = list(
                (
                    await session.execute(
                        select(QuoteSymbol)
                        .order_by(QuoteSymbol.kind, QuoteSymbol.symbol)
                        .limit(limit + 1)
                        .offset(offset)
                    )
                ).scalars()
            )
            symbols, has_more = page_slice(found, limit)
            bars: dict[tuple[str, str], tuple[int, datetime, datetime]] = {}
            exchanges: dict[tuple[str, str], list[str]] = {}
            for row in await session.execute(self.bar_coverage_statement()):
                key = (row.kind, row.symbol)
                found = bars.get(key)
                bars[key] = (
                    (found[0] if found else 0) + row.rows,
                    min(found[1], row.oldest) if found else row.oldest,
                    max(found[2], row.newest) if found else row.newest,
                )
                if row.exchange is not None:
                    exchanges.setdefault(key, []).append(row.exchange)

            daily: dict[tuple[str, str], tuple[int, date, date]] = {}
            for row in await session.execute(self.daily_coverage_statement()):
                key = (row.kind, row.symbol)
                found = daily.get(key)
                daily[key] = (
                    (found[0] if found else 0) + row.rows,
                    min(found[1], row.oldest) if found else row.oldest,
                    max(found[2], row.newest) if found else row.newest,
                )
        return SymbolRows(
            symbols=symbols,
            has_more=has_more,
            bars=bars,
            daily=daily,
            exchanges={key: tuple(sorted(value)) for key, value in exchanges.items()},
        )

    async def symbol(self, kind: QuoteSymbolKind, symbol: str) -> QuoteSymbol | None:
        """심볼 하나의 마스터 행. 제공처와 라벨이 여기서 온다."""
        async with self._session_factory() as session:
            return (
                await session.execute(
                    select(QuoteSymbol).where(QuoteSymbol.kind == kind, QuoteSymbol.symbol == symbol)
                )
            ).scalar_one_or_none()

    async def bar_rows(
        self,
        *,
        kind: QuoteSymbolKind,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
        exchange: str | None = None,
    ) -> tuple[tuple[Any, ...], ...]:
        """재집계된 분봉. **상한 판정은 부르는 쪽이 한다** — 여기는 `limit + 1`을 준다."""
        async with self._session_factory() as session:
            rows = await session.execute(
                self.bar_statement(
                    kind=kind,
                    symbol=symbol,
                    interval=interval,
                    start=start,
                    end=end,
                    exchange=exchange,
                )
            )
            return tuple(tuple(row) for row in rows)

    async def daily_rows(
        self,
        *,
        kind: QuoteSymbolKind,
        symbol: str,
        start: date,
        end: date,
        exchange: str | None = None,
    ) -> tuple[tuple[Any, ...], ...]:
        async with self._session_factory() as session:
            rows = await session.execute(
                self.daily_statement(kind=kind, symbol=symbol, start=start, end=end, exchange=exchange)
            )
            return tuple(tuple(row) for row in rows)


def bar_provider(symbols: Sequence[QuoteSymbol], kind: QuoteSymbolKind, symbol: str) -> str:
    """마스터에서 제공처를 찾는다. 봉 행에도 있지만 0건일 때 낼 것이 없다."""
    for row in symbols:
        if row.kind is kind and row.symbol == symbol:
            return row.provider
    return ""
