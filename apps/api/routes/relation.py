"""요인 관계와 메모 라우트. 원본이 Neo4j다.

**그래프가 없으면 503이다.** 빈 목록으로 위장하면 "관측이 없다"와 "저장소가 없다"가
같아 보인다 — 옛 인과 그래프 라우트가 같은 판단을 하고 있다.

**메모가 `/api/relations/memories`다.** 층 넷에서 리소스 이름이 같아야 한다는 규칙 때문에
`/api/memories`를 따로 두지 않았다. 메모는 관계로 담기지 않는 것을 담는 자리라 같은
리소스로 읽는 것이 뜻에도 맞는다.
"""

from datetime import date
from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Query

from apps.api.container import ApiContainer
from apps.api.repository import DEFAULT_LIMIT, MAX_LIMIT
from apps.api.schemas import MemoryItem, MemoryList, ObservationList, RelationGraph, RelationList
from apps.api.service import RelationReadService
from apps.core.utility import kst_today

router = APIRouter(prefix="/api/relations", tags=["relation"])

ServiceDep = Annotated[RelationReadService, Depends(Provide[ApiContainer.relation_service])]

Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT, description="쪽 크기")]
Offset = Annotated[int, Query(ge=0, description="건너뛸 건수")]
AsOf = Annotated[
    date | None,
    Query(alias="as_of", description="가중치의 기준일(KST). 안 주면 오늘이다"),
]

OFFLINE = "neo4j is not configured"


def _guard(service: RelationReadService) -> None:
    if not service.enabled:
        raise HTTPException(status_code=503, detail=OFFLINE)


@router.get("", response_model=RelationList)
@inject
async def list_relations(
    service: ServiceDep,
    as_of: AsOf = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> RelationList:
    """요인 **전부**의 가중치. 관측이 없는 요인도 행이 있고 `n_obs`가 0이다.

    빈 칸으로 두면 "관계 없음"으로 읽힌다 — 0은 "아직 모른다"이고 화면이 그 뜻을 밝힌다.
    """
    _guard(service)
    return service.relations(as_of_date=as_of or kst_today(), limit=limit, offset=offset)


@router.get("/graph", response_model=RelationGraph)
@inject
async def read_graph(service: ServiceDep, as_of: AsOf = None) -> RelationGraph:
    """관계 그림. 요인이 코스피 하나를 가리키는 **깊이 1의 별 모양**이다.

    **`/{factor}` 보다 먼저 등록한다** — 정적 경로가 뒤면 `graph`가 요인 코드로 물린다.
    """
    _guard(service)
    return service.graph(as_of_date=as_of or kst_today())


@router.get("/memories", response_model=MemoryList)
@inject
async def list_memories(
    service: ServiceDep,
    retired: Annotated[
        bool | None,
        Query(description="true면 내린 것만, false면 활성만. 안 주면 둘 다"),
    ] = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> MemoryList:
    """메모 목록. **내린 것도 준다** — 왜 지웠는지가 남아 있어야 한다."""
    _guard(service)
    return service.memories(retired=retired, limit=limit, offset=offset)


@router.get("/memories/{memory_id}", response_model=MemoryItem)
@inject
async def read_memory(memory_id: int, service: ServiceDep) -> MemoryItem:
    """메모 하나. 전망의 이유가 `memory_id`로 인용한 것을 여는 자리다."""
    _guard(service)
    found = service.memory(memory_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"memory {memory_id} not found")
    return found


@router.get("/{factor}", response_model=ObservationList)
@inject
async def list_observations(
    factor: str,
    service: ServiceDep,
    as_of: AsOf = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> ObservationList:
    """요인 하나의 관측. 최신순이고 관측마다 오늘 기준 무게가 붙는다."""
    _guard(service)
    return service.observations(
        factor, as_of_date=as_of or kst_today(), limit=limit, offset=offset
    )
