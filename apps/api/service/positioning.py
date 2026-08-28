"""수급·신용·대차·공매도의 매핑.

**행 모델이 아홉이지만 매퍼는 전부 같은 모양이다** — ORM 행의 칸을 응답 칸으로 옮기고
`Decimal`을 JSON number로 바꾼다. 그 변환의 원본은 `apps/api/service/common.number` 하나다.
"""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from apps.api.repository import DEFAULT_LIMIT, PositioningReadRepository
from apps.api.schemas import (
    CreditBalanceList,
    CreditBalanceRow,
    CreditRankingList,
    CreditRankingRow,
    InvestorEstimateList,
    InvestorEstimateRow,
    InvestorFlowList,
    InvestorFlowPoint,
    LendingList,
    MarketFundsList,
    MarketFundsRow,
    MarketLendingRow,
    MarketMovementList,
    MarketMovementPoint,
    ShortSaleList,
    ShortSaleRow,
    StockFlowList,
    StockFlowRow,
    StockLendingRow,
)
from apps.api.service.common import number


def flow_of(row: Any) -> InvestorFlowPoint:
    return InvestorFlowPoint(
        market_code=str(getattr(row.market_code, "value", row.market_code)),
        observed_at=row.observed_at,
        foreign_net_buy_qty=row.foreign_net_buy_qty,
        institution_net_buy_qty=row.institution_net_buy_qty,
        individual_net_buy_qty=row.individual_net_buy_qty,
        foreign_net_buy_amount=number(row.foreign_net_buy_amount),
        institution_net_buy_amount=number(row.institution_net_buy_amount),
        individual_net_buy_amount=number(row.individual_net_buy_amount),
        pension_fund_net_buy_qty=row.pension_fund_net_buy_qty,
        investment_trust_net_buy_qty=row.investment_trust_net_buy_qty,
    )


def movement_of(row: Any) -> MarketMovementPoint:
    return MarketMovementPoint(
        symbol=row.symbol,
        observed_at=row.observed_at,
        upper_limit_count=row.upper_limit_count,
        rising_count=row.rising_count,
        unchanged_count=row.unchanged_count,
        falling_count=row.falling_count,
        lower_limit_count=row.lower_limit_count,
    )


def stock_flow_of(row: Any) -> StockFlowRow:
    return StockFlowRow(
        stock_code=row.stock_code,
        business_date=row.business_date,
        close_price=number(row.close_price) or 0.0,
        accumulated_volume=row.accumulated_volume,
        accumulated_trade_amount=number(row.accumulated_trade_amount) or 0.0,
        foreign_net_buy_qty=row.foreign_net_buy_qty,
        foreign_registered_net_buy_qty=row.foreign_registered_net_buy_qty,
        foreign_unregistered_net_buy_qty=row.foreign_unregistered_net_buy_qty,
        institution_net_buy_qty=row.institution_net_buy_qty,
        individual_net_buy_qty=row.individual_net_buy_qty,
        securities_net_buy_qty=row.securities_net_buy_qty,
        investment_trust_net_buy_qty=row.investment_trust_net_buy_qty,
        private_equity_net_buy_qty=row.private_equity_net_buy_qty,
        bank_net_buy_qty=row.bank_net_buy_qty,
        insurance_net_buy_qty=row.insurance_net_buy_qty,
        merchant_bank_net_buy_qty=row.merchant_bank_net_buy_qty,
        pension_fund_net_buy_qty=row.pension_fund_net_buy_qty,
        other_corporation_net_buy_qty=row.other_corporation_net_buy_qty,
        other_organization_net_buy_qty=row.other_organization_net_buy_qty,
        foreign_net_buy_amount=number(row.foreign_net_buy_amount),
        institution_net_buy_amount=number(row.institution_net_buy_amount),
        individual_net_buy_amount=number(row.individual_net_buy_amount),
    )


def estimate_of(row: Any) -> InvestorEstimateRow:
    return InvestorEstimateRow(
        stock_code=row.stock_code,
        business_date=row.business_date,
        source_time_code=str(getattr(row.source_time_code, "value", row.source_time_code)),
        foreign_net_buy_qty=row.foreign_net_buy_qty,
        institution_net_buy_qty=row.institution_net_buy_qty,
        total_net_buy_qty=row.total_net_buy_qty,
        collected_at=row.collected_at,
    )


