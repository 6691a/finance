"""시세 봉의 매핑. **행 묶음을 컬럼 배열로 편다.**

층의 경계와 파일 규칙은 `apps/api/service/__init__.py`가 갖는다.

**상한 초과를 여기서 예외로 올린다.** 리포지토리는 `limit + 1`을 줄 뿐 HTTP를 모르고,
라우트가 이 예외를 400으로 바꾼다 — 판단할 것을 위로 올리는 것이 이 저장소의 규칙이다.
"""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from apps.api.repository import (
    DEFAULT_LIMIT,
    INTERVALS,
    MAX_POINTS,
    QuoteReadRepository,
    SymbolRows,
    bar_provider,
)
from apps.api.schemas import BarSeries, DailySeries, QuoteSymbolItem, QuoteSymbolList
from apps.api.service.common import number
from apps.models.reference import QuoteSymbol, QuoteSymbolKind


class TooManyPoints(Exception):
    """상한을 넘은 요청. **조용히 솎지 않고 실패시킨다.**

    메시지가 무엇을 고쳐야 하는지 말한다 — 상한만 알려 주면 부르는 쪽이 다음 수를 못 정한다.
    """

    def __init__(self, points: int, interval: str) -> None:
        self.points = points
        self.interval = interval
        super().__init__(
            f"요청 구간이 {points}점을 넘는다. 상한은 {MAX_POINTS}점이다. "
            f"interval을 {interval}보다 넓히거나 구간을 좁혀라."
        )


class UnknownSymbol(Exception):
    """마스터에 없는 (kind, symbol). 라우트가 404로 바꾼다."""


class ExchangeRequired(Exception):
    """`equity`인데 거래소를 안 골랐다.

    **기본값을 두지 않는다.** 같은 종목이 KRX와 NXT에서 따로 체결되므로, 말없이 한쪽을
    고르면 화면이 어느 거래소 값인지 밝히지 못한 채 선을 그린다.
    """


def symbol_item(
    symbol: QuoteSymbol,
    bars: tuple[int, datetime, datetime] | None,
    daily: tuple[int, date, date] | None,
    exchanges: tuple[str, ...],
) -> QuoteSymbolItem:
    return QuoteSymbolItem(
        kind=symbol.kind.value,
        symbol=symbol.symbol,
        provider=symbol.provider,
        label=symbol.label,
        country=symbol.country,
        country_name=symbol.country_name,
        exchanges=exchanges,
        bar_rows=bars[0] if bars else 0,
        bar_from=bars[1] if bars else None,
        bar_to=bars[2] if bars else None,
        daily_rows=daily[0] if daily else 0,
        daily_from=daily[1] if daily else None,
        daily_to=daily[2] if daily else None,
    )


def build_symbols(rows: SymbolRows, *, limit: int, offset: int) -> QuoteSymbolList:
    return QuoteSymbolList(
        limit=limit,
        offset=offset,
        has_more=rows.has_more,
        items=tuple(
            symbol_item(
                symbol,
                rows.bars.get((symbol.kind.value, symbol.symbol)),
                rows.daily.get((symbol.kind.value, symbol.symbol)),
                rows.exchanges.get((symbol.kind.value, symbol.symbol), ()),
            )
            for symbol in rows.symbols
        )
    )


def columns(rows: Sequence[tuple[Any, ...]], index: int) -> tuple[Any, ...]:
    """행 묶음의 한 칸을 배열로. **전치는 여기 한 번뿐이다.**"""
    return tuple(row[index] for row in rows)


def build_bars(
    rows: Sequence[tuple[Any, ...]],
    *,
    kind: str,
    symbol: str,
    exchange: str | None,
    provider: str,
    interval: str,
) -> BarSeries:
    """**`volume`만 `None`을 허용한다.** 거래량 개념이 없는 심볼이 있다."""
    return BarSeries(
        kind=kind,
        symbol=symbol,
        exchange=exchange,
        provider=provider,
        interval=interval,
        points=len(rows),
        times=columns(rows, 0),
        open=tuple(number(value) or 0.0 for value in columns(rows, 1)),
        high=tuple(number(value) or 0.0 for value in columns(rows, 2)),
        low=tuple(number(value) or 0.0 for value in columns(rows, 3)),
        close=tuple(number(value) or 0.0 for value in columns(rows, 4)),
        volume=tuple(number(value) for value in columns(rows, 5)),
        # **종목 분봉만 칸이 하나 더 온다.** 빈 배열이 "그 kind에는 잠정 개념이 없다"다 —
        # 매크로 봉은 `is_final` 칸 자체가 테이블에 없다.
        settled=tuple(bool(value) for value in columns(rows, 6)) if rows and len(rows[0]) > 6 else (),
    )


