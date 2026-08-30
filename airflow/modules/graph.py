"""주간 인과 그래프를 Neo4j에 투영한다.

설계는 [4-graph.md](../../docs/analysis/market-thesis/4-graph.md)다. **Postgres가 원본이고
여기는 파생물이다** — Neo4j가 통째로 날아가도 잃는 것이 없다. 주 하나를 다시 밀어 넣으면
같은 그래프가 선다. 반대로 Postgres가 날아가면 Neo4j로 못 되살린다. `input_hash`·
`llm_run_id`·근거를 싣지 않기 때문이다.

`slack.py`와 같은 자리다 — Airflow를 import하지 않고, 재시도 핸들러를 붙이지 않는다.
드라이버 자체 재시도는 `max_transaction_retry_time=0`으로 끈다. 켜 두면 `execute_write`가
transient 오류를 기본 30초 동안 스스로 다시 부르고, 태스크 로그의 시도 횟수와 실제 호출
횟수가 어긋난다. **재시도는 Airflow가 한다.**

## 노드 키는 Postgres의 자연키다

`market_event.id`가 아니라 `(title, occurred_on)`을 쓴다. 재적재가 정상 흐름이고, 자연키면
몇 번을 다시 넣어도 같은 노드다. `Channel`은 `(name)`, `Target`은 `(kind, code)`다.

**`Target` 노드는 주를 넘어 하나다.** 그것이 주와 주를 잇는 장치이고(설계 §1), 동시에
무제약 탐색이 **시각을 역행하는 경로**를 만드는 원인이다 — 08-17 주에 닿은 `SOX`가 08-10
주의 원인으로 이어진다. 그래서 모든 엣지가 `week_start`를 싣는다. 조회하는 쪽이 그 값으로
단조 증가를 걸어야 한다(§7.8 발견 ②).

## 엣지는 경로 하나를 편 것이다

경로 헤더 한 행 + 단계 N행이 엣지 N+1개가 된다.

    (:Event|:Target)-[:LEADS_TO]->(:Channel)-[:LEADS_TO]->…-[:HITS]->(:Target)

`path_id`가 모든 엣지에 실린다. **채널 노드가 모든 경로에 공유되므로** 이 값이 없으면
서로 다른 주장이 `할인율`에서 섞인다(§7.8 발견 ①).
"""

import logging
from collections.abc import Sequence
from datetime import date
from itertools import pairwise
from typing import Any

from neo4j import Driver, GraphDatabase
from neo4j.exceptions import ClientError, Neo4jError, ServiceUnavailable, SessionExpired, TransientError
from pydantic import BaseModel, ConfigDict

from modules.db import Connection
from modules.sql import read_sql

logger = logging.getLogger(__name__)


class GraphError(RuntimeError):
    """Neo4j가 거절했고 다시 불러도 같은 결과다(인증·제약 위반·쿼리 오류)."""


class _Row(BaseModel):
    """투영에 오가는 값은 전부 불변이다. 재시도 경로에서 바뀌면 원본과 어긋난다."""

    model_config = ConfigDict(frozen=True)


class CausalPathRow(_Row):
    """`market_causal_path` 한 행 중 그래프에 싣는 것만."""

    path_id: int
    week_start: date
    event_title: str | None
    event_occurred_on: date | None
    source_target_kind: str | None
    source_target_code: str | None
    source_sign: str | None
    target_kind: str
    target_code: str
    sign: str
    confidence: str
    reasoning: str
    return_week_change: float
    return_t1_change: float
    return_t5_change: float
    return_unit: str


class CausalStepRow(_Row):
    """`market_causal_step` 한 행. 채널을 id가 아니라 이름으로 갖는다."""

    path_id: int
    position: int
    channel: str


class EventNode(_Row):
    title: str
    occurred_on: date


class ChannelNode(_Row):
    name: str


class TargetNode(_Row):
    kind: str
    code: str


