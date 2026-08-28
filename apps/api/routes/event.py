"""사건·신호·투자의견 라우트.

주장과 판정의 축은 **시각**(`stated_at`·`announced_at`)이고 신호·의견의 축은 **거래일**이다.
전자는 "언제 그렇게 말했나"라 분 단위가 뜻을 갖고, 후자는 하루가 단위다.
"""

from datetime import date, timedelta
from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query

from apps.api.container import ApiContainer
from apps.api.repository import DEFAULT_LIMIT, MAX_LIMIT
from apps.api.schemas import (
    AnalystOpinionList,
    EventClaimList,
    EventExtractionList,
    EventOutcomeList,
    SignalList,
)
from apps.api.service import EventReadService
from apps.core.utility import kst_day_bounds, kst_today

router = APIRouter(prefix="/api/events", tags=["event"])

# 기본 창(일). 행이 전부 합쳐 이천 행대라 넉넉히 잡는다.
DEFAULT_WINDOW_DAYS = 90

ServiceDep = Annotated[
    EventReadService,
    Depends(Provide[ApiContainer.event_service]),
]

FromDay = Annotated[date | None, Query(alias="from", description="시작일(KST, 포함)")]
ToDay = Annotated[date | None, Query(alias="to", description="종료일(KST, 포함)")]
StockCodes = Annotated[list[str] | None, Query(description="6자리 종목코드. 여러 번 줄 수 있다")]

# **행을 주는 라우트는 전부 쪽으로 낸다.**
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT, description="쪽 크기")]
Offset = Annotated[int, Query(ge=0, description="건너뛸 건수")]


def _days(start: date | None, end: date | None) -> tuple[date, date]:
    to_day = end or kst_today()
    return start or to_day - timedelta(days=DEFAULT_WINDOW_DAYS), to_day


@router.get("/claims", response_model=EventClaimList)
@inject
async def read_claims(
    service: ServiceDep,
    start: FromDay = None,
    end: ToDay = None,
    stock_code: StockCodes = None,
    claim_kind: Annotated[
        list[str] | None, Query(description="expectation(기대)·actual(실제)")
    ] = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> EventClaimList:
    """문서에서 뽑은 주장. **발표 전 기대만 판정에 쓰이지만 목록은 전부 보인다.**"""
    from_day, to_day = _days(start, end)
    window = kst_day_bounds(from_day, to_day)
    return await service.claims(
        start=window[0],
        end=window[1],
        stock_codes=stock_code or (),
        claim_kinds=claim_kind or (),
        limit=limit,
        offset=offset,
    )


@router.get("/outcomes", response_model=EventOutcomeList)
@inject
async def read_outcomes(
    service: ServiceDep,
    start: FromDay = None,
    end: ToDay = None,
    stock_code: StockCodes = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> EventOutcomeList:
    """기대 대비 실제의 판정. **첫 성공본 불변이라 다시 내는 손잡이가 없다.**"""
    from_day, to_day = _days(start, end)
    window = kst_day_bounds(from_day, to_day)
    return await service.outcomes(
        start=window[0], end=window[1], stock_codes=stock_code or (), limit=limit, offset=offset
    )


@router.get("/extractions", response_model=EventExtractionList)
@inject
async def read_extractions(
    service: ServiceDep,
    start: FromDay = None,
    end: ToDay = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> EventExtractionList:
    """추출 원장. **주장 0건 행이 대부분이고 그것이 요점이다** — 안 뽑은 것과 구분된다."""
    from_day, to_day = _days(start, end)
    window = kst_day_bounds(from_day, to_day)
    return await service.extractions(start=window[0], end=window[1], limit=limit, offset=offset)


@router.get("/signals", response_model=SignalList)
@inject
async def read_signals(
    service: ServiceDep,
    start: FromDay = None,
    end: ToDay = None,
    symbol: Annotated[list[str] | None, Query(description="대상 심볼")] = None,
    kind: Annotated[list[str] | None, Query(description="신호 종류")] = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> SignalList:
    """확정 일봉에서 검출한 기술적 매매 신호."""
    from_day, to_day = _days(start, end)
    return await service.signals(
        start=from_day,
        end=to_day,
        symbols=symbol or (),
        kinds=kind or (),
        limit=limit,
        offset=offset,
    )


@router.get("/analyst-opinions", response_model=AnalystOpinionList)
@inject
async def read_opinions(
    service: ServiceDep,
    start: FromDay = None,
    end: ToDay = None,
    stock_code: StockCodes = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> AnalystOpinionList:
    """증권사 투자의견·목표주가. 구조화된 숫자는 문서가 아니라 여기 있다."""
    from_day, to_day = _days(start, end)
    return await service.opinions(
        start=from_day, end=to_day, stock_codes=stock_code or (), limit=limit, offset=offset
    )
