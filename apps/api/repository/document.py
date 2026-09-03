"""문서·공시 조회.

**본문은 상세에만 있다.** 3천 건의 `body`를 목록에 실으면 응답이 수십 MB가 된다 —
실행 원장의 툴 결과를 단건으로 뺀 것과 같은 판단이다.

**그리고 목록 조회는 그 칸을 읽지도 않는다.** 응답에서 빼는 것만으로는 부족하다 —
`select(Document)`는 `body`·`assessment`까지 TOAST에서 끌어와 DB가 API로 보내고, 우리는
그것을 버린다. 2026-08-31 실측으로 101행에 **6.8MB·0.65초**였고 칸을 고르면 **59KB·0.011초**다
(60배). 계획은 둘 다 0.6ms라 **쿼리가 아니라 옮기는 양이 문제였다.** 문서 본문 수집이
붙기 전에는 그 칸이 비어 있어 이 값이 안 보였다.

**태그는 배치로 읽는다.** `document_instrument`·`document_indicator`가 문서마다 여러 행이라
목록 한 쪽마다 `WHERE document_id = ANY(:ids)` 두 번이면 끝난다.
"""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from pydantic import Field
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import load_only

from apps.api.repository.common import DEFAULT_LIMIT, RowBundle, page_slice
from apps.models.content import (
    Document,
    DocumentAttachment,
    DocumentIndicator,
    DocumentInstrument,
    DocumentSource,
)
from apps.models.market import DisclosureEvent, EarningsFact

# 목록 한 줄이 실제로 쓰는 칸. **`body`와 `assessment`가 여기 없다** — 둘이 이 표의
# TOAST 22MB를 만든다(2026-08-31 실측).
LIST_COLUMNS = (
    Document.source_slug,
    Document.external_id,
    Document.title,
    Document.document_type,
    Document.published_at,
    Document.language,
    Document.body_status,
    Document.canonical_url,
    Document.value_score,
    Document.direction,
    Document.assessed_at,
    Document.llm_model,
    Document.prompt_version,
)


class DocumentListRows(RowBundle):
    documents: tuple[Document, ...] = ()
    has_more: bool = False

    # 문서 id → 태그
    instruments: dict[int, tuple[str, ...]] = Field(default_factory=dict)
    indicators: dict[int, tuple[str, ...]] = Field(default_factory=dict)


class DocumentDetailRows(RowBundle):
    document: Document
    instruments: tuple[str, ...] = ()
    indicators: tuple[str, ...] = ()
    # **상세에만 싣는다.** 목록 한 쪽에 첨부까지 붙이면 문서 하나에 여러 행이 딸려 온다.
    attachments: tuple[DocumentAttachment, ...] = ()


class SourceRows(RowBundle):
    sources: tuple[DocumentSource, ...] = ()
    has_more: bool = False
    # slug → (문서 수, 가장 최근 발행 시각)
    coverage: dict[str, tuple[int, datetime]] = Field(default_factory=dict)


