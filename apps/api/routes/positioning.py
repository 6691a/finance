"""수급·신용·대차·공매도 라우트.

**축이 둘이다.** 장중 스냅샷(`investor-flows`·`market-movement`)은 UTC 시각이고 나머지는
거래일이다. 그래서 앞의 둘만 KST 날짜를 받아 `kst_day_bounds`로 시각 구간을 만든다 —
분 단위 데이터라 날짜 경계가 곧 그 세션의 경계다.
"""

from datetime import date, timedelta
from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query

from apps.api.container import ApiContainer
from apps.api.repository import DEFAULT_LIMIT, MAX_LIMIT
from apps.api.schemas import (
    CreditBalanceList,
    CreditRankingList,
    InvestorEstimateList,
    InvestorFlowList,
    LendingList,
    MarketFundsList,
    MarketMovementList,
    ShortSaleList,
    StockFlowList,
)
from apps.api.service import PositioningReadService
from apps.core.utility import kst_day_bounds, kst_today

router = APIRouter(prefix="/api/positioning", tags=["positioning"])

# 장중 스냅샷의 기본 창(일). 분 단위라 하루도 400행에 가깝다.
DEFAULT_INTRADAY_DAYS = 1

# 일별 확정값의 기본 창(일).
DEFAULT_DAILY_DAYS = 90

ServiceDep = Annotated[
    PositioningReadService,
    Depends(Provide[ApiContainer.positioning_service]),
]

FromDay = Annotated[date | None, Query(alias="from", description="시작일(KST, 포함)")]
ToDay = Annotated[date | None, Query(alias="to", description="종료일(KST, 포함)")]
StockCodes = Annotated[list[str] | None, Query(description="6자리 종목코드. 여러 번 줄 수 있다")]

# **행을 주는 라우트는 전부 쪽으로 낸다.** 상한을 두는 이유는 날짜 구간이 넓을 때의
# 폭주를 막는 것이고, 실질 페이지네이션은 구간을 좁히는 것이다.
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT, description="쪽 크기")]
Offset = Annotated[int, Query(ge=0, description="건너뛸 건수")]


def _days(start: date | None, end: date | None, span: int) -> tuple[date, date]:
    to_day = end or kst_today()
    return start or to_day - timedelta(days=span), to_day


@router.get("/investor-flows", response_model=InvestorFlowList)
@inject
async def read_investor_flows(
    service: ServiceDep,
    start: FromDay = None,
    end: ToDay = None,
    market: Annotated[list[str] | None, Query(description="KOSPI·KOSDAQ")] = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> InvestorFlowList:
    """시장 전체의 **장중** 누적 매매동향. 분 단위 추정이고 확정값이 아니다."""
    from_day, to_day = _days(start, end, DEFAULT_INTRADAY_DAYS)
    window = kst_day_bounds(from_day, to_day)
    return await service.investor_flows(
        start=window[0], end=window[1], markets=market or (), limit=limit, offset=offset
    )


@router.get("/market-movement", response_model=MarketMovementList)
@inject
async def read_market_movement(
    service: ServiceDep,
    start: FromDay = None,
    end: ToDay = None,
    symbol: Annotated[list[str] | None, Query(description="KOSPI·KOSDAQ")] = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> MarketMovementList:
    """상승·보합·하락 종목 수. 시장의 폭을 본다."""
    from_day, to_day = _days(start, end, DEFAULT_INTRADAY_DAYS)
    window = kst_day_bounds(from_day, to_day)
    return await service.market_movement(
        start=window[0], end=window[1], symbols=symbol or (), limit=limit, offset=offset
    )


@router.get("/stock-flows", response_model=StockFlowList)
@inject
async def read_stock_flows(
    service: ServiceDep,
    start: FromDay = None,
    end: ToDay = None,
    stock_code: StockCodes = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> StockFlowList:
    """종목별 투자자 매매동향의 **확정** 일별값. 가격도 같은 행에 있다."""
    from_day, to_day = _days(start, end, DEFAULT_DAILY_DAYS)
    return await service.stock_flows(
        start=from_day, end=to_day, stock_codes=stock_code or (), limit=limit, offset=offset
    )


@router.get("/estimates", response_model=InvestorEstimateList)
@inject
async def read_estimates(
    service: ServiceDep,
    start: FromDay = None,
    end: ToDay = None,
    stock_code: StockCodes = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> InvestorEstimateList:
    """종목별 외국인·기관 **추정** 순매수. 같은 거래일에 슬롯마다 온다."""
    from_day, to_day = _days(start, end, DEFAULT_DAILY_DAYS)
    return await service.estimates(
        start=from_day, end=to_day, stock_codes=stock_code or (), limit=limit, offset=offset
    )


@router.get("/short-sale", response_model=ShortSaleList)
@inject
async def read_short_sale(
    service: ServiceDep,
    start: FromDay = None,
    end: ToDay = None,
    stock_code: StockCodes = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> ShortSaleList:
    """종목별 공매도. 비중은 제공처가 준 값 그대로다."""
    from_day, to_day = _days(start, end, DEFAULT_DAILY_DAYS)
    return await service.short_sale(
        start=from_day, end=to_day, stock_codes=stock_code or (), limit=limit, offset=offset
    )


@router.get("/lending", response_model=LendingList)
@inject
async def read_lending(
    service: ServiceDep,
    start: FromDay = None,
    end: ToDay = None,
    stock_code: StockCodes = None,
    market: Annotated[list[str] | None, Query(description="KOSPI·KOSDAQ")] = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> LendingList:
    """대차거래. **시장과 종목을 배열 둘로 나눠 낸다** — 축은 같지만 단위와 뜻이 다르다."""
    from_day, to_day = _days(start, end, DEFAULT_DAILY_DAYS)
    return await service.lending(
        start=from_day,
        end=to_day,
        stock_codes=stock_code or (),
        markets=market or (),
        limit=limit,
        offset=offset,
    )


@router.get("/credit", response_model=CreditBalanceList)
@inject
async def read_credit(
    service: ServiceDep,
    start: FromDay = None,
    end: ToDay = None,
    stock_code: StockCodes = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> CreditBalanceList:
    """종목별 신용잔고. **축이 거래일이고 결제일은 따로 있다.**"""
    from_day, to_day = _days(start, end, DEFAULT_DAILY_DAYS)
    return await service.credit_balance(
        start=from_day, end=to_day, stock_codes=stock_code or (), limit=limit, offset=offset
    )


@router.get("/funds", response_model=MarketFundsList)
@inject
async def read_funds(
    service: ServiceDep,
    start: FromDay = None,
    end: ToDay = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> MarketFundsList:
    """증시자금 종합. 고객예탁금·신용융자·펀드가 한 행이다."""
    from_day, to_day = _days(start, end, DEFAULT_DAILY_DAYS)
    return await service.market_funds(start=from_day, end=to_day, limit=limit, offset=offset)


@router.get("/credit-ranking", response_model=CreditRankingList)
@inject
async def read_credit_ranking(
    service: ServiceDep,
    standard_date: Annotated[date | None, Query(description="기준일. 비우면 가장 최근 날")] = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> CreditRankingList:
    """융자잔고 상위 종목. **하루를 고른다** — 날짜마다 순위가 다시 매겨져 구간으로 주면
    같은 종목이 여러 번 나온다. 고를 수 있는 날짜 목록을 함께 낸다."""
    return await service.credit_ranking(standard_date=standard_date, limit=limit, offset=offset)
