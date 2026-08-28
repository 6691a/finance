"""수급·신용·대차·공매도 조회.

**테이블이 아홉이지만 조회는 전부 같은 모양이다** — 날짜 구간으로 자르고, 종목별인 것은
종목코드로 더 자르고, 오름차순으로 한 쪽을 준다. 그래서 조회문을 만드는 함수 하나를
나눠 쓴다. 그 함수가 없으면 같은 세 줄이 아홉 번 복사되고, 정렬 방향이나 `limit + 1`을
한 곳만 고친 날 표 하나가 거꾸로 뜨거나 다음 쪽 버튼이 죽는다.

**장중 스냅샷과 확정 일별값을 한 메서드로 합치지 않는다.** 축이 시각과 거래일로 다르고,
앞은 갱신되는 추정이며 뒤는 마감 뒤 확정이라 섞으면 어느 쪽 숫자인지 못 가른다.
"""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.repository.common import DEFAULT_LIMIT, page_slice
from apps.models.market import (
    KrxCreditBalanceRankingDaily,
    KrxMarketFundsDaily,
    KrxMarketSecuritiesLendingDaily,
    KrxStockCreditBalanceDaily,
    KrxStockSecuritiesLendingDaily,
    KrxStockShortSaleDaily,
    MarketInvestorFlowSnapshot,
    MarketMovementSnapshot,
    StockInvestorEstimateSnapshot,
    StockInvestorTradeDaily,
)

# 한 쪽에 담긴 행과 "다음 쪽이 있나".
Rows = tuple[tuple[Any, ...], bool]


