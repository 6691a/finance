"""수집 원장과 마스터 조회.

**`payload`를 목록 조회에 싣지 않는다.** jsonb 원본이라 행 하나가 수백 KB일 수 있어서,
`SELECT`에 칸을 따로 적어 컬럼을 고른다 — 모델을 통째로 읽으면 그것이 딸려 온다.
"""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy import Select, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.repository.common import DEFAULT_LIMIT, page_slice
from apps.models.market import MarketSession
from apps.models.raw import SourceRecord, SourceStatus
from apps.models.reference import Instrument


class CollectionReadRepository:
    """수집 원장과 마스터를 읽는다. 쓰기 경로는 없다."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    # --- 조회문 (테스트가 컴파일해서 본다) ---------------------------------------

    @staticmethod
    def health_statement(
        since: datetime, *, limit: int = DEFAULT_LIMIT, offset: int = 0
    ) -> Select[Any]:
        """출처별 최신성과 실패. **`running`을 따로 센다.**

        종료를 기록하지 못한 수집이 몇 건인지가 곧 "끊긴 것이 있나"의 첫 신호다. 성공·실패에
        묻어 두면 그 신호가 사라진다.

        **정렬을 SQL이 한다.** 늦은 출처가 위여야 하는데 쪽을 나눈 뒤 Python에서 정렬하면
        그 쪽 안에서만 늦은 것이 위로 온다 — 두 번째 쪽의 첫 행이 첫 쪽의 마지막보다
        이른 일이 생긴다.
        """

        def tally(status: SourceStatus) -> Any:
            return func.count(case((SourceRecord.status == status, 1))).label(status.value)

        return (
            select(
                SourceRecord.source,
                func.min(SourceRecord.source_type).label("source_type"),
                func.count().label("records"),
                tally(SourceStatus.SUCCEEDED),
                tally(SourceStatus.FAILED),
                tally(SourceStatus.RUNNING),
                tally(SourceStatus.QUARANTINED),
                func.coalesce(func.sum(SourceRecord.record_count), 0).label("rows"),
                func.max(SourceRecord.started_at).label("latest_at"),
            )
            .where(SourceRecord.started_at >= since)
            .group_by(SourceRecord.source)
            .order_by(func.max(SourceRecord.started_at).asc())
            .limit(limit + 1)
            .offset(offset)
        )

    @staticmethod
    def record_statement(
        *,
        start: datetime,
        end: datetime,
        sources: Sequence[str] = (),
        statuses: Sequence[str] = (),
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> Select[Any]:
        """수집 레코드. **`payload`를 안 읽는다** — 있는지 여부만 낸다."""
        statement = select(
            SourceRecord.id,
            SourceRecord.source_type,
            SourceRecord.source,
            SourceRecord.source_key,
            SourceRecord.started_at,
            SourceRecord.completed_at,
            SourceRecord.status,
            SourceRecord.record_count,
            SourceRecord.payload.is_not(None).label("has_payload"),
            SourceRecord.source_metadata.is_not(None).label("has_metadata"),
            SourceRecord.payload_uri,
        ).where(SourceRecord.started_at >= start, SourceRecord.started_at <= end)
        if sources:
            statement = statement.where(SourceRecord.source.in_(sources))
        if statuses:
            statement = statement.where(SourceRecord.status.in_(statuses))
        return (
            statement.order_by(SourceRecord.started_at.desc(), SourceRecord.id.desc())
            .limit(limit + 1)
            .offset(offset)
        )

    # --- 공개 조회 -----------------------------------------------------------

    async def health_rows(
        self, since: datetime, *, limit: int = DEFAULT_LIMIT, offset: int = 0
    ) -> tuple[tuple[tuple[Any, ...], ...], bool]:
        async with self._session_factory() as session:
            rows = [
                tuple(row)
                for row in await session.execute(
                    self.health_statement(since, limit=limit, offset=offset)
                )
            ]
        return page_slice(rows, limit)

    async def record_rows(self, **filters: Any) -> tuple[tuple[tuple[Any, ...], ...], bool]:
        async with self._session_factory() as session:
            rows = [tuple(row) for row in await session.execute(self.record_statement(**filters))]
        return page_slice(rows, filters.get("limit", DEFAULT_LIMIT))

    async def instrument_rows(self, *, limit: int, offset: int) -> tuple[tuple[Instrument, ...], bool]:
        async with self._session_factory() as session:
            found = list(
                (
                    await session.execute(
                        select(Instrument)
                        .order_by(Instrument.market, Instrument.ticker)
                        .limit(limit + 1)
                        .offset(offset)
                    )
                ).scalars()
            )
        return page_slice(found, limit)

    async def session_rows(
        self, *, start: date, end: date, markets: Sequence[str] = (), limit: int, offset: int
    ) -> tuple[tuple[Any, ...], bool]:
        statement = select(MarketSession).where(
            MarketSession.session_date >= start, MarketSession.session_date <= end
        )
        if markets:
            statement = statement.where(MarketSession.market_code.in_(markets))
        async with self._session_factory() as session:
            found = list(
                (
                    await session.execute(
                        statement.order_by(MarketSession.session_date, MarketSession.market_code)
                        .limit(limit + 1)
                        .offset(offset)
                    )
                ).scalars()
            )
        return page_slice(found, limit)
