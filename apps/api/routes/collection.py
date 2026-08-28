"""수집 원장과 마스터 라우트.

**이 화면의 질문은 "무엇이 안 들어오고 있나"다.** 그래서 요약이 먼저이고 레코드 목록이
그다음이다 — 2만 행을 훑어 이상을 찾는 화면이 아니다.
"""

from datetime import date, timedelta
from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query

from apps.api.container import ApiContainer
from apps.api.repository import DEFAULT_LIMIT, MAX_LIMIT
from apps.api.schemas import (
    InstrumentList,
    MarketSessionList,
    SourceHealthList,
    SourceRecordList,
)
from apps.api.service import CollectionReadService
from apps.core.utility import kst_day_bounds, kst_today

router = APIRouter(prefix="/api/collection", tags=["collection"])

# 요약이 보는 창(시간). 하루면 매일 도는 수집이 한 번은 들어 있다.
DEFAULT_HEALTH_HOURS = 24

# 레코드 목록의 기본 창(일).
DEFAULT_RECORD_DAYS = 2

# 캘린더의 기본 창(일). 앞뒤로 본다 — 다음 개장일이 궁금한 화면이다.
DEFAULT_SESSION_DAYS = 30

ServiceDep = Annotated[
    CollectionReadService,
    Depends(Provide[ApiContainer.collection_service]),
]

# **행을 주는 라우트는 전부 쪽으로 낸다.**
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT, description="쪽 크기")]
Offset = Annotated[int, Query(ge=0, description="건너뛸 건수")]


@router.get("/health", response_model=SourceHealthList)
@inject
async def read_health(
    service: ServiceDep,
    hours: Annotated[int, Query(ge=1, le=24 * 30, description="이 시간만큼 뒤를 본다")] = DEFAULT_HEALTH_HOURS,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> SourceHealthList:
    """출처별 최신성과 실패. **늦은 것이 위다** — 소식 없는 출처가 이 화면의 답이다."""
    since = kst_day_bounds(kst_today(), kst_today())[1] - timedelta(hours=hours)
    return await service.health(since, limit=limit, offset=offset)


@router.get("/records", response_model=SourceRecordList)
@inject
async def read_records(
    service: ServiceDep,
    start: Annotated[date | None, Query(alias="from", description="시작일(KST, 포함)")] = None,
    end: Annotated[date | None, Query(alias="to", description="종료일(KST, 포함)")] = None,
    source: Annotated[list[str] | None, Query(description="제공처 식별자")] = None,
    status: Annotated[list[str] | None, Query(description="succeeded·failed·running")] = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> SourceRecordList:
    """수집 레코드. **`payload`는 없다** — 있는지 여부만 낸다."""
    to_day = end or kst_today()
    from_day = start or to_day - timedelta(days=DEFAULT_RECORD_DAYS)
    window = kst_day_bounds(from_day, to_day)
    return await service.records(
        start=window[0],
        end=window[1],
        sources=source or (),
        statuses=status or (),
        limit=limit,
        offset=offset,
    )


@router.get("/instruments", response_model=InstrumentList)
@inject
async def read_instruments(
    service: ServiceDep, limit: Limit = MAX_LIMIT, offset: Offset = 0
) -> InstrumentList:
    """추적 종목 마스터. `is_watched`가 수집·분석 대상 여부다.

    **여기만 기본 쪽 크기가 상한이다.** 화면이 종목 코드를 한국어 이름으로 바꾸는 표로
    쓰기 때문에, 기본값이 50이면 51번째 종목부터 코드가 그대로 보인다.
    """
    return await service.instruments(limit=limit, offset=offset)


@router.get("/sessions", response_model=MarketSessionList)
@inject
async def read_sessions(
    service: ServiceDep,
    start: Annotated[date | None, Query(alias="from", description="시작일(포함)")] = None,
    end: Annotated[date | None, Query(alias="to", description="종료일(포함)")] = None,
    market: Annotated[list[str] | None, Query(description="시장 코드")] = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> MarketSessionList:
    """개장 캘린더. 기본 창은 오늘 앞뒤 30일이다 — 다음 개장일을 보는 화면이다."""
    today = kst_today()
    return await service.sessions(
        start=start or today - timedelta(days=DEFAULT_SESSION_DAYS),
        end=end or today + timedelta(days=DEFAULT_SESSION_DAYS),
        markets=market or (),
        limit=limit,
        offset=offset,
    )