class EventEdge(_Row):
    """사건에서 첫 채널로."""

    path_id: int
    week_start: date
    position: int
    title: str
    occurred_on: date
    channel: str


class TargetEdge(_Row):
    """대상에서 첫 채널로. 앞 주의 결과가 다음 원인이 되는 자리다(설계 §11.4)."""

    path_id: int
    week_start: date
    position: int
    src_kind: str
    src_code: str
    sign: str
    channel: str


class ChainEdge(_Row):
    """채널에서 채널로."""

    path_id: int
    week_start: date
    position: int
    src: str
    dst: str


class HitsEdge(_Row):
    """마지막 채널에서 대상으로. **경로 수준 속성이 여기 실린다** — 주장이 착지하는 자리다."""

    path_id: int
    week_start: date
    channel: str
    kind: str
    code: str
    sign: str
    confidence: str
    reasoning: str
    return_unit: str
    return_week_change: float
    return_t1_change: float
    return_t5_change: float


class GraphPayload(_Row):
    """한 주를 Neo4j에 넣을 모양으로 편 것."""

    events: tuple[EventNode, ...]
    channels: tuple[ChannelNode, ...]
    targets: tuple[TargetNode, ...]
    from_event: tuple[EventEdge, ...]
    from_target: tuple[TargetEdge, ...]
    chain: tuple[ChainEdge, ...]
    hits: tuple[HitsEdge, ...]

    @property
    def node_count(self) -> int:
        return len(self.events) + len(self.channels) + len(self.targets)

    @property
    def edge_count(self) -> int:
        return len(self.from_event) + len(self.from_target) + len(self.chain) + len(self.hits)


# 제약은 붙을 때마다 멱등하게 보장한다. Neo4j는 Alembic 대상이 아니라 마이그레이션 파일로
# 관리하지 않는다. `NODE KEY`를 쓰지 않는 이유는 Enterprise 전용이라 community 이미지에서
# `CREATE CONSTRAINT`가 거절되기 때문이다 — 복합 속성 유일성으로 같은 것을 얻는다.
CONSTRAINTS = (
    ("CREATE CONSTRAINT event_key IF NOT EXISTS FOR (e:Event) REQUIRE (e.title, e.occurred_on) IS UNIQUE"),
    "CREATE CONSTRAINT channel_key IF NOT EXISTS FOR (c:Channel) REQUIRE c.name IS UNIQUE",
    ("CREATE CONSTRAINT target_key IF NOT EXISTS FOR (t:Target) REQUIRE (t.kind, t.code) IS UNIQUE"),
)

# MERGE 키에 `path_id`와 `position`이 들어간다. 재적재가 엣지를 누적하지 않게 하는 장치다.
WRITES: tuple[tuple[str, str], ...] = (
    (
        "events",
        "UNWIND $rows AS r MERGE (:Event {title: r.title, occurred_on: r.occurred_on})",
    ),
    ("channels", "UNWIND $rows AS r MERGE (:Channel {name: r.name})"),
    ("targets", "UNWIND $rows AS r MERGE (:Target {kind: r.kind, code: r.code})"),
    (
        "from_event",
        (
            "UNWIND $rows AS r"
            " MATCH (e:Event {title: r.title, occurred_on: r.occurred_on})"
            " MATCH (c:Channel {name: r.channel})"
            " MERGE (e)-[l:LEADS_TO {path_id: r.path_id, position: r.position}]->(c)"
            " SET l.week_start = r.week_start"
        ),
    ),
    (
        "from_target",
        (
            "UNWIND $rows AS r"
            " MATCH (t:Target {kind: r.src_kind, code: r.src_code})"
            " MATCH (c:Channel {name: r.channel})"
            " MERGE (t)-[l:LEADS_TO {path_id: r.path_id, position: r.position}]->(c)"
            " SET l.week_start = r.week_start, l.sign = r.sign"
        ),
    ),
    (
        "chain",
        (
            "UNWIND $rows AS r"
            " MATCH (a:Channel {name: r.src})"
            " MATCH (b:Channel {name: r.dst})"
            " MERGE (a)-[l:LEADS_TO {path_id: r.path_id, position: r.position}]->(b)"
            " SET l.week_start = r.week_start"
        ),
    ),
    (
        "hits",
        (
            "UNWIND $rows AS r"
            " MATCH (c:Channel {name: r.channel})"
            " MATCH (t:Target {kind: r.kind, code: r.code})"
            " MERGE (c)-[h:HITS {path_id: r.path_id}]->(t)"
            " SET h.week_start = r.week_start, h.sign = r.sign, h.confidence = r.confidence,"
            " h.reasoning = r.reasoning, h.return_unit = r.return_unit,"
            " h.return_week_change = r.return_week_change,"
            " h.return_t1_change = r.return_t1_change,"
            " h.return_t5_change = r.return_t5_change"
        ),
    ),
)


