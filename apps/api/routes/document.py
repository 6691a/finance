"""문서·공시 라우트.

**`/sources`·`/disclosures`·`/earnings`는 `/{document_id}`보다 먼저 등록한다.** 정적 경로가
뒤에 오면 동적 id가 먼저 물어 `sources`를 정수로 파싱하려다 422를 낸다. 이 파일 안의 함수
정의 순서가 그 계약이다.

문서 축은 **발행 시각(UTC)**이고 공시 축은 **접수일(KST)**이다. 제공처가 정한 축이 달라
같은 이름의 파라미터가 라우트마다 다른 것을 가리킨다.
"""

from datetime import date, timedelta
from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Query

from apps.api.container import ApiContainer
from apps.api.repository import DEFAULT_LIMIT, DEFAULT_WINDOW_DAYS, MAX_LIMIT
from apps.api.schemas import (
    DisclosureList,
    DocumentDetail,
    DocumentList,
    DocumentSourceList,
    EarningsFactList,
)
from apps.api.service import DocumentReadService
from apps.api.service.document import UnknownDocument
from apps.core.utility import kst_day_bounds, kst_today

router = APIRouter(prefix="/api/documents", tags=["document"])

ServiceDep = Annotated[
    DocumentReadService,
    Depends(Provide[ApiContainer.document_service]),
]

# **행을 주는 라우트는 전부 쪽으로 낸다.**
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT, description="쪽 크기")]
Offset = Annotated[int, Query(ge=0, description="건너뛸 건수")]


@router.get("/sources", response_model=DocumentSourceList)
@inject
async def list_sources(
    service: ServiceDep, limit: Limit = DEFAULT_LIMIT, offset: Offset = 0
) -> DocumentSourceList:
    """출처와 각각의 문서 수·최신성. **`enabled`가 카테고리를 끄는 손잡이다.**"""
    return await service.sources(limit=limit, offset=offset)


@router.get("/disclosures", response_model=DisclosureList)
@inject
async def list_disclosures(
    service: ServiceDep,
    receipt_from: Annotated[date | None, Query(alias="from", description="접수일(KST, 포함)")] = None,
    receipt_to: Annotated[date | None, Query(alias="to", description="접수일(KST, 포함)")] = None,
    stock_code: Annotated[list[str] | None, Query(description="6자리 종목코드. 여러 번 줄 수 있다")] = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> DisclosureList:
    """DART 공시 접수. 기본 창은 오늘까지 14일이다."""
    to_day = receipt_to or kst_today()
    return await service.disclosures(
        receipt_from=receipt_from or to_day - timedelta(days=DEFAULT_WINDOW_DAYS),
        receipt_to=to_day,
        stock_codes=stock_code or (),
        limit=limit,
        offset=offset,
    )


@router.get("/earnings", response_model=EarningsFactList)
@inject
async def list_earnings(
    service: ServiceDep,
    stock_code: Annotated[list[str] | None, Query(description="6자리 종목코드")] = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> EarningsFactList:
    """공시에서 뽑은 실적 지표. **이것이 실제값의 원본이다** — 기사 산문에서 다시 뽑지 않는다."""
    return await service.earnings(stock_code or (), limit=limit, offset=offset)


@router.get("", response_model=DocumentList)
@inject
async def list_documents(
    service: ServiceDep,
    published_from: Annotated[date | None, Query(alias="from", description="발행일(KST, 포함)")] = None,
    published_to: Annotated[date | None, Query(alias="to", description="발행일(KST, 포함)")] = None,
    source: Annotated[list[str] | None, Query(description="출처 slug. 여러 번 줄 수 있다")] = None,
    min_score: Annotated[
        int | None,
        Query(ge=0, le=10, description="이 값 이상만. **미평가 문서는 함께 빠진다** — null은 낮음이 아니다"),
    ] = None,
    instrument: Annotated[str | None, Query(description="종목 태그(티커)")] = None,
    indicator: Annotated[str | None, Query(description="지표 태그(series_id)")] = None,
    q: Annotated[str | None, Query(description="제목·요약 부분 일치")] = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> DocumentList:
    """문서 목록. **본문은 없다** — 상세가 준다. 기본 창은 오늘까지 14일이다."""
    to_day = published_to or kst_today()
    from_day = published_from or to_day - timedelta(days=DEFAULT_WINDOW_DAYS)
    start, end = kst_day_bounds(from_day, to_day)
    return await service.list_page(
        published_from=start,
        published_to=end,
        sources=source or (),
        min_score=min_score,
        instrument=instrument,
        indicator=indicator,
        search=q,
        limit=limit,
        offset=offset,
    )


@router.get("/{document_id}", response_model=DocumentDetail)
@inject
async def read_document(document_id: int, service: ServiceDep) -> DocumentDetail:
    """문서 하나. 본문과 LLM 평가를 함께 낸다."""
    try:
        return await service.detail(document_id)
    except UnknownDocument as error:
        raise HTTPException(status_code=404, detail=f"document {error} not found") from error
