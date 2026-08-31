"""인과 그래프를 Neo4j에서 읽는 층.

주제 셋: ① 쓰기 문장을 코드가 막는다 ② 가드 둘(`path_id`·`week_start`)이 조회에 있다
③ 그래프 DB가 없으면 **빈 그래프가 아니라** 503이다.
"""

from datetime import date
from typing import Any, Self

import httpx
import pytest
from dependency_injector import providers

from apps.api.app import create_app
from apps.api.repository.causal_graph import (
    TARGET_CHAIN,
    WEEK_GRAPH,
    CausalGraphReadRepository,
    UnsafeCypher,
    ensure_read_only,
)
from tests.api.conftest import container

WEEK = date(2026, 8, 10)


class FakeSession:
    def __init__(self, rows: list[dict[str, Any]], log: list[tuple[str, dict[str, Any]]]) -> None:
        self._rows = rows
        self._log = log

    def run(self, query: str, **parameters: Any) -> list[dict[str, Any]]:
        self._log.append((query, parameters))
        return self._rows

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class FakeDriver:
    """드라이버를 가짜로 바꿔 끼운다. **여기서 볼 것은 Neo4j가 아니라 우리 코드다.**"""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def session(self) -> FakeSession:
        return FakeSession(self.rows, self.calls)


def test_a_write_statement_is_refused():
    """community 판에는 읽기 전용 계정이 없다(설계 §8.1). 그래서 가드가 코드에 있다."""
    for cypher in (
        "MATCH (n) DELETE n RETURN 1",
        "CREATE (n:Event) RETURN n",
        "MATCH (n) SET n.x = 1 RETURN n",
        "MATCH (n) CALL db.labels() RETURN 1",
        "MERGE (n:Event {title: 'x'}) RETURN n",
    ):
        with pytest.raises(UnsafeCypher):
            ensure_read_only(cypher)


def test_our_own_queries_pass_the_guard():
    """상수 쿼리가 그 규칙을 지키는지도 함께 본다 — 나중에 고칠 때 여기서 걸린다."""
    assert ensure_read_only(WEEK_GRAPH).startswith("MATCH ")
    assert ensure_read_only(TARGET_CHAIN).startswith("MATCH ")


def test_the_week_query_cuts_by_week_and_carries_the_path():
    """가드 둘이다. `week_start`가 없으면 시각이 역행하고, `path_id`가 없으면 서로 다른
    주장이 `할인율`에서 섞인다(설계 §2.1)."""
    assert "edge.week_start = $week" in WEEK_GRAPH
    assert "properties(edge)" in WEEK_GRAPH

    # 사슬 조회는 단조 증가를 조건으로 건다.
    assert "edges[index].week_start <= edges[index + 1].week_start" in TARGET_CHAIN


def test_the_week_query_gets_a_date_not_a_string():
    """투영이 `week_start`를 Neo4j `Date`로 저장한다. 문자열로 비교하면 조용히 0건이다."""
    driver = FakeDriver()
    CausalGraphReadRepository(driver).week_graph(WEEK)

    _, parameters = driver.calls[0]
    assert parameters["week"] == WEEK


def test_the_rows_become_nodes_and_edges():
    rows = [
        {
            "source_label": "Event",
            "source_props": {"title": "미국 물가 둔화", "occurred_on": date(2026, 8, 12)},
            "destination_label": "Channel",
            "destination_props": {"name": "할인율"},
            "edge_type": "LEADS_TO",
            "edge_props": {"path_id": 35, "week_start": WEEK, "position": 1},
        },
        {
            "source_label": "Channel",
            "source_props": {"name": "할인율"},
            "destination_label": "Target",
            "destination_props": {"kind": "quote", "code": "US10Y"},
            "edge_type": "HITS",
            "edge_props": {"path_id": 35, "week_start": WEEK},
        },
    ]
    graph = CausalGraphReadRepository(FakeDriver(rows)).week_graph(WEEK)

    # 채널 노드가 두 행에 걸쳐 있어도 하나다 — 그 공유가 그래프의 요점이다.
    assert [node.id for node in graph.nodes] == [
        "event:미국 물가 둔화:2026-08-12",
        "channel:할인율",
        "target:quote:US10Y",
    ]
    assert [edge.path_id for edge in graph.edges] == [35, 35]
    assert graph.edges[0].week_start == WEEK


@pytest.mark.asyncio
async def test_a_process_without_neo4j_says_so_instead_of_drawing_nothing():
    """**빈 그래프로 위장하지 않는다.** "그 주에 경로가 없다"와 "그래프 DB가 꺼져 있다"는
    화면이 다르게 말해야 한다."""
    built = container()
    built.neo4j_driver.override(providers.Object(None))
    app = create_app(built)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        reply = await http.get("/api/causal/graph", params={"week": "2026-08-10"})

    assert reply.status_code == 503
    assert "neo4j" in reply.json()["detail"]


@pytest.mark.asyncio
async def test_the_graph_route_answers_from_the_projection():
    rows = [
        {
            "source_label": "Target",
            "source_props": {"kind": "quote", "code": "SOX"},
            "destination_label": "Channel",
            "destination_props": {"name": "이익 기대"},
            "edge_type": "LEADS_TO",
            "edge_props": {"path_id": 55, "week_start": WEEK, "position": 1},
        }
    ]
    built = container()
    built.neo4j_driver.override(providers.Object(FakeDriver(rows)))
    app = create_app(built)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        payload = (await http.get("/api/causal/graph", params={"week": "2026-08-10"})).json()

    assert payload["source"] == "neo4j"
    assert {node["kind"] for node in payload["nodes"]} == {"target", "channel"}
    assert payload["edges"][0]["path_id"] == 55