class DocumentReadRepository:
    """문서와 공시를 읽는다. 쓰기 경로는 없다."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    # --- 조회문 (테스트가 컴파일해서 본다) ---------------------------------------

    @staticmethod
    def list_statement(
        *,
        published_from: datetime,
        published_to: datetime,
        sources: Sequence[str] = (),
        min_score: int | None = None,
        instrument: str | None = None,
        indicator: str | None = None,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> Select[Any]:
        """목록 조회문. **끝 경계가 열려 있다**(`< published_to`).

        **본문 검색은 여기 없다**(2026-09-02에 뺐다). BM25로 붙였다가 하루 만에 되돌렸고,
        `ILIKE` 대체도 함께 뺐다 — 다음 판은 쪽 단위 색인으로 새로 짓기 때문에 그 사이
        반쪽짜리 검색창을 두지 않는다. 왜 그렇게 정했는지는
        `docs/analysis/market-thesis/20-collection-browser.md` §8.8에 있다.
        """
        statement = (
            select(Document)
            # **목록이 쓰는 칸만 읽는다.** `load_only`라 ORM 인스턴스는 그대로여서 매핑이
            # 안 바뀌고, 빠뜨린 칸을 나중에 건드리면 세션이 닫힌 뒤라 조용히 지나가지 않는다.
            .options(load_only(*LIST_COLUMNS))
            .where(
                Document.published_at >= published_from,
                Document.published_at < published_to,
            )
        )
        if sources:
            statement = statement.where(Document.source_slug.in_(sources))
        if min_score is not None:
            # **미평가 문서를 함께 빼는 것이 맞다.** `value_score`가 NULL인 것은 "낮다"가
            # 아니라 "아직 안 봤다"이고, 점수로 거르는 화면은 평가된 것만 묻는다.
            statement = statement.where(Document.value_score >= min_score)
        if instrument:
            statement = statement.where(
                Document.id.in_(
                    select(DocumentInstrument.document_id).where(DocumentInstrument.ticker == instrument)
                )
            )
        if indicator:
            statement = statement.where(
                Document.id.in_(
                    select(DocumentIndicator.document_id).where(DocumentIndicator.series_id == indicator)
                )
            )
        return (
            statement.order_by(Document.published_at.desc(), Document.id.desc())
            .limit(limit + 1)
            .offset(offset)
        )

    @staticmethod
    def source_coverage_statement() -> Select[Any]:
        return select(
            Document.source_slug,
            func.count().label("documents"),
            func.max(Document.published_at).label("latest_at"),
        ).group_by(Document.source_slug)

    @staticmethod
    def disclosure_statement(
        *,
        receipt_from: date,
        receipt_to: date,
        stock_codes: Sequence[str] = (),
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> Select[Any]:
        statement = select(DisclosureEvent).where(
            DisclosureEvent.receipt_date >= receipt_from,
            DisclosureEvent.receipt_date <= receipt_to,
        )
        if stock_codes:
            statement = statement.where(DisclosureEvent.stock_code.in_(stock_codes))
        return (
            statement.order_by(DisclosureEvent.receipt_date.desc(), DisclosureEvent.rcept_no.desc())
            .limit(limit + 1)
            .offset(offset)
        )

    @staticmethod
    def earnings_statement(
        stock_codes: Sequence[str] = (), *, limit: int = DEFAULT_LIMIT, offset: int = 0
    ) -> Select[Any]:
        """실적 지표. 지금은 여섯 행이지만 **행을 주는 라우트는 전부 쪽으로 낸다** —
        예외를 두면 어느 것이 쪽이고 어느 것이 전부인지 부르는 쪽이 외워야 한다."""
        statement = select(EarningsFact)
        if stock_codes:
            statement = statement.where(EarningsFact.stock_code.in_(stock_codes))
        return (
            statement.order_by(EarningsFact.period_end.desc(), EarningsFact.metric)
            .limit(limit + 1)
            .offset(offset)
        )

    # --- 공개 조회 -----------------------------------------------------------

    async def list_rows(self, **filters: Any) -> DocumentListRows:
        """문서 목록 한 쪽. 왕복 셋이고 한 세션 안이다.

        """
        limit = filters.get("limit", DEFAULT_LIMIT)
        async with self._session_factory() as session:
            rows = list((await session.execute(self.list_statement(**filters))).scalars())
            found, has_more = page_slice(rows, limit)
            if not found:
                return DocumentListRows(has_more=has_more)
            ids = [row.id for row in found]
            instruments = await self._instruments(session, ids)
            indicators = await self._indicators(session, ids)
        return DocumentListRows(
            documents=found,
            has_more=has_more,
            instruments=instruments,
            indicators=indicators,
        )

    async def detail_rows(self, document_id: int) -> DocumentDetailRows | None:
        async with self._session_factory() as session:
            document = (
                await session.execute(select(Document).where(Document.id == document_id))
            ).scalar_one_or_none()
            if document is None:
                return None
            instruments = await self._instruments(session, [document_id])
            indicators = await self._indicators(session, [document_id])
            attachments = list(
                (
                    await session.execute(
                        select(DocumentAttachment)
                        .where(DocumentAttachment.document_id == document_id)
                        .order_by(DocumentAttachment.position)
                    )
                ).scalars()
            )
        return DocumentDetailRows(
            document=document,
            instruments=instruments.get(document_id, ()),
            indicators=indicators.get(document_id, ()),
            attachments=tuple(attachments),
        )

    async def source_rows(self, *, limit: int = DEFAULT_LIMIT, offset: int = 0) -> SourceRows:
        async with self._session_factory() as session:
            found = list(
                (
                    await session.execute(
                        select(DocumentSource)
                        .order_by(DocumentSource.slug)
                        .limit(limit + 1)
                        .offset(offset)
                    )
                ).scalars()
            )
            sources, has_more = page_slice(found, limit)
            coverage = {
                row.source_slug: (row.documents, row.latest_at)
                for row in await session.execute(self.source_coverage_statement())
            }
        return SourceRows(sources=sources, coverage=coverage, has_more=has_more)

    async def disclosure_rows(self, **filters: Any) -> tuple[tuple[Any, ...], bool]:
        async with self._session_factory() as session:
            found = list((await session.execute(self.disclosure_statement(**filters))).scalars())
        return page_slice(found, filters.get("limit", DEFAULT_LIMIT))

    async def earnings_rows(
        self, stock_codes: Sequence[str] = (), *, limit: int = DEFAULT_LIMIT, offset: int = 0
    ) -> tuple[tuple[EarningsFact, ...], bool]:
        async with self._session_factory() as session:
            found = list(
                (
                    await session.execute(
                        self.earnings_statement(stock_codes, limit=limit, offset=offset)
                    )
                ).scalars()
            )
        return page_slice(found, limit)

    # --- 조각 조회 -----------------------------------------------------------

    @staticmethod
    async def _instruments(session: AsyncSession, ids: Sequence[int]) -> dict[int, tuple[str, ...]]:
        rows = await session.execute(
            select(DocumentInstrument.document_id, DocumentInstrument.ticker)
            .where(DocumentInstrument.document_id.in_(ids))
            .order_by(DocumentInstrument.ticker)
        )
        found: dict[int, list[str]] = {}
        for document_id, ticker in rows:
            found.setdefault(document_id, []).append(ticker)
        return {key: tuple(value) for key, value in found.items()}

    @staticmethod
    async def _indicators(session: AsyncSession, ids: Sequence[int]) -> dict[int, tuple[str, ...]]:
        rows = await session.execute(
            select(DocumentIndicator.document_id, DocumentIndicator.series_id)
            .where(DocumentIndicator.document_id.in_(ids))
            .order_by(DocumentIndicator.series_id)
        )
        found: dict[int, list[str]] = {}
        for document_id, series_id in rows:
            found.setdefault(document_id, []).append(series_id)
        return {key: tuple(value) for key, value in found.items()}
