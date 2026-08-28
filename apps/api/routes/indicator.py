"""지표 시계열 라우트.

**`/series`와 `/curve`는 정적 경로라 관측값 라우트보다 먼저 등록한다.** 관측값은
`(provider, series_id)` 둘을 함께 받아야 해서 경로 인자가 아니라 query로 둔다 —
`series_id` 하나로 거는 조회는 제공처가 늘어나면 조용히 틀린다.

`from`·`to`의 축은 **관측일**이고 그 기준 시간대는 제공처가 정한다(ECOS는 KST 고시일,
FRED는 미국 영업일). 우리가 다시 바꾸지 않는다.
"""

from datetime import date, timedelta
from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Query

from apps.api.container import ApiContainer
from apps.api.repository import DEFAULT_LIMIT, MAX_LIMIT
from apps.api.schemas import CurveResponse, IndicatorPoints, IndicatorSeriesList
from apps.api.service import IndicatorReadService
from apps.api.service.indicator import UnknownSeries
from apps.core.utility import kst_today

router = APIRouter(prefix="/api/indicators", tags=["indicator"])

# 관측값 기본 창(일). 대부분 일별이라 1년이면 250점 안팎이다.
DEFAULT_WINDOW_DAYS = 365

ServiceDep = Annotated[
    IndicatorReadService,
    Depends(Provide[ApiContainer.indicator_service]),
]

# **행을 주는 라우트는 전부 쪽으로 낸다.**
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT, description="쪽 크기")]
Offset = Annotated[int, Query(ge=0, description="건너뛸 건수")]


@router.get("/series", response_model=IndicatorSeriesList)
@inject
async def list_series(
    service: ServiceDep,
    kind: Annotated[
        list[str] | None,
        Query(description="government_bond·money_market·price_index·activity. 여러 번 줄 수 있다"),
    ] = None,
    country: Annotated[list[str] | None, Query(description="ISO 3166-1 alpha-2. 유로 지역은 XM")] = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> IndicatorSeriesList:
    """시계열 마스터와 **실제로 쌓인 구간**."""
    return await service.series(
        kinds=kind or (), countries=country or (), limit=limit, offset=offset
    )


@router.get("/curve", response_model=CurveResponse)
@inject
async def read_curve(
    service: ServiceDep,
    as_of: Annotated[date | None, Query(description="기준일. 계열마다 이 날짜 이전 마지막 값을 쓴다")] = None,
    country: Annotated[list[str] | None, Query(description="국가 코드. 여러 번 줄 수 있다")] = None,
) -> CurveResponse:
    """국가별 국채 곡선. **국채이고 만기가 있는 계열만 태운다.**

    나라마다 마지막 고시일이 달라 계열마다 기준일 이전의 마지막 값을 집는다. 그래서 한
    곡선 안에서도 점마다 관측일이 다를 수 있고, 그것이 사실이라 감추지 않는다.
    """
    return await service.curve(as_of=as_of or kst_today(), countries=country or ())


@router.get("/observations", response_model=IndicatorPoints)
@inject
async def read_observations(
    service: ServiceDep,
    provider: Annotated[str, Query(description="fred·ecos·mof·boe·bbk·ecb")],
    series_id: Annotated[str, Query(description="제공처 안에서 고유한 식별자")],
    start: Annotated[date | None, Query(alias="from", description="시작 관측일(포함)")] = None,
    end: Annotated[date | None, Query(alias="to", description="종료 관측일(포함)")] = None,
) -> IndicatorPoints:
    """한 시계열의 관측값. 기본 창은 오늘까지 1년이다."""
    to_day = end or kst_today()
    try:
        return await service.points(
            provider=provider,
            series_id=series_id,
            start=start or to_day - timedelta(days=DEFAULT_WINDOW_DAYS),
            end=to_day,
        )
    except UnknownSeries as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
