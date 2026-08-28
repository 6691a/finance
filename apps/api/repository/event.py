"""사건의 기대·실제·판정과 기술적 신호·투자의견 조회.

넷을 한 리포지토리에 두는 이유는 **전부 종목과 날짜로 자르는 같은 질문**이기 때문이다.
추출 원장만 문서 축이라 페이지네이션이 붙는다.
"""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.repository.common import DEFAULT_LIMIT, page_slice
from apps.models.analysis import (
    StockEventClaim,
    StockEventExtraction,
    StockEventOutcome,
    TechnicalSignal,
)
from apps.models.market import StockAnalystOpinion

# 한 쪽에 담긴 행과 "다음 쪽이 있나".
Rows = tuple[tuple[object, ...], bool]


class EventReadRepository:
    """사건·신호·투자의견을 읽는다. 쓰기 경로는 없다."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def _page(self, statement: Select[Any], limit: int) -> Rows:
        async with self._session_factory() as session:
            found = list((await session.execute(statement)).scalars())
        return page_slice(found, limit)

    @staticmethod
    def claim_statement(
        *,
        start: datetime,
        end: datetime,
        stock_codes: Sequence[str] = (),
        claim_kinds: Sequence[str] = (),
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> Select[Any]:
        statement = select(StockEventClaim).where(
            StockEventClaim.stated_at >= start, StockEventClaim.stated_at <= end
        )
        if stock_codes:
            statement = statement.where(StockEventClaim.stock_code.in_(stock_codes))
        if claim_kinds:
            statement = statement.where(StockEventClaim.claim_kind.in_(claim_kinds))
        return statement.order_by(StockEventClaim.stated_at.desc()).limit(limit + 1).offset(offset)

    @staticmethod
    def outcome_statement(
        *,
        start: datetime,
        end: datetime,
        stock_codes: Sequence[str] = (),
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> Select[Any]:
        statement = select(StockEventOutcome).where(
            StockEventOutcome.announced_at >= start, StockEventOutcome.announced_at <= end
        )
        if stock_codes:
            statement = statement.where(StockEventOutcome.stock_code.in_(stock_codes))
        return statement.order_by(StockEventOutcome.announced_at.desc()).limit(limit + 1).offset(offset)

    @staticmethod
    def extraction_statement(
        *, start: datetime, end: datetime, limit: int = DEFAULT_LIMIT, offset: int = 0
    ) -> Select[Any]:
        """추출 원장. **주장 0건 행이 대부분이라** 페이지네이션이 붙는다."""
        return (
            select(StockEventExtraction)
            .where(
                StockEventExtraction.extracted_at >= start,
                StockEventExtraction.extracted_at <= end,
            )
            .order_by(StockEventExtraction.extracted_at.desc())
            .limit(limit + 1)
            .offset(offset)
        )

    @staticmethod
    def signal_statement(
        *,
        start: date,
        end: date,
        symbols: Sequence[str] = (),
        kinds: Sequence[str] = (),
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> Select[Any]:
        statement = select(TechnicalSignal).where(
            TechnicalSignal.signal_date >= start, TechnicalSignal.signal_date <= end
        )
        if symbols:
            statement = statement.where(TechnicalSignal.symbol.in_(symbols))
        if kinds:
            statement = statement.where(TechnicalSignal.kind.in_(kinds))
        return (
            statement.order_by(TechnicalSignal.signal_date.desc(), TechnicalSignal.symbol)
            .limit(limit + 1)
            .offset(offset)
        )

    @staticmethod
    def opinion_statement(
        *,
        start: date,
        end: date,
        stock_codes: Sequence[str] = (),
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> Select[Any]:
        statement = select(StockAnalystOpinion).where(
            StockAnalystOpinion.business_date >= start, StockAnalystOpinion.business_date <= end
        )
        if stock_codes:
            statement = statement.where(StockAnalystOpinion.stock_code.in_(stock_codes))
        return (
            statement.order_by(
                StockAnalystOpinion.business_date.desc(), StockAnalystOpinion.broker_name
            )
            .limit(limit + 1)
            .offset(offset)
        )

    # --- 공개 조회 -----------------------------------------------------------

    async def claims(self, **filters: Any) -> Rows:
        return await self._page(self.claim_statement(**filters), filters["limit"])

    async def outcomes(self, **filters: Any) -> Rows:
        return await self._page(self.outcome_statement(**filters), filters["limit"])

    async def extractions(self, **filters: Any) -> Rows:
        return await self._page(self.extraction_statement(**filters), filters["limit"])

    async def signals(self, **filters: Any) -> Rows:
        return await self._page(self.signal_statement(**filters), filters["limit"])

    async def opinions(self, **filters: Any) -> Rows:
        return await self._page(self.opinion_statement(**filters), filters["limit"])