def read_week(connection: Connection, week_start: date) -> tuple[list[CausalPathRow], list[CausalStepRow]]:
    """한 주의 경로와 단계를 읽는다."""
    with connection.cursor() as cursor:
        cursor.execute(
            read_sql("postgres", "market_causal_path", "select_graph_by_week.sql"),
            {"week_start": week_start},
        )
        # 컬럼 순서는 SQL 파일이 정한다. `tests/modules/test_graph.py`가 둘을 대조한다.
        paths = [
            CausalPathRow(
                path_id=row[0],
                week_start=row[1],
                event_title=row[2],
                event_occurred_on=row[3],
                source_target_kind=row[4],
                source_target_code=row[5],
                source_sign=row[6],
                target_kind=row[7],
                target_code=row[8],
                sign=row[9],
                confidence=row[10],
                reasoning=row[11],
                return_week_change=row[12],
                return_t1_change=row[13],
                return_t5_change=row[14],
                return_unit=row[15],
            )
            for row in cursor.fetchall()
        ]

        cursor.execute(
            read_sql("postgres", "market_causal_step", "select_by_week.sql"),
            {"week_start": week_start},
        )
        steps = [CausalStepRow(path_id=row[0], position=row[1], channel=row[2]) for row in cursor.fetchall()]
    return paths, steps


def stored_weeks(connection: Connection) -> list[date]:
    """경로가 있는 주 전부. 초기 적재와 재동기화가 이것으로 돈다."""
    with connection.cursor() as cursor:
        cursor.execute(read_sql("postgres", "market_causal_path", "select_weeks.sql"))
        return [row[0] for row in cursor.fetchall()]


