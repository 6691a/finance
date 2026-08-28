"""LLM 실행 원장 라우트.

**실행일 필터의 축은 `started_at`이다.** 원 추론의 `run_date`가 아니다 — T+5 해설의
`run_date`는 과거 원 추론일이라, 그것으로 거르면 오늘 실행한 해설이 목록에서 빠진다.
KST 날짜를 받아 UTC 경계로 바꾸는 것은 `apps/core/utility.kst_day_bounds`가 한다.

**대상(`subject_code`) 필터를 두지 않는다.** 대화 하나가 여러 대상을 다루고 실패·중단
대화에는 산출물이 아예 없다. 대상으로 찾는 것은 `/api/theses`가 답한다.
"""

from datetime import date, timedelta
from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Query

from apps.api.container import ApiContainer
from apps.api.repository import DEFAULT_LIMIT, DEFAULT_WINDOW_DAYS, MAX_LIMIT
from apps.api.schemas import LlmRunDetail, LlmRunList, ToolCallDetail
from apps.api.service import LlmRunReadService
from apps.core.utility import kst_day_bounds, kst_today

router = APIRouter(prefix="/api/llm-runs", tags=["llm-run"])

ServiceDep = Annotated[
    LlmRunReadService,
    Depends(Provide[ApiContainer.llm_run_service]),
]


@router.get("", response_model=LlmRunList)
@inject
async def list_llm_runs(
    service: ServiceDep,
    started_from: Annotated[date | None, Query(alias="from", description="실행 시작일(KST, 포함)")] = None,
    started_to: Annotated[date | None, Query(alias="to", description="실행 종료일(KST, 포함)")] = None,
    kind: Annotated[list[str] | None, Query(description="LlmRunKind 값. 여러 번 줄 수 있다")] = None,
    status: Annotated[
        list[str] | None, Query(description="running·succeeded·failed. 여러 번 줄 수 있다")
    ] = None,
    slot: Annotated[list[str] | None, Query(description="RunSlot 값. 여러 번 줄 수 있다")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> LlmRunList:
    """실행 목록. 기본 창은 KST 오늘까지 14일이다."""
    to_day = started_to or kst_today()
    from_day = started_from or to_day - timedelta(days=DEFAULT_WINDOW_DAYS)
    start, end = kst_day_bounds(from_day, to_day)
    return await service.list_page(
        started_from=start,
        started_to=end,
        kinds=kind or (),
        statuses=status or (),
        run_slots=slot or (),
        limit=limit,
        offset=offset,
    )


@router.get("/{llm_run_id}", response_model=LlmRunDetail)
@inject
async def read_llm_run(llm_run_id: int, service: ServiceDep) -> LlmRunDetail:
    """실행 하나. **툴 결과 전문은 없다** — 호출 하나를 골랐을 때 단건 라우트가 준다."""
    detail = await service.detail(llm_run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"llm run {llm_run_id} not found")
    return detail


@router.get("/{llm_run_id}/tool-calls/{seq}", response_model=ToolCallDetail)
@inject
async def read_tool_call(llm_run_id: int, seq: int, service: ServiceDep) -> ToolCallDetail:
    """툴 호출 하나. 결과 전문이 여기에만 있다."""
    detail = await service.tool_call(llm_run_id, seq)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"tool call {llm_run_id}/{seq} not found")
    return detail