def build_daily(
    rows: Sequence[tuple[Any, ...]],
    *,
    kind: str,
    symbol: str,
    exchange: str | None,
    provider: str,
) -> DailySeries:
    return DailySeries(
        kind=kind,
        symbol=symbol,
        exchange=exchange,
        provider=provider,
        points=len(rows),
        dates=columns(rows, 0),
        open=tuple(number(value) or 0.0 for value in columns(rows, 1)),
        high=tuple(number(value) or 0.0 for value in columns(rows, 2)),
        low=tuple(number(value) or 0.0 for value in columns(rows, 3)),
        close=tuple(number(value) or 0.0 for value in columns(rows, 4)),
        volume=tuple(number(value) for value in columns(rows, 5)),
        # 지수선물만 칸이 하나 더 온다. 없는 kind는 빈 배열이고 그것이 "월물 개념이 없다"다.
        contracts=tuple(columns(rows, 6)) if rows and len(rows[0]) > 6 else (),
    )


def wider(interval: str) -> str:
    """오류 메시지에 실을 다음 간격. 마지막 간격이면 그대로 둔다."""
    names = list(INTERVALS)
    position = names.index(interval)
    return names[min(position + 1, len(names) - 1)]


class QuoteReadService:
    """시세 봉을 읽어 응답 계약으로 준다."""

    def __init__(self, repository: QuoteReadRepository) -> None:
        self._repository = repository

    async def symbols(self, *, limit: int = DEFAULT_LIMIT, offset: int = 0) -> QuoteSymbolList:
        rows = await self._repository.symbol_rows(limit=limit, offset=offset)
        return build_symbols(rows, limit=limit, offset=offset)

    async def bars(
        self,
        *,
        kind: QuoteSymbolKind,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
        exchange: str | None = None,
    ) -> BarSeries:
        master = await self._guard(kind, symbol, exchange)
        rows = await self._repository.bar_rows(
            kind=kind, symbol=symbol, interval=interval, start=start, end=end, exchange=exchange
        )
        if len(rows) > MAX_POINTS:
            raise TooManyPoints(MAX_POINTS, wider(interval))
        return build_bars(
            rows,
            kind=kind.value,
            symbol=symbol,
            exchange=exchange,
            provider=master.provider,
            interval=interval,
        )

    async def daily(
        self,
        *,
        kind: QuoteSymbolKind,
        symbol: str,
        start: date,
        end: date,
        exchange: str | None = None,
    ) -> DailySeries:
        master = await self._guard(kind, symbol, exchange)
        rows = await self._repository.daily_rows(
            kind=kind, symbol=symbol, start=start, end=end, exchange=exchange
        )
        if len(rows) > MAX_POINTS:
            raise TooManyPoints(MAX_POINTS, "1d")
        return build_daily(
            rows, kind=kind.value, symbol=symbol, exchange=exchange, provider=master.provider
        )

    async def _guard(
        self, kind: QuoteSymbolKind, symbol: str, exchange: str | None
    ) -> QuoteSymbol:
        """마스터에 있는 심볼인지, 종목이면 거래소를 골랐는지."""
        master = await self._repository.symbol(kind, symbol)
        if master is None:
            raise UnknownSymbol(f"{kind.value}:{symbol}")
        if kind is QuoteSymbolKind.EQUITY and not exchange:
            raise ExchangeRequired(symbol)
        return master


__all__ = [
    "ExchangeRequired",
    "QuoteReadService",
    "TooManyPoints",
    "UnknownSymbol",
    "bar_provider",
    "build_bars",
    "build_daily",
    "build_symbols",
    "columns",
    "wider",
]
