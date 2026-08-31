"""문서·공시의 매핑.

**본문을 목록 매퍼가 만지지 않는다.** 상세 매퍼만 `body`를 읽어서, 목록 응답에 본문이
새어 들어갈 길 자체가 없다.
"""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from apps.api.repository import (
    DEFAULT_LIMIT,
    DocumentDetailRows,
    DocumentListRows,
    DocumentReadRepository,
    SourceRows,
)
from apps.api.schemas import (
    DisclosureItem,
    DisclosureList,
    DocumentAttachmentItem,
    DocumentDetail,
    DocumentList,
    DocumentSourceItem,
    DocumentSourceList,
    DocumentSummary,
    EarningsFactItem,
    EarningsFactList,
)
from apps.api.service.common import dart_url, number
from apps.models.content import Document, DocumentSource
from apps.models.market import DisclosureEvent, EarningsFact


class UnknownDocument(Exception):
    """없는 문서 id. 라우트가 404로 바꾼다."""


# DART 원문 뷰어. 접수번호 하나로 열리는 공개 주소라 저장하지 않고 만든다 —
# 저장하면 같은 값이 두 테이블에 생기고 제공처가 주소를 바꾼 날 둘이 갈린다.
def summary_of(
    document: Document, instruments: tuple[str, ...] = (), indicators: tuple[str, ...] = ()
) -> DocumentSummary:
    return DocumentSummary(
        id=document.id,
        source_slug=document.source_slug,
        external_id=document.external_id,
        title=document.title,
        document_type=document.document_type.value
        if hasattr(document.document_type, "value")
        else str(document.document_type),
        published_at=document.published_at,
        language=document.language,
        body_status=None
        if document.body_status is None
        else str(getattr(document.body_status, "value", document.body_status)),
        canonical_url=document.canonical_url,
        value_score=document.value_score,
        direction=None if document.direction is None else str(getattr(document.direction, "value", document.direction)),
        assessed_at=document.assessed_at,
        llm_model=document.llm_model,
        prompt_version=document.prompt_version,
        instruments=instruments,
        indicators=indicators,
    )


def attachment_of(row: Any) -> DocumentAttachmentItem:
    """**저장 경로를 밖으로 내지 않는다.** 마운트 안의 상대경로라 화면이 열 수 있는 주소가
    아니고, 그 자리를 알릴 이유도 없다 — 있는지 여부만 낸다."""
    return DocumentAttachmentItem(
        position=row.position,
        kind=str(getattr(row.kind, "value", row.kind)),
        url=row.url,
        filename=row.filename,
        media_type=row.media_type,
        byte_size=row.byte_size,
        stored=row.storage_path is not None,
        fetched_at=row.fetched_at,
    )


def detail_of(rows: DocumentDetailRows) -> DocumentDetail:
    document = rows.document
    return DocumentDetail(
        **summary_of(document, rows.instruments, rows.indicators).model_dump(),
        body=document.body,
        summary=document.summary,
        assessment=document.assessment,
        detected_at=document.detected_at,
        content_hash=document.content_hash,
        assessed_content_hash=document.assessed_content_hash,
        attachments=tuple(attachment_of(row) for row in rows.attachments),
    )


def build_list(rows: DocumentListRows, *, limit: int, offset: int) -> DocumentList:
    return DocumentList(
        items=tuple(
            summary_of(
                document,
                rows.instruments.get(document.id, ()),
                rows.indicators.get(document.id, ()),
            )
            for document in rows.documents
        ),
        limit=limit,
        offset=offset,
        has_more=rows.has_more,
    )


def source_of(source: DocumentSource, coverage: tuple[int, datetime] | None) -> DocumentSourceItem:
    return DocumentSourceItem(
        slug=source.slug,
        name=source.name,
        source_kind=str(getattr(source.source_kind, "value", source.source_kind)),
        country=source.country,
        language=source.language,
        collection_mode=str(getattr(source.collection_mode, "value", source.collection_mode)),
        enabled=source.enabled,
        terms_url=source.terms_url,
        terms_checked_at=source.terms_checked_at,
        documents=coverage[0] if coverage else 0,
        latest_at=coverage[1] if coverage else None,
    )