def short_sale_of(row: Any) -> ShortSaleRow:
    return ShortSaleRow(
        stock_code=row.stock_code,
        business_date=row.business_date,
        close_price=number(row.close_price),
        accumulated_volume=row.accumulated_volume,
        short_sale_quantity=row.short_sale_quantity,
        short_sale_volume_ratio=number(row.short_sale_volume_ratio),
        short_sale_amount=number(row.short_sale_amount),
        short_sale_amount_ratio=number(row.short_sale_amount_ratio),
        short_sale_average_price=number(row.short_sale_average_price),
    )


def stock_lending_of(row: Any) -> StockLendingRow:
    return StockLendingRow(
        stock_code=row.stock_code,
        business_date=row.business_date,
        close_price=number(row.close_price),
        new_quantity=row.new_quantity,
        repayment_quantity=row.repayment_quantity,
        balance_quantity=row.balance_quantity,
        balance_amount=number(row.balance_amount),
        balance_change_quantity=row.balance_change_quantity,
    )


def market_lending_of(row: Any) -> MarketLendingRow:
    return MarketLendingRow(
        market_code=str(getattr(row.market_code, "value", row.market_code)),
        business_date=row.business_date,
        index_close=number(row.index_close),
        new_quantity=row.new_quantity,
        repayment_quantity=row.repayment_quantity,
        balance_quantity=row.balance_quantity,
        balance_amount=number(row.balance_amount),
    )


def credit_of(row: Any) -> CreditBalanceRow:
    return CreditBalanceRow(
        stock_code=row.stock_code,
        trade_date=row.trade_date,
        settlement_date=row.settlement_date,
        close_price=number(row.close_price),
        loan_balance_quantity=row.loan_balance_quantity,
        loan_balance_amount=number(row.loan_balance_amount),
        loan_balance_rate=number(row.loan_balance_rate),
        short_loan_balance_quantity=row.short_loan_balance_quantity,
        short_loan_balance_amount=number(row.short_loan_balance_amount),
        short_loan_balance_rate=number(row.short_loan_balance_rate),
    )


def ranking_of(row: Any) -> CreditRankingRow:
    return CreditRankingRow(
        standard_date=row.standard_date,
        comparison_date=row.comparison_date,
        rank=row.rank,
        stock_code=row.stock_code,
        stock_name=row.stock_name,
        close_price=number(row.close_price),
        loan_balance_quantity=row.loan_balance_quantity,
        loan_balance_amount=number(row.loan_balance_amount),
        loan_balance_rate=number(row.loan_balance_rate),
        loan_balance_growth_rate=number(row.loan_balance_growth_rate),
    )


def funds_of(row: Any) -> MarketFundsRow:
    return MarketFundsRow(
        business_date=row.business_date,
        index_close=number(row.index_close),
        customer_deposit=number(row.customer_deposit),
        customer_deposit_change=number(row.customer_deposit_change),
        credit_loan_balance=number(row.credit_loan_balance),
        unsettled_amount=number(row.unsettled_amount),
        turnover_ratio=number(row.turnover_ratio),
        equity_fund_amount=number(row.equity_fund_amount),
        bond_fund_amount=number(row.bond_fund_amount),
        mmf_amount=number(row.mmf_amount),
        securities_lending_amount=number(row.securities_lending_amount),
    )


