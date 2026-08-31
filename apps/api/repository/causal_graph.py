"""인과 그래프를 **Neo4j에서** 읽는다.

[4단계 설계](../../../docs/analysis/market-thesis/4-graph.md)의 §8.3이 정한 소비자 둘 중
하나가 여기다. **Postgres가 원본이고 Neo4j는 탐색용 투영이다** — 숫자(실현 등락·근거·
`input_hash`)는 Neo4j에 없으므로 화면의 표는 계속 Postgres가 채운다. 여기서 가져오는 것은
**모양**뿐이다.

## 왜 여기서 다시 읽나

그림은 경로 목록에서도 조립할 수 있고 실제로 그렇게 하고 있었다. 그런데 그 방식은 **한
주에 갇힌다** — 경로 응답이 그 주의 행이라, 주를 넘어 같은 `Target`을 공유하는 사슬은
보이지 않는다. 그것이 Neo4j를 들인 이유고(설계 §1) 이 리포지토리가 존재하는 이유다.

## 코드가 거는 가드 둘

설계 §8.2가 **프롬프트가 아니라 코드**로 걸라고 못 박은 둘이다. 여기는 LLM이 없지만 이유는
같다 — 빠뜨리면 조용히 더 많은/틀린 답이 된다.

- **경로 경계(`path_id`)** — 채널 노드가 모든 경로에 공유되므로, 이 값을 안 실으면 서로 다른
  주장이 `할인율`에서 섞인다.
- **시각 단조 증가(`week_start`)** — `Target` 노드가 주를 넘어 하나라, 안 걸면 08-17 주에 닿은
  `SOX`가 08-10 주의 원인으로 이어지는 **역행 경로**가 나온다.

## Cypher는 우리가 갖는다

설계 §8.2의 두 방법 중 "파라미터만 받고 쿼리는 우리가 갖는" 쪽이다. 질문이 둘뿐이라 그쪽이
짧고, `MATCH`로 시작해 `RETURN`으로 끝나는 문장만 쓴다는 §8.1의 규칙을 상수로 지킬 수 있다.
"""

from datetime import date
from typing import Any, Protocol, Self

from pydantic import BaseModel, ConfigDict

# 읽기 전용이 아닌 낱말. **거절 목록이 아니라 통과 검사의 보조다** — 문장이 `MATCH`로
# 시작해 `RETURN`으로 끝나는지를 먼저 보고, 그 안에 이 낱말이 있으면 거절한다.
# `airflow/modules/graph.py`가 쓰기 쪽이고 두 트리는 서로를 import하지 않는다.
FORBIDDEN = (
    "CREATE",
    "MERGE",
    "DELETE",
    "SET",
    "REMOVE",
    "DROP",
    "LOAD CSV",
    "CALL",
)


class UnsafeCypher(RuntimeError):
    """읽기 전용이 아닌 문장. **삼키지 않는다** — 빈 결과로 바꾸면 "그런 경로가 없다"와
    "우리가 막았다"를 부르는 쪽이 구별하지 못한다."""


def ensure_read_only(cypher: str) -> str:
    """`MATCH`로 시작하고 `RETURN`이 있는 문장만 통과시킨다.

    community 판은 사용자가 `neo4j` 하나뿐이고 역할 기반 권한이 Enterprise 전용이라
    (설계 §8.1) **읽기 전용 계정을 줄 수 없다.** 그래서 가드가 코드에 있다.

    **통과 검사가 먼저이고 거절 목록은 보조다.** 거절 목록만 두면 새 쓰기 문법이 생길 때
    조용히 통과한다 — 통과 조건을 좁게 잡는 편이 낫다는 것이 설계 §8.1의 판단이다.
    """
    text = " ".join(cypher.split())
    upper = text.upper()
    if not upper.startswith("MATCH ") or " RETURN " not in f" {upper} ":
        raise UnsafeCypher(f"read-only queries must MATCH ... RETURN: {text[:80]}")
    for word in FORBIDDEN:
        if word in upper:
            raise UnsafeCypher(f"{word} is not allowed in a read query: {text[:80]}")
    return text


# 주 하나의 서브그래프. **엣지가 `week_start`를 싣고 있어 그 값으로 자른다**(가드 ②).
WEEK_GRAPH = """
MATCH (source)-[edge:LEADS_TO|HITS]->(destination)
WHERE edge.week_start = $week
RETURN labels(source)[0] AS source_label, properties(source) AS source_props,
       labels(destination)[0] AS destination_label,
       properties(destination) AS destination_props,
       type(edge) AS edge_type, properties(edge) AS edge_props
"""

# 대상 하나에서 시작해 주를 넘어 앞뒤로 뻗은 사슬. **이것이 Neo4j를 들인 이유다** —
# 경로 목록만으로는 한 주 안에서만 이을 수 있다.
TARGET_CHAIN = """
MATCH path = (start:Target {kind: $kind, code: $code})-[edges:LEADS_TO|HITS*1..6]->(end:Target)
WHERE all(index IN range(0, size(edges) - 2)
          WHERE edges[index].week_start <= edges[index + 1].week_start)
RETURN [node IN nodes(path) | {label: labels(node)[0], props: properties(node)}] AS nodes,
       [edge IN relationships(path) | {type: type(edge), props: properties(edge)}] AS edges
LIMIT $limit
"""


