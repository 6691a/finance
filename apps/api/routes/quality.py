"""품질 집계 라우트.

**`/api/theses/quality`는 `/api/theses/{thesis_id}`보다 먼저 등록해야 한다.** 정적 경로가
뒤에 오면 동적 id가 먼저 물어 `quality`를 정수로 파싱하려다 422를 낸다. 순서를 정하는
자리는 `apps/api/routes/__init__.py`의 `routers` 튜플이고, `tests/api/test_routes.py`가
실제 요청까지 보내 그것을 확인한다.

경로가 `/api/theses` 아래인 이유는 이 집계가 추론의 품질이기 때문이다. 그래서 파일은
따로 두되 `prefix`는 같은 것을 쓴다.
"""

from datetime import date, timedelta
from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query

from apps.api.container import ApiContainer
from apps.api.schemas import QualityResponse
from apps.api.service import QualityReadService
from apps.core.utility import kst_today

router = APIRouter(prefix="/api/theses", tags=["quality"])

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
    slot: Annotated[list[str] | None, Query(description="RunSlot 값. 여러 번 줄 수 있다")] = None,
    subject_code: Annotated[list[str] | None, Query(description="대상 코드. 여러 번 줄 수 있다")] = None,
    horizon_days: Annotated[list[int] | None, Query(description="채점 지평(0·1·3·5)")] = None,
) -> QualityResponse:
    """주 단위 예측 품질과 해설 품질. **표 둘을 한 응답에 담고 합치지 않는다.**"""
    to_day = run_date_to or kst_today()
    return await service.summary(
        run_date_from=run_date_from or to_day - timedelta(days=DEFAULT_QUALITY_WINDOW_DAYS),
        run_date_to=to_day,
        run_slots=slot or (),
        subject_codes=subject_code or (),
        horizon_days=horizon_days or (),
    )