def windowed(
    model: Any,
    axis: Any,
    *,
    start: date | datetime,
    end: date | datetime,
    code_column: Any = None,
    codes: Sequence[str] = (),
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> Select[Any]:
    """날짜(또는 시각) 구간으로 자른 한 쪽. **양끝을 포함하고 `limit + 1`을 읽는다.**

    시각 축도 양끝 포함인 이유는 이 테이블들이 분 단위 스냅샷이라 끝 경계에 정확히 걸리는
    행이 뜻을 갖기 때문이다 — 봉과 달리 구간을 대표하지 않는다.
    """
    statement = select(model).where(axis >= start, axis <= end)
    if codes and code_column is not None:
        statement = statement.where(code_column.in_(codes))
    return statement.order_by(axis).limit(limit + 1).offset(offset)


class PositioningReadRepository:
    """수급·신용·대차·공매도를 읽는다. 쓰기 경로는 없다."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def _page(self, statement: Select[Any], limit: int) -> Rows:
        async with self._session_factory() as session:
            found = list((await session.execute(statement)).scalars())
        return page_slice(found, limit)

    # --- 장중 (축이 시각이다) --------------------------------------------------

    async def investor_flows(
        self, *, start: datetime, end: datetime, markets: Sequence[str] = (), limit: int, offset: int
    ) -> Rows:
        return await self._page(
            windowed(
                MarketInvestorFlowSnapshot,
                MarketInvestorFlowSnapshot.observed_at,
                start=start,
                end=end,
                code_column=MarketInvestorFlowSnapshot.market_code,
                codes=markets,
                limit=limit,
                offset=offset,
            ),
            limit,
        )

    async def market_movement(
        self, *, start: datetime, end: datetime, symbols: Sequence[str] = (), limit: int, offset: int
    ) -> Rows:
        return await self._page(
            windowed(
                MarketMovementSnapshot,
                MarketMovementSnapshot.observed_at,
                start=start,
                end=end,
                code_column=MarketMovementSnapshot.symbol,
                codes=symbols,
                limit=limit,
                offset=offset,
            ),
            limit,
        )

    # --- 확정 (축이 거래일이다) ------------------------------------------------

    async def stock_flows(
        self, *, start: date, end: date, stock_codes: Sequence[str] = (), limit: int, offset: int
    ) -> Rows:
        return await self._page(
            windowed(
                StockInvestorTradeDaily,
                StockInvestorTradeDaily.business_date,
                start=start,
                end=end,
                code_column=StockInvestorTradeDaily.stock_code,
                codes=stock_codes,
                limit=limit,
                offset=offset,
            ),
            limit,
        )

    async def estimates(
        self, *, start: date, end: date, stock_codes: Sequence[str] = (), limit: int, offset: int
    ) -> Rows:
        return await self._page(
            windowed(
                StockInvestorEstimateSnapshot,
                StockInvestorEstimateSnapshot.business_date,
                start=start,
                end=end,
                code_column=StockInvestorEstimateSnapshot.stock_code,
                codes=stock_codes,
                limit=limit,
                offset=offset,
            ),
            limit,
        )

    async def short_sale(
        self, *, start: date, end: date, stock_codes: Sequence[str] = (), limit: int, offset: int
    ) -> Rows:
        return await self._page(
            windowed(
                KrxStockShortSaleDaily,
                KrxStockShortSaleDaily.business_date,
                start=start,
                end=end,
                code_column=KrxStockShortSaleDaily.stock_code,
                codes=stock_codes,
                limit=limit,
                offset=offset,
            ),
            limit,
        )

    async def lending(
        self,
        *,
        start: date,
        end: date,
        stock_codes: Sequence[str] = (),
        markets: Sequence[str] = (),
        limit: int,
        offset: int,
    ) -> tuple[tuple[Any, ...], tuple[Any, ...], bool]:
        """**시장과 종목을 함께 읽되 섞지 않는다.** 같은 쪽 크기를 양쪽에 걸고,
        "다음 쪽이 있나"는 둘 중 하나라도 더 있으면 참이다 — 한쪽이 끝났다고 버튼이
        꺼지면 나머지가 잘린 채로 남는다."""
        market_rows, market_more = await self._page(
            windowed(
                KrxMarketSecuritiesLendingDaily,
                KrxMarketSecuritiesLendingDaily.business_date,
                start=start,
                end=end,
                code_column=KrxMarketSecuritiesLendingDaily.market_code,
                codes=markets,
                limit=limit,
                offset=offset,
            ),
            limit,
        )
        stock_rows, stock_more = await self._page(
            windowed(
                KrxStockSecuritiesLendingDaily,
                KrxStockSecuritiesLendingDaily.business_date,
                start=start,
                end=end,
                code_column=KrxStockSecuritiesLendingDaily.stock_code,
                codes=stock_codes,
                limit=limit,
                offset=offset,
            ),
            limit,
        )
        return market_rows, stock_rows, market_more or stock_more

    async def credit_balance(
        self, *, start: date, end: date, stock_codes: Sequence[str] = (), limit: int, offset: int
    ) -> Rows:
        """**축이 `trade_date`다** — 이 테이블만 거래일과 결제일을 둘 다 갖는다."""
        return await self._page(
            windowed(
                KrxStockCreditBalanceDaily,
                KrxStockCreditBalanceDaily.trade_date,
                start=start,
                end=end,
                code_column=KrxStockCreditBalanceDaily.stock_code,
                codes=stock_codes,
                limit=limit,
                offset=offset,
            ),
            limit,
        )

    async def market_funds(self, *, start: date, end: date, limit: int, offset: int) -> Rows:
        return await self._page(
            windowed(
                KrxMarketFundsDaily,
                KrxMarketFundsDaily.business_date,
                start=start,
                end=end,
                limit=limit,
                offset=offset,
            ),
            limit,
        )

    async def credit_ranking(
        self, *, standard_date: date | None, limit: int, offset: int
    ) -> tuple[tuple[Any, ...], bool, tuple[date, ...]]:
        """순위 스냅샷 한 쪽과 **고를 수 있는 기준일 전부**.

        날짜마다 순위가 다시 매겨지므로 구간으로 주면 같은 종목이 여러 번 나온다. 그래서
        하루를 고르게 하고, 고를 수 있는 날짜 목록을 함께 준다 — 그 목록은 화면의 선택지라
        페이지네이션 밖이다.
        """
        async with self._session_factory() as session:
            dates = tuple(
                (
                    await session.execute(
                        select(KrxCreditBalanceRankingDaily.standard_date)
                        .distinct()
                        .order_by(KrxCreditBalanceRankingDaily.standard_date.desc())
                    )
                ).scalars()
            )
            target = standard_date or (dates[0] if dates else None)
            if target is None:
                return (), False, dates
            found = list(
                (
                    await session.execute(
                        select(KrxCreditBalanceRankingDaily)
                        .where(KrxCreditBalanceRankingDaily.standard_date == target)
                        .order_by(KrxCreditBalanceRankingDaily.rank)
                        .limit(limit + 1)
                        .offset(offset)
                    )
                ).scalars()
            )
        rows, has_more = page_slice(found, limit)
        return rows, has_more, dates
