"""인과 그래프의 매핑.

**경로 한 줄이 사건·체인·대상·실현 등락을 다 들고 나간다.** 화면이 단계마다 다시 묻지
않게 하는 것이 이 층의 일이고, 리포지토리가 준 행 묶음을 자리로 푸는 것도 여기서 한다.
"""

from datetime import date
from typing import Any

from apps.api.repository import DEFAULT_LIMIT, MarketCausalReadRepository, PathRows
from apps.api.schemas import (
    CausalChannelList,
    CausalChannelRow,
    CausalEventList,
    CausalEventRow,
    CausalPathDetail,
    CausalPathList,
    CausalPathRow,
)
from apps.api.service.common import number


def _text(value: Any) -> str:
    """Enum이면 값을, 아니면 문자열을."""
    return str(getattr(value, "value", value))


def path_of(path: Any, event: Any, channels: tuple[str, ...]) -> CausalPathRow:
    return CausalPathRow(
        id=path.id,
        week_start=path.week_start,
        event_id=path.event_id,
        event_title=event.title,
        event_occurred_on=event.occurred_on,
        target_kind=_text(path.target_kind),
        target_code=path.target_code,
        channels=channels,
        sign=_text(path.sign),
        confidence=_text(path.confidence),
        reasoning=path.reasoning,
        return_week_change=number(path.return_week_change) or 0.0,
        return_t1_change=number(path.return_t1_change) or 0.0,
        return_t5_change=number(path.return_t5_change) or 0.0,
        return_unit=_text(path.return_unit),
        llm_run_id=path.llm_run_id,
    )


def build_paths(rows: PathRows, *, limit: int, offset: int) -> CausalPathList:
    """**사건을 못 찾은 경로는 내지 않는다.** 외래키가 있어 생길 수 없는 일이고,
    생겼다면 그 행은 사건 없는 경로라 화면에 반쪽으로 보이는 것보다 빠지는 편이 낫다."""
    return CausalPathList(
        items=tuple(
            path_of(path, rows.events[path.id], rows.chains.get(path.id, ()))
            for path in rows.paths
            if path.id in rows.events
        ),
        limit=limit,
        offset=offset,
        has_more=rows.has_more,
    )


class UnknownPath(Exception):
    """없는 경로 id. 라우트가 404로 바꾼다."""


def build_detail(rows: PathRows, path_id: int) -> CausalPathDetail:
    """**형제를 함께 낸다.** 경로 하나만 내면 화면이 그릴 것이 직선 하나뿐이다."""
    family = tuple(
        path_of(path, rows.events[path.id], rows.chains.get(path.id, ()))
        for path in rows.paths
        if path.id in rows.events
    )
    found = next((row for row in family if row.id == path_id), None)
    if found is None:
        raise UnknownPath(str(path_id))
    return CausalPathDetail(path=found, siblings=family)


def event_of(row: tuple[Any, ...]) -> CausalEventRow:
    event, paths = row
    return CausalEventRow(
        id=event.id,
        title=event.title,
        occurred_on=event.occurred_on,
        first_seen_week=event.first_seen_week,
        paths=paths,
    )


def channel_of(row: tuple[Any, ...]) -> CausalChannelRow:
    channel, steps = row
    return CausalChannelRow(
        id=channel.id,
        name=channel.name,
        first_seen_week=channel.first_seen_week,
        steps=steps,
    )


class MarketCausalReadService:
    """인과 그래프를 읽어 응답 계약으로 준다."""

    def __init__(self, repository: MarketCausalReadRepository) -> None:
        self._repository = repository

    async def paths(
        self,
        *,
        start: date,
        end: date,
        target_kinds: tuple[str, ...] = (),
        target_codes: tuple[str, ...] = (),
        event_id: int | None = None,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> CausalPathList:
        rows = await self._repository.path_rows(
            start=start,
            end=end,
            target_kinds=target_kinds,
            target_codes=target_codes,
            event_id=event_id,
            limit=limit,
            offset=offset,
        )
        return build_paths(rows, limit=limit, offset=offset)

    async def detail(self, path_id: int) -> CausalPathDetail:
        rows = await self._repository.detail_rows(path_id)
        if rows is None:
            raise UnknownPath(str(path_id))
        return build_detail(rows, path_id)

    async def events(
        self, *, start: date, end: date, limit: int = DEFAULT_LIMIT, offset: int = 0
    ) -> CausalEventList:
        rows, has_more = await self._repository.event_rows(
            start=start, end=end, limit=limit, offset=offset
        )
        return CausalEventList(
            items=tuple(event_of(row) for row in rows),
            limit=limit,
            offset=offset,
            has_more=has_more,
        )

    async def channels(
        self, *, start: date, end: date, limit: int = DEFAULT_LIMIT, offset: int = 0
    ) -> CausalChannelList:
        rows, has_more = await self._repository.channel_rows(
            start=start, end=end, limit=limit, offset=offset
        )
        return CausalChannelList(
            items=tuple(channel_of(row) for row in rows),
            limit=limit,
            offset=offset,
            has_more=has_more,
        )


__all__ = [
    "MarketCausalReadService",
    "UnknownPath",
    "build_detail",
    "build_paths",
    "channel_of",
    "event_of",
    "path_of",
]
