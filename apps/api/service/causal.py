"""인과 그래프의 매핑.

**경로 한 줄이 사건·체인·대상·실현 등락을 다 들고 나간다.** 화면이 단계마다 다시 묻지
않게 하는 것이 이 층의 일이고, 리포지토리가 준 행 묶음을 자리로 푸는 것도 여기서 한다.
"""

from datetime import date
from typing import Any

from apps.api.repository import (
    DEFAULT_LIMIT,
    CausalGraphReadRepository,
    GraphRows,
    MarketCausalReadRepository,
    PathRows,
)
from apps.api.schemas import (
    CausalChannelList,
    CausalChannelRow,
    CausalEventList,
    CausalEventRow,
    CausalEvidenceRow,
    CausalGraph,
    CausalGraphEdge,
    CausalGraphNode,
    CausalPathDetail,
    CausalPathList,
    CausalPathRow,
)
from apps.api.service.common import dart_url, number


def _text(value: Any) -> str:
    """Enum이면 값을, 아니면 문자열을."""
    return str(getattr(value, "value", value))


def path_of(path: Any, event: Any | None, channels: tuple[str, ...]) -> CausalPathRow:
    """**사건이 없는 경로가 정상이다.** 대상에서 출발한 경로는 `event_id`가 NULL이고
    `source_target_*` 셋이 채워진다 — 그것을 버리면 다중 홉의 절반이 화면에서 사라진다."""
    return CausalPathRow(
        id=path.id,
        week_start=path.week_start,
        source_kind="event" if path.event_id is not None else "target",
        event_id=path.event_id,
        event_title=None if event is None else event.title,
        event_occurred_on=None if event is None else event.occurred_on,
        source_target_kind=None
        if path.source_target_kind is None
        else _text(path.source_target_kind),
        source_target_code=path.source_target_code,
        source_sign=None if path.source_sign is None else _text(path.source_sign),
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
    """**모든 경로를 낸다.** 전에는 사건을 못 찾은 행을 버렸는데, 출발점이 대상인 경로가
    생기면서 그 규칙이 그것들을 통째로 삼켰다(2026-08-30). 사건 출발인데 사건이 없는 것은
    외래키가 막고 있다."""
    return CausalPathList(
        items=tuple(
            path_of(path, rows.events.get(path.id), rows.chains.get(path.id, ()))
            for path in rows.paths
        ),
        limit=limit,
        offset=offset,
        has_more=rows.has_more,
    )


class UnknownPath(Exception):
    """없는 경로 id. 라우트가 404로 바꾼다."""


class GraphOffline(Exception):
    """그래프 DB가 이 실행에 붙어 있지 않다. 라우트가 503으로 바꾼다.

    **빈 그래프로 위장하지 않는다** — "그 주에 경로가 없다"와 "그래프 DB가 꺼져 있다"는
    화면이 다르게 말해야 한다. 화면은 이 응답을 보고 경로 목록으로 그림을 조립한다.
    """


def graph_of(rows: GraphRows, week: date | None) -> CausalGraph:
    """투영을 응답 계약으로. **모양만 옮긴다** — 숫자는 경로 응답이 갖는다."""
    return CausalGraph(
        source="neo4j",
        week_start=week,
        nodes=tuple(
            CausalGraphNode(id=node.id, kind=node.kind, label=node.label) for node in rows.nodes
        ),
        edges=tuple(
            CausalGraphEdge(
                source=edge.source,
                target=edge.target,
                type=edge.type,
                path_id=edge.path_id,
                week_start=edge.week_start,
                position=edge.position,
            )
            for edge in rows.edges
        ),
    )


def evidence_of(path_id: int, ref: str, titles: dict[int, str]) -> CausalEvidenceRow:
    """`<kind>:<id>` 규약을 응답에서 한 번만 푼다.

    **화면이 문자열을 쪼개게 두지 않는다** — 그러면 규약이 두 곳에 생기고, 종류가 늘 때
    한쪽만 고친다. 모르는 종류는 종류 이름만 남기고 링크를 만들지 않는다.
    """
    kind, _, value = ref.partition(":")
    title = titles.get(int(value)) if kind == "document" and value.isdigit() else None
    url = None
    if kind == "document" and value.isdigit():
        # 문서는 우리 화면에 상세가 있다. 원문 링크는 그 상세가 준다.
        url = f"/documents/{value}"
    elif kind == "disclosure" and value:
        # 공시는 접수번호가 곧 주소다. 이 근거는 DART에서 온 것만 있다.
        url = dart_url("dart", value)
    return CausalEvidenceRow(path_id=path_id, ref=ref, kind=kind, title=title, url=url)


def build_detail(rows: PathRows, path_id: int) -> CausalPathDetail:
    """**그 주 전체를 함께 낸다.** 경로 하나만 내면 화면이 그릴 것이 직선 하나뿐이고,
    사건 단위로 묶으면 대상이 다시 원인이 되는 사슬이 끊어진 채로 보인다."""
    family = tuple(
        path_of(path, rows.events.get(path.id), rows.chains.get(path.id, ()))
        for path in rows.paths
    )
    found = next((row for row in family if row.id == path_id), None)
    if found is None:
        raise UnknownPath(str(path_id))
    return CausalPathDetail(
        path=found,
        siblings=family,
        evidence=tuple(
            evidence_of(path, ref, rows.document_titles) for path, ref in rows.evidence
        ),
    )


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

    def __init__(
        self,
        repository: MarketCausalReadRepository,
        graph_repository: CausalGraphReadRepository,
    ) -> None:
        self._repository = repository
        # **저장소가 둘이다.** 숫자·근거는 Postgres가 원본이고 모양은 Neo4j 투영이 낫다 —
        # 투영은 주를 넘어 노드를 공유해서 경로 목록이 못 잇는 사슬을 잇는다.
        self._graph = graph_repository

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

    async def week_graph(self, week: date) -> CausalGraph:
        """그 주의 투영. 그래프 DB가 없으면 `GraphOffline`이다."""
        if not self._graph.enabled:
            raise GraphOffline("neo4j is not configured for this process")
        return graph_of(self._graph.week_graph(week), week)

    async def target_chain(self, *, kind: str, code: str, limit: int = 50) -> CausalGraph:
        """대상 하나에서 **주를 넘어** 뻗은 사슬. 경로 목록으로는 못 만드는 답이다."""
        if not self._graph.enabled:
            raise GraphOffline("neo4j is not configured for this process")
        return graph_of(self._graph.target_chain(kind=kind, code=code, limit=limit), None)

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
    "GraphOffline",
    "MarketCausalReadService",
    "UnknownPath",
    "build_detail",
    "build_paths",
    "channel_of",
    "event_of",
    "evidence_of",
    "graph_of",
    "path_of",
]
