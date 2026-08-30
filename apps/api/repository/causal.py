"""인과 그래프 조회.

**단계는 경로마다 다시 묻지 않는다.** 한 쪽의 경로 id를 모아 `WHERE path_id = ANY(:ids)`
한 번으로 읽고 서비스가 체인으로 편다 — 문서 태그를 배치로 읽는 것과 같은 판단이다.

**사건·채널 목록의 수(count)는 왼쪽 조인 집계다.** 아직 경로가 없는 사건도 목록에 남아야
한다 — "사건은 있는데 경로가 안 나왔다"가 사실이고, 그 행이 사라지면 그것을 못 본다.
"""

from collections.abc import Sequence
from datetime import date
from typing import Any

from pydantic import Field
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.repository.common import DEFAULT_LIMIT, RowBundle, page_slice
from apps.models.analysis import (
    MarketCausalEvidence,
    MarketCausalPath,
    MarketCausalStep,
    MarketChannel,
    MarketEvent,
)
from apps.models.content import Document


class PathRows(RowBundle):
    paths: tuple[MarketCausalPath, ...] = ()
    has_more: bool = False
    # 경로 id → 사건
    events: dict[int, MarketEvent] = Field(default_factory=dict)
    # 경로 id → 채널 이름(사건 쪽에서 대상 쪽 순서)
    chains: dict[int, tuple[str, ...]] = Field(default_factory=dict)
    # (경로 id, ref) 쌍. 목록에는 안 싣고 상세만 채운다 — 판정을 되짚는 자리다.
    evidence: tuple[tuple[int, str], ...] = ()
    # 문서 id → 제목. `document:189`만 보이면 사람이 못 읽는다.
    document_titles: dict[int, str] = Field(default_factory=dict)