def project(paths: Sequence[CausalPathRow], steps: Sequence[CausalStepRow]) -> GraphPayload:
    """경로 헤더 1행 + 단계 N행을 엣지 N+1개로 편다. 순수 함수다."""
    by_path: dict[int, list[CausalStepRow]] = {}
    for step in steps:
        by_path.setdefault(step.path_id, []).append(step)

    events: dict[tuple[str, date], EventNode] = {}
    channels: dict[str, ChannelNode] = {}
    targets: dict[tuple[str, str], TargetNode] = {}
    from_event: list[EventEdge] = []
    from_target: list[TargetEdge] = []
    chain: list[ChainEdge] = []
    hits: list[HitsEdge] = []

    for path in paths:
        chain_steps = by_path.get(path.path_id, [])
        if not chain_steps:
            # 저장 코드가 헤더와 단계를 한 트랜잭션에 넣으므로 여기 오면 데이터가 깨진 것이다.
            raise GraphError(f"path {path.path_id} has no steps")

        for step in chain_steps:
            channels[step.channel] = ChannelNode(name=step.channel)
        targets[(path.target_kind, path.target_code)] = TargetNode(kind=path.target_kind, code=path.target_code)

        first = chain_steps[0].channel
        if path.event_title is not None and path.event_occurred_on is not None:
            events[(path.event_title, path.event_occurred_on)] = EventNode(
                title=path.event_title, occurred_on=path.event_occurred_on
            )
            from_event.append(
                EventEdge(
                    path_id=path.path_id,
                    week_start=path.week_start,
                    position=0,
                    title=path.event_title,
                    occurred_on=path.event_occurred_on,
                    channel=first,
                )
            )
        elif (
            path.source_target_kind is not None and path.source_target_code is not None and path.source_sign is not None
        ):
            targets[(path.source_target_kind, path.source_target_code)] = TargetNode(
                kind=path.source_target_kind, code=path.source_target_code
            )
            from_target.append(
                TargetEdge(
                    path_id=path.path_id,
                    week_start=path.week_start,
                    position=0,
                    src_kind=path.source_target_kind,
                    src_code=path.source_target_code,
                    sign=path.source_sign,
                    channel=first,
                )
            )
        else:
            # `ck_market_causal_path_source_exclusive`가 DB에서 막는 모양이다. 여기 오면
            # 제약이 빠졌거나 조회가 컬럼을 잘못 골랐다는 뜻이라 조용히 넘기지 않는다.
            raise GraphError(f"path {path.path_id} has neither an event nor a source target")

        for previous, current in pairwise(chain_steps):
            chain.append(
                ChainEdge(
                    path_id=path.path_id,
                    week_start=path.week_start,
                    position=current.position,
                    src=previous.channel,
                    dst=current.channel,
                )
            )

        hits.append(
            HitsEdge(
                path_id=path.path_id,
                week_start=path.week_start,
                channel=chain_steps[-1].channel,
                kind=path.target_kind,
                code=path.target_code,
                sign=path.sign,
                confidence=path.confidence,
                reasoning=path.reasoning,
                return_unit=path.return_unit,
                return_week_change=path.return_week_change,
                return_t1_change=path.return_t1_change,
                return_t5_change=path.return_t5_change,
            )
        )

    return GraphPayload(
        events=tuple(events.values()),
        channels=tuple(channels.values()),
        targets=tuple(targets.values()),
        from_event=tuple(from_event),
        from_target=tuple(from_target),
        chain=tuple(chain),
        hits=tuple(hits),
    )


def write_graph(uri: str, auth: tuple[str, str], payload: GraphPayload) -> None:
    """한 주 몫을 트랜잭션 하나에 넣는다. 부분 반영을 막는다."""
    driver = _driver(uri, auth)
    try:
        with driver, driver.session() as session:
            for statement in CONSTRAINTS:
                session.run(statement)
            session.execute_write(_merge_all, payload)
    except (ServiceUnavailable, SessionExpired, TransientError) as error:
        # 잠시 뒤 다시 부르면 될 실패다. Airflow가 재시도하게 그대로 올린다.
        raise ConnectionError(f"neo4j at {uri} is unavailable: {error}") from error
    except (ClientError, Neo4jError) as error:
        # 인증·제약 위반·쿼리 오류. 다시 불러도 같은 답이다.
        raise GraphError(f"neo4j rejected the write: {error}") from error

    logger.info(
        "projected %d nodes and %d edges into neo4j",
        payload.node_count,
        payload.edge_count,
    )


def _driver(uri: str, auth: tuple[str, str]) -> Driver:
    try:
        return GraphDatabase.driver(uri, auth=auth, max_transaction_retry_time=0)
    except (ServiceUnavailable, SessionExpired) as error:
        raise ConnectionError(f"neo4j at {uri} is unavailable: {error}") from error


def _merge_all(transaction: Any, payload: GraphPayload) -> None:
    for key, statement in WRITES:
        rows = getattr(payload, key)
        if rows:
            transaction.run(statement, rows=[row.model_dump() for row in rows])