class GraphNode(BaseModel):
    """그래프 노드 하나. **키는 Postgres의 자연키다**(설계 §2)."""

    model_config = ConfigDict(frozen=True)

    id: str
    kind: str
    label: str


class GraphEdge(BaseModel):
    """엣지 하나. `path_id`와 `week_start`를 반드시 싣는다 — 가드 둘의 값이다."""

    model_config = ConfigDict(frozen=True)

    source: str
    target: str
    type: str
    path_id: int | None = None
    week_start: date | None = None
    position: int | None = None


class GraphRows(BaseModel):
    model_config = ConfigDict(frozen=True)

    nodes: tuple[GraphNode, ...] = ()
    edges: tuple[GraphEdge, ...] = ()


class Session(Protocol):
    """Neo4j 세션 중 우리가 쓰는 것만. 드라이버를 가짜로 바꿔 끼우려고 좁게 잡는다."""

    def run(self, query: str, **parameters: Any) -> Any: ...

    def __enter__(self) -> Self: ...

    def __exit__(self, *args: object) -> None: ...


class Driver(Protocol):
    def session(self) -> Session: ...


def node_of(label: str, props: dict[str, Any]) -> GraphNode:
    """레이블마다 키가 다르다. **id는 그 키를 편 문자열이고 화면의 노드 id가 된다.**"""
    if label == "Event":
        occurred = props.get("occurred_on")
        return GraphNode(
            id=f"event:{props.get('title')}:{occurred}",
            kind="event",
            label=str(props.get("title", "")),
        )
    if label == "Channel":
        name = str(props.get("name", ""))
        return GraphNode(id=f"channel:{name}", kind="channel", label=name)
    kind, code = props.get("kind", ""), props.get("code", "")
    return GraphNode(id=f"target:{kind}:{code}", kind="target", label=str(code))


def as_date(value: Any) -> date | None:
    """Neo4j `Date`를 파이썬 `date`로. **드라이버 타입을 응답까지 들이지 않는다.**

    `neo4j.time.Date`는 `date`의 하위형이 아니라서 Pydantic이 그대로는 못 받는다.
    """
    if value is None or isinstance(value, date):
        return value if isinstance(value, date) else None
    native = getattr(value, "to_native", None)
    return native() if callable(native) else None


def edge_of(source: GraphNode, destination: GraphNode, edge_type: str, props: dict[str, Any]) -> GraphEdge:
    return GraphEdge(
        source=source.id,
        target=destination.id,
        type=edge_type,
        path_id=props.get("path_id"),
        week_start=as_date(props.get("week_start")),
        position=props.get("position"),
    )


class CausalGraphReadRepository:
    """Neo4j를 읽는다. 쓰기 경로는 없고 가드가 그것을 문장 단위로 강제한다."""

    def __init__(self, driver: Driver | None) -> None:
        # **드라이버가 없을 수 있다.** `NEO4J_URI`를 안 준 실행(로컬·테스트)에서는 그래프만
        # 꺼지고 나머지 화면은 그대로 돈다. 그 판단은 서비스가 한다.
        self._driver = driver

    @property
    def enabled(self) -> bool:
        return self._driver is not None

    def _read(self, cypher: str, **parameters: Any) -> list[dict[str, Any]]:
        if self._driver is None:
            raise RuntimeError("neo4j driver is not configured")
        with self._driver.session() as session:
            return [dict(record) for record in session.run(ensure_read_only(cypher), **parameters)]

    def week_graph(self, week: date) -> GraphRows:
        """그 주의 노드와 엣지 전부."""
        nodes: dict[str, GraphNode] = {}
        edges: list[GraphEdge] = []
        # **`date`를 그대로 넘긴다.** 투영이 `week_start`를 Neo4j `Date`로 저장해서
        # ISO 문자열로 비교하면 아무 것도 안 걸린다(2026-08-30에 그렇게 0건이 나왔다).
        for row in self._read(WEEK_GRAPH, week=week):
            source = node_of(row["source_label"], row["source_props"])
            destination = node_of(row["destination_label"], row["destination_props"])
            nodes.setdefault(source.id, source)
            nodes.setdefault(destination.id, destination)
            edges.append(edge_of(source, destination, row["edge_type"], row["edge_props"]))
        return GraphRows(nodes=tuple(nodes.values()), edges=tuple(edges))

    def target_chain(self, *, kind: str, code: str, limit: int = 50) -> GraphRows:
        """대상 하나에서 **주를 넘어** 뻗은 사슬. 시각이 역행하는 경로는 조회가 뺀다."""
        nodes: dict[str, GraphNode] = {}
        edges: dict[tuple[str, str, int | None], GraphEdge] = {}
        for row in self._read(TARGET_CHAIN, kind=kind, code=code, limit=limit):
            walked = [node_of(item["label"], item["props"]) for item in row["nodes"]]
            for node in walked:
                nodes.setdefault(node.id, node)
            for index, item in enumerate(row["edges"]):
                edge = edge_of(walked[index], walked[index + 1], item["type"], item["props"])
                edges.setdefault((edge.source, edge.target, edge.path_id), edge)
        return GraphRows(nodes=tuple(nodes.values()), edges=tuple(edges.values()))
