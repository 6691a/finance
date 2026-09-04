"""품질 집계 라우트.

**`/api/forecasts/quality`는 `/api/forecasts/{run_date}/{slot}`보다 먼저 등록해야 한다.**
경로 조각 수가 달라 실제로는 안 물리지만, 순서를 정하는 자리는
`apps/api/routes/__init__.py`의 `routers` 튜플이고 `tests/api/test_routes.py`가 실제 요청까지
보내 그것을 확인한다.

경로가 `/api/forecasts` 아래인 이유는 이 집계가 전망의 품질이기 때문이다. 그래서 파일은
따로 두되 `prefix`는 같은 것을 쓴다.
"""

from datetime import date, timedelta
from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query

from apps.api.container import ApiContainer
from apps.api.repository import FORECAST_SLOTS
from apps.api.schemas import QualityResponse
from apps.api.service import QualityReadService
from apps.core.utility import kst_today

router = APIRouter(prefix="/api/forecasts", tags=["quality"])

# 기본 창(일). 주 단위 집계라 목록의 14일보다 길다 — 네 주가 최소 비교 단위다.
DEFAULT_QUALITY_WINDOW_DAYS = 27

ServiceDep = Annotated[
    QualityReadService,
    Depends(Provide[ApiContainer.quality_service]),
]


@router.get("/quality", response_model=QualityResponse)
@inject
async def read_quality(
    service: ServiceDep,
    run_date_from: Annotated[date | None, Query(alias="from", description="시작일(KST, 포함)")] = None,
    run_date_to: Annotated[date | None, Query(alias="to", description="종료일(KST, 포함)")] = None,
    slot: Annotated[
        list[str] | None,
        Query(description=f"슬롯. 여러 번 줄 수 있다. {' · '.join(FORECAST_SLOTS)}"),
    ] = None,
) -> QualityResponse:
    """주 단위 전망 품질과 관찰 품질. **표 둘을 한 응답에 담고 합치지 않는다.**"""
    to_day = run_date_to or kst_today()
    return await service.summary(
        run_date_from=run_date_from or to_day - timedelta(days=DEFAULT_QUALITY_WINDOW_DAYS),
        run_date_to=to_day,
        slots=slot or (),
    )
