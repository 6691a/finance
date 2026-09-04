"""코스피 일일 전망 라우트.

**축이 `run_date`다.** 이미 KST 날짜라 시각 경계로 바꿀 것이 없다 — 다른 화면의
`from`·`to`가 UTC 경계로 번역되는 것과 다르다.

**상세의 키가 `(run_date, slot)`이다.** 자연키가 그것이라 `id`를 쓰면 같은 슬롯이 두
주소를 갖는다.
"""

from datetime import date, timedelta
from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Query

from apps.api.container import ApiContainer
from apps.api.repository import DEFAULT_LIMIT, FORECAST_SLOTS, MAX_LIMIT
from apps.api.schemas import ForecastAccuracy, ForecastDetail, ForecastList
from apps.api.service import ForecastReadService
from apps.core.utility import kst_today

router = APIRouter(prefix="/api/forecasts", tags=["forecast"])

# 기본 창(일). 하루 셋이라 2주면 마흔둘이고 한 쪽에 들어온다.
DEFAULT_WINDOW_DAYS = 13

# 집계의 기본 창(일). 20영업일쯤이다 — 판 동결 기간이 그 길이다.
ACCURACY_WINDOW_DAYS = 27

ServiceDep = Annotated[ForecastReadService, Depends(Provide[ApiContainer.forecast_service])]

Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT, description="쪽 크기")]
Offset = Annotated[int, Query(ge=0, description="건너뛸 건수")]


def _days(start: date | None, end: date | None, window: int) -> tuple[date, date]:
    to_day = end or kst_today()
    return start or to_day - timedelta(days=window), to_day


@router.get("", response_model=ForecastList)
@inject
async def list_forecasts(
    service: ServiceDep,
    run_from: Annotated[date | None, Query(alias="from", description="세션 날짜(KST, 포함)")] = None,
    run_to: Annotated[date | None, Query(alias="to", description="세션 날짜(KST, 포함)")] = None,
    slot: Annotated[
        list[str] | None,
        Query(description=f"슬롯. 여러 번 줄 수 있다. {' · '.join(FORECAST_SLOTS)}"),
    ] = None,
    graded: Annotated[bool | None, Query(description="채점 여부. 안 주면 둘 다")] = None,
    hit: Annotated[bool | None, Query(description="방향 적중 여부. 채점된 것만 걸린다")] = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> ForecastList:
    """전망 목록. 최신 날짜 먼저, 같은 날은 슬롯 시간 순이다.

    **`reasons`와 `input_state`는 없다** — 상세가 준다. 한 건이 수 KB라 목록에 실으면
    한 쪽이 메가 단위가 된다.
    """
    from_day, to_day = _days(run_from, run_to, DEFAULT_WINDOW_DAYS)
    return await service.list_page(
        run_from=from_day,
        run_to=to_day,
        slots=slot or (),
        graded=graded,
        hit=hit,
        limit=limit,
        offset=offset,
    )


@router.get("/accuracy", response_model=ForecastAccuracy)
@inject
async def read_accuracy(
    service: ServiceDep,
    run_from: Annotated[date | None, Query(alias="from", description="세션 날짜(KST, 포함)")] = None,
    run_to: Annotated[date | None, Query(alias="to", description="세션 날짜(KST, 포함)")] = None,
) -> ForecastAccuracy:
    """슬롯별 채점 집계.

    **`/{run_date}` 보다 먼저 등록한다** — 정적 경로가 뒤면 `accuracy`를 날짜로 파싱하려다
    422가 된다. 이 파일 안의 함수 정의 순서가 그 계약이다.

    표본 수(`graded`)를 반드시 함께 준다. 비율만 보이면 3건에서 나온 67%가 100건에서 나온
    67%처럼 읽힌다.
    """
    from_day, to_day = _days(run_from, run_to, ACCURACY_WINDOW_DAYS)
    return await service.accuracy(run_from=from_day, run_to=to_day)


@router.get("/{run_date}/{slot}", response_model=ForecastDetail)
@inject
async def read_forecast(run_date: date, slot: str, service: ServiceDep) -> ForecastDetail:
    """전망 하나. 이유 목록과 모델이 본 관측 상태 전부가 여기에만 있다."""
    detail = await service.detail(run_date, slot)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"forecast {run_date}/{slot} not found")
    return detail
