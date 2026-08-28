"""인과 그래프 라우트.

**축이 주(week)다.** 다른 화면의 `from`·`to`가 거래일·발행일인 것과 달리 여기서는
`market_causal_path.week_start`(월요일)를 자른다. 주 하나가 그래프 한 판이라 날짜를 좁히는
것이 곧 판을 고르는 것이다.
"""

from datetime import date, timedelta
from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Query

from apps.api.container import ApiContainer
from apps.api.repository import DEFAULT_LIMIT, MAX_LIMIT
from apps.api.schemas import (
    CausalChannelList,
    CausalEventList,
    CausalPathDetail,
    CausalPathList,
)
from apps.api.service import MarketCausalReadService
from apps.api.service.causal import UnknownPath
from apps.core.utility import kst_today

router = APIRouter(prefix="/api/causal", tags=["causal"])

# 기본 창(일). 주 단위로 쌓이므로 열두 주쯤이 기본이다.
DEFAULT_WINDOW_DAYS = 84

ServiceDep = Annotated[
    MarketCausalReadService,
    Depends(Provide[ApiContainer.causal_service]),
]

FromDay = Annotated[date | None, Query(alias="from", description="시작 주(포함). 주의 시작일")]
ToDay = Annotated[date | None, Query(alias="to", description="종료 주(포함)")]

# **행을 주는 라우트는 전부 쪽으로 낸다.**
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT, description="쪽 크기")]
Offset = Annotated[int, Query(ge=0, description="건너뛸 건수")]


def _days(start: date | None, end: date | None) -> tuple[date, date]:
    to_day = end or kst_today()
    return start or to_day - timedelta(days=DEFAULT_WINDOW_DAYS), to_day


@router.get("/paths", response_model=CausalPathList)
@inject
async def read_paths(
    service: ServiceDep,
    start: FromDay = None,
    end: ToDay = None,
    target_kind: Annotated[
        list[str] | None,
        Query(description="instrument·index·quote·indicator. **값의 성격이 아니라 저장소를 가른다**"),
    ] = None,
    target_code: Annotated[list[str] | None, Query(description="대상 식별자")] = None,
    event_id: Annotated[int | None, Query(description="이 사건에서 뻗은 경로만")] = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> CausalPathList:
    """사건 → 채널 체인 → 대상. **실현 등락에는 단위가 붙는다** — 7bp와 10%는 다른 축이다."""
    from_day, to_day = _days(start, end)
    return await service.paths(
        start=from_day,
        end=to_day,
        target_kinds=tuple(target_kind or ()),
        target_codes=tuple(target_code or ()),
        event_id=event_id,
        limit=limit,
        offset=offset,
    )


@router.get("/events", response_model=CausalEventList)
@inject
async def read_events(
    service: ServiceDep,
    start: FromDay = None,
    end: ToDay = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> CausalEventList:
    """그 주에 일어난 일. **경로가 0인 사건도 남는다** — 사건은 있는데 경로가 안 나온 것도 사실이다."""
    from_day, to_day = _days(start, end)
    return await service.events(start=from_day, end=to_day, limit=limit, offset=offset)


@router.get("/channels", response_model=CausalChannelList)
@inject
async def read_channels(
    service: ServiceDep,
    start: FromDay = None,
    end: ToDay = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> CausalChannelList:
    """전달 경로 이름과 그것을 거친 단계 수. 주가 쌓이면서 같은 채널을 공유한다."""
    from_day, to_day = _days(start, end)
    return await service.channels(start=from_day, end=to_day, limit=limit, offset=offset)


@router.get("/paths/{path_id}", response_model=CausalPathDetail)
@inject
async def read_path(path_id: int, service: ServiceDep) -> CausalPathDetail:
    """경로 하나와 **그 사건이 그 주에 뻗은 경로 전부**.

    **`/paths`보다 뒤에 등록한다.** 정적 경로가 뒤에 오면 `paths`를 정수로 파싱하려다 422가
    된다 — 이 파일 안의 함수 정의 순서가 그 계약이다.
    """
    try:
        return await service.detail(path_id)
    except UnknownPath as error:
        raise HTTPException(status_code=404, detail=f"causal path {error} not found") from error