class PositioningReadService:
    """수급·신용·대차·공매도를 읽어 응답 계약으로 준다.

    **아홉이 전부 같은 모양이다** — 리포지토리가 준 `(행, 더 있나)`를 `Page`로 감싼다.
    """

    def __init__(self, repository: PositioningReadRepository) -> None:
        self._repository = repository

    async def investor_flows(
        self, *, start: datetime, end: datetime, markets: Sequence[str] = (), limit: int = DEFAULT_LIMIT, offset: int = 0
    ) -> InvestorFlowList:
        rows, has_more = await self._repository.investor_flows(
            start=start, end=end, markets=markets, limit=limit, offset=offset
        )
        return InvestorFlowList(
            items=tuple(flow_of(row) for row in rows), limit=limit, offset=offset, has_more=has_more
        )

    async def market_movement(
        self, *, start: datetime, end: datetime, symbols: Sequence[str] = (), limit: int = DEFAULT_LIMIT, offset: int = 0
    ) -> MarketMovementList:
        rows, has_more = await self._repository.market_movement(
            start=start, end=end, symbols=symbols, limit=limit, offset=offset
        )
        return MarketMovementList(
            items=tuple(movement_of(row) for row in rows), limit=limit, offset=offset, has_more=has_more
        )

    async def stock_flows(
        self, *, start: date, end: date, stock_codes: Sequence[str] = (), limit: int = DEFAULT_LIMIT, offset: int = 0
    ) -> StockFlowList:
        rows, has_more = await self._repository.stock_flows(
            start=start, end=end, stock_codes=stock_codes, limit=limit, offset=offset
        )
        return StockFlowList(
            items=tuple(stock_flow_of(row) for row in rows), limit=limit, offset=offset, has_more=has_more
        )

    async def estimates(
        self, *, start: date, end: date, stock_codes: Sequence[str] = (), limit: int = DEFAULT_LIMIT, offset: int = 0
    ) -> InvestorEstimateList:
        rows, has_more = await self._repository.estimates(
            start=start, end=end, stock_codes=stock_codes, limit=limit, offset=offset
        )
        return InvestorEstimateList(
            items=tuple(estimate_of(row) for row in rows), limit=limit, offset=offset, has_more=has_more
        )

    async def short_sale(
        self, *, start: date, end: date, stock_codes: Sequence[str] = (), limit: int = DEFAULT_LIMIT, offset: int = 0
    ) -> ShortSaleList:
        rows, has_more = await self._repository.short_sale(
            start=start, end=end, stock_codes=stock_codes, limit=limit, offset=offset
        )
        return ShortSaleList(
            items=tuple(short_sale_of(row) for row in rows), limit=limit, offset=offset, has_more=has_more
        )

    async def lending(
        self,
        *,
        start: date,
        end: date,
        stock_codes: Sequence[str] = (),
        markets: Sequence[str] = (),
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> LendingList:
        market_rows, stock_rows, has_more = await self._repository.lending(
            start=start, end=end, stock_codes=stock_codes, markets=markets, limit=limit, offset=offset
        )
        return LendingList(
            market=tuple(market_lending_of(row) for row in market_rows),
            stock=tuple(stock_lending_of(row) for row in stock_rows),
            limit=limit,
            offset=offset,
            has_more=has_more,
        )

    async def credit_balance(
        self, *, start: date, end: date, stock_codes: Sequence[str] = (), limit: int = DEFAULT_LIMIT, offset: int = 0
    ) -> CreditBalanceList:
        rows, has_more = await self._repository.credit_balance(
            start=start, end=end, stock_codes=stock_codes, limit=limit, offset=offset
        )
        return CreditBalanceList(
            items=tuple(credit_of(row) for row in rows), limit=limit, offset=offset, has_more=has_more
        )

    async def market_funds(
        self, *, start: date, end: date, limit: int = DEFAULT_LIMIT, offset: int = 0
    ) -> MarketFundsList:
        rows, has_more = await self._repository.market_funds(
            start=start, end=end, limit=limit, offset=offset
        )
        return MarketFundsList(
            items=tuple(funds_of(row) for row in rows), limit=limit, offset=offset, has_more=has_more
        )

    async def credit_ranking(
        self, *, standard_date: date | None, limit: int = DEFAULT_LIMIT, offset: int = 0
    ) -> CreditRankingList:
        rows, has_more, dates = await self._repository.credit_ranking(
            standard_date=standard_date, limit=limit, offset=offset
        )
        return CreditRankingList(
            items=tuple(ranking_of(row) for row in rows),
            limit=limit,
            offset=offset,
            has_more=has_more,
            standard_dates=tuple(dates),
        )
