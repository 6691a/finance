"""시장 추론 라우트. **컨테이너가 보이는 자리다.**

서비스는 `@inject`로 주입되어 들어오고, 자기가 어느 컨테이너에서 왔는지 모른다 —
업무 코드가 `container.thesis_service()`를 직접 부르면 그건 Service Locator이지
의존성 주입이 아니다.

**상세와 평가를 나누지 않는다.** 상세 화면은 언제나 둘 다 필요하다. 라우트를 가르면
클라이언트가 매번 두 번 부르고 우리는 같은 조인을 두 번 쓴다.
"""

from datetime import date, timedelta
from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Query

from apps.api.container import ApiContainer
from apps.api.repository import DEFAULT_LIMIT, DEFAULT_WINDOW_DAYS, MAX_LIMIT
from apps.api.schemas import GraphResponse, ThesisDetail, ThesisList
from apps.api.service import ThesisReadService
from apps.core.utility import kst_today

router = APIRouter(prefix="/api/theses", tags=["thesis"])

ServiceDep = Annotated[
    ThesisReadService,
    Depends(Provide[ApiContainer.thesis_service]),
]


@router.get("", response_model=ThesisList)
@inject
async def list_theses(
    service: ServiceDep,
    run_date_from: Annotated[date | None, Query(alias="from", description="시작일(KST, 포함)")] = None,
    run_date_to: Annotated[date | None, Query(alias="to", description="종료일(KST, 포함)")] = None,
    slot: Annotated[list[str] | None, Query(description="RunSlot 값. 여러 번 줄 수 있다")] = None,
    subject_code: Annotated[list[str] | None, Query(description="대상 코드. 여러 번 줄 수 있다")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ThesisList:
    """추론 목록. 기본 창은 오늘까지 14일이다."""
    to_day = run_date_to or kst_today()
    return await service.list_page(
        run_date_from=run_date_from or to_day - timedelta(days=DEFAULT_WINDOW_DAYS),
        run_date_to=to_day,
        run_slots=slot or (),
        subject_codes=subject_code or (),
        limit=limit,
        offset=offset,
    )


@router.get("/{thesis_id}", response_model=ThesisDetail)
@inject
async def read_thesis(thesis_id: int, service: ServiceDep) -> ThesisDetail:
    """상세 하나. 근거·평가·과거 추론·대화를 한 응답에 담는다."""
    detail = await service.detail(thesis_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"thesis {thesis_id} not found")
    return detail


@router.get("/{thesis_id}/graph", response_model=GraphResponse)
@inject
async def read_graph(thesis_id: int, service: ServiceDep) -> GraphResponse:
    """중심 추론의 1홉. 노드·엣지 이름은 4단계가 정한 Neo4j 모양 그대로다."""
    graph = await service.graph(thesis_id)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"thesis {thesis_id} not found")
    return graph