def build_sources(rows: SourceRows, *, limit: int, offset: int) -> DocumentSourceList:
    return DocumentSourceList(
        items=tuple(source_of(source, rows.coverage.get(source.slug)) for source in rows.sources),
        limit=limit,
        offset=offset,
        has_more=rows.has_more,
    )


def disclosure_of(row: DisclosureEvent) -> DisclosureItem:
    return DisclosureItem(
        rcept_no=row.rcept_no,
        stock_code=row.stock_code,
        corp_code=row.corp_code,
        company_name=row.company_name,
        report_name=row.report_name,
        filer_name=row.filer_name,
        corp_class=None if row.corp_class is None else str(getattr(row.corp_class, "value", row.corp_class)),
        receipt_date=row.receipt_date,
        detected_at=row.detected_at,
        remarks=row.remarks,
        # 본문은 있다는 사실만 낸다. 목록에 실으면 한 쪽이 수십만 자가 된다.
        has_body=row.body is not None,
        url=dart_url(row.provider, row.rcept_no),
    )


def earnings_of(row: EarningsFact) -> EarningsFactItem:
    return EarningsFactItem(
        stock_code=row.stock_code,
        rcept_no=row.rcept_no,
        release_type=str(getattr(row.release_type, "value", row.release_type)),
        period_end=row.period_end,
        statement_scope=str(getattr(row.statement_scope, "value", row.statement_scope)),
        amount_basis=str(getattr(row.amount_basis, "value", row.amount_basis)),
        metric=str(getattr(row.metric, "value", row.metric)),
        current_amount=number(row.current_amount),
        prior_year_amount=number(row.prior_year_amount),
        currency=row.currency,
        source_account_name=row.source_account_name,
        url=dart_url(row.provider, row.rcept_no),
    )


class DocumentReadService:
    """문서와 공시를 읽어 응답 계약으로 준다."""

    def __init__(self, repository: DocumentReadRepository) -> None:
        self._repository = repository

    async def list_page(
        self,
        *,
        published_from: datetime,
        published_to: datetime,
        sources: Sequence[str] = (),
        min_score: int | None = None,
        instrument: str | None = None,
        indicator: str | None = None,
        search: str | None = None,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> DocumentList:
        rows = await self._repository.list_rows(
            published_from=published_from,
            published_to=published_to,
            sources=sources,
            min_score=min_score,
            instrument=instrument,
            indicator=indicator,
            search=search,
            limit=limit,
            offset=offset,
        )
        return build_list(rows, limit=limit, offset=offset)

    async def detail(self, document_id: int) -> DocumentDetail:
        rows = await self._repository.detail_rows(document_id)
        if rows is None:
            raise UnknownDocument(str(document_id))
        return detail_of(rows)

    async def sources(
        self, *, limit: int = DEFAULT_LIMIT, offset: int = 0
    ) -> DocumentSourceList:
        rows = await self._repository.source_rows(limit=limit, offset=offset)
        return build_sources(rows, limit=limit, offset=offset)

    async def disclosures(
        self,
        *,
        receipt_from: date,
        receipt_to: date,
        stock_codes: Sequence[str] = (),
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> DisclosureList:
        rows, has_more = await self._repository.disclosure_rows(
            receipt_from=receipt_from,
            receipt_to=receipt_to,
            stock_codes=stock_codes,
            limit=limit,
            offset=offset,
        )
        return DisclosureList(
            items=tuple(disclosure_of(row) for row in rows),
            limit=limit,
            offset=offset,
            has_more=has_more,
        )

    async def earnings(
        self, stock_codes: Sequence[str] = (), *, limit: int = DEFAULT_LIMIT, offset: int = 0
    ) -> EarningsFactList:
        rows, has_more = await self._repository.earnings_rows(
            stock_codes, limit=limit, offset=offset
        )
        return EarningsFactList(
            items=tuple(earnings_of(row) for row in rows),
            limit=limit,
            offset=offset,
            has_more=has_more,
        )