class MarketCausalReadRepository:
    """인과 그래프를 읽는다. 쓰기 경로는 없다."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    # --- 조회문 (테스트가 컴파일해서 본다) ---------------------------------------

    @staticmethod
    def path_statement(
        *,
        start: date,
        end: date,
        target_kinds: Sequence[str] = (),
        target_codes: Sequence[str] = (),
        event_id: int | None = None,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> Select[Any]:
        """경로 목록. 축은 **주(week_start)**다 — 이 그래프는 주 단위로 쌓인다."""
        statement = select(MarketCausalPath).where(
            MarketCausalPath.week_start >= start, MarketCausalPath.week_start <= end
        )
        if target_kinds:
            statement = statement.where(MarketCausalPath.target_kind.in_(target_kinds))
        if target_codes:
            statement = statement.where(MarketCausalPath.target_code.in_(target_codes))
        if event_id is not None:
            statement = statement.where(MarketCausalPath.event_id == event_id)
        return (
            statement.order_by(MarketCausalPath.week_start.desc(), MarketCausalPath.id)
            .limit(limit + 1)
            .offset(offset)
        )

    @staticmethod
    def chain_statement(path_ids: Sequence[int]) -> Select[Any]:
        """경로들의 단계와 채널 이름. **`position` 오름차순이 곧 체인의 순서다.**"""
        return (
            select(MarketCausalStep.path_id, MarketChannel.name)
            .join(MarketChannel, MarketChannel.id == MarketCausalStep.channel_id)
            .where(MarketCausalStep.path_id.in_(path_ids))
            .order_by(MarketCausalStep.path_id, MarketCausalStep.position)
        )

    @staticmethod
    def evidence_statement(path_ids: Sequence[int]) -> Select[Any]:
        """경로들이 인용한 근거. **`ref`는 `<kind>:<id>` 문자열이라 조인할 대상이 없다.**"""
        return (
            select(MarketCausalEvidence.path_id, MarketCausalEvidence.ref)
            .where(MarketCausalEvidence.path_id.in_(path_ids))
            .order_by(MarketCausalEvidence.path_id, MarketCausalEvidence.ref)
        )

    @staticmethod
    def event_statement(
        *, start: date, end: date, limit: int = DEFAULT_LIMIT, offset: int = 0
    ) -> Select[Any]:
        """사건 목록과 경로 수. 축은 **처음 등장한 주**다."""
        counted = (
            select(MarketCausalPath.event_id, func.count().label("paths"))
            .group_by(MarketCausalPath.event_id)
            .subquery()
        )
        return (
            select(MarketEvent, func.coalesce(counted.c.paths, 0).label("paths"))
            .outerjoin(counted, counted.c.event_id == MarketEvent.id)
            .where(MarketEvent.first_seen_week >= start, MarketEvent.first_seen_week <= end)
            .order_by(MarketEvent.occurred_on.desc(), MarketEvent.id)
            .limit(limit + 1)
            .offset(offset)
        )

    @staticmethod
    def channel_statement(
        *, start: date, end: date, limit: int = DEFAULT_LIMIT, offset: int = 0
    ) -> Select[Any]:
        """채널 목록과 그 채널을 거친 단계 수."""
        counted = (
            select(MarketCausalStep.channel_id, func.count().label("steps"))
            .group_by(MarketCausalStep.channel_id)
            .subquery()
        )
        return (
            select(MarketChannel, func.coalesce(counted.c.steps, 0).label("steps"))
            .outerjoin(counted, counted.c.channel_id == MarketChannel.id)
            .where(MarketChannel.first_seen_week >= start, MarketChannel.first_seen_week <= end)
            .order_by(MarketChannel.first_seen_week.desc(), MarketChannel.name)
            .limit(limit + 1)
            .offset(offset)
        )

    # --- 공개 조회 -----------------------------------------------------------

    async def path_rows(self, **filters: Any) -> PathRows:
        """경로 한 쪽과 그 사건·체인. 왕복 셋이고 한 세션 안이다."""
        limit = filters.get("limit", DEFAULT_LIMIT)
        async with self._session_factory() as session:
            found = list((await session.execute(self.path_statement(**filters))).scalars())
            paths, has_more = page_slice(found, limit)
            if not paths:
                return PathRows(has_more=has_more)

            events = {
                event.id: event
                for event in (
                    await session.execute(
                        select(MarketEvent).where(
                            MarketEvent.id.in_({path.event_id for path in paths})
                        )
                    )
                ).scalars()
            }
            chains: dict[int, list[str]] = {}
            for path_id, name in await session.execute(
                self.chain_statement([path.id for path in paths])
            ):
                chains.setdefault(path_id, []).append(name)

        return PathRows(
            paths=paths,
            has_more=has_more,
            # 경로 id로 다시 걸어 준다 — 부르는 쪽이 event_id를 한 번 더 따라가지 않게 한다.
            events={path.id: events[path.event_id] for path in paths if path.event_id in events},
            chains={key: tuple(value) for key, value in chains.items()},
        )

    async def detail_rows(self, path_id: int) -> PathRows | None:
        """경로 하나와 **같은 사건·같은 주의 형제 경로 전부**. 왕복 셋이고 한 세션 안이다."""
        async with self._session_factory() as session:
            found = (
                await session.execute(
                    select(MarketCausalPath).where(MarketCausalPath.id == path_id)
                )
            ).scalar_one_or_none()
            if found is None:
                return None

            family = list(
                (
                    await session.execute(
                        select(MarketCausalPath)
                        .where(
                            MarketCausalPath.event_id == found.event_id,
                            MarketCausalPath.week_start == found.week_start,
                        )
                        .order_by(MarketCausalPath.id)
                    )
                ).scalars()
            )
            event = (
                await session.execute(
                    select(MarketEvent).where(MarketEvent.id == found.event_id)
                )
            ).scalar_one_or_none()
            chains: dict[int, list[str]] = {}
            for path_key, name in await session.execute(
                self.chain_statement([path.id for path in family])
            ):
                chains.setdefault(path_key, []).append(name)

            evidence = tuple(
                (row.path_id, row.ref)
                for row in await session.execute(
                    self.evidence_statement([path.id for path in family])
                )
            )
            # 문서 제목만 한 번 더 읽는다. 공시·신호는 식별자가 곧 읽을 수 있는 값이다.
            document_ids = {
                int(ref.split(":", 1)[1])
                for _, ref in evidence
                if ref.startswith("document:") and ref.split(":", 1)[1].isdigit()
            }
            titles = (
                {
                    row.id: row.title
                    for row in await session.execute(
                        select(Document.id, Document.title).where(Document.id.in_(document_ids))
                    )
                }
                if document_ids
                else {}
            )

        if event is None:
            return None
        return PathRows(
            paths=tuple(family),
            events={path.id: event for path in family},
            chains={key: tuple(value) for key, value in chains.items()},
            evidence=evidence,
            document_titles=titles,
        )

    async def event_rows(self, **filters: Any) -> tuple[tuple[Any, ...], bool]:
        async with self._session_factory() as session:
            rows = [tuple(row) for row in await session.execute(self.event_statement(**filters))]
        return page_slice(rows, filters.get("limit", DEFAULT_LIMIT))

    async def channel_rows(self, **filters: Any) -> tuple[tuple[Any, ...], bool]:
        async with self._session_factory() as session:
            rows = [tuple(row) for row in await session.execute(self.channel_statement(**filters))]
        return page_slice(rows, filters.get("limit", DEFAULT_LIMIT))
