"""조회 API의 라우팅·매핑·직렬화.

**가짜는 리포지토리 자리에 둔다.** 그러면 진짜 `ThesisReadService`가 그 위에서 돌아
HTTP 경로가 라우팅·매핑·직렬화를 통째로 지나간다 — 없는 것은 세션뿐이다.

끼우는 방법은 `container.thesis_repository.override(...)`다. `dependency_injector`의
문서화된 형태이고, 그것이 먹는다는 것 자체가 wiring이 풀렸다는 증거이기도 하다 —
마커가 안 풀리면 주입 자리에 `Provide` 객체가 그대로 들어온다.
"""

from typing import Any

import httpx
import pytest
from dependency_injector import providers

from apps.api.app import create_app
from apps.api.repository import ThesisDetailRows, ThesisGraphRows, ThesisListRows
from apps.models.analysis import ThesisPrecedent
from tests.api.conftest import (
    container,
    evidence_row,
    llm_run_row,
    outcome_row,
    thesis_row,
)


class FakeRepository:
    """행 묶음만 돌려준다. 그 위의 진짜 서비스가 응답 계약을 만든다."""

    def __init__(self, **rows: Any) -> None:
        self.rows = rows
        self.calls: list[dict[str, Any]] = []

    def _thesis(self, thesis_id: int) -> Any:
        return next((row for row in self.rows.get("theses", []) if row.id == thesis_id), None)

    async def list_rows(self, **kwargs: Any) -> ThesisListRows:
        self.calls.append(kwargs)
        return ThesisListRows(
            theses=tuple(self.rows.get("theses", [])),
            has_more=self.rows.get("has_more", False),
            grades=self.rows.get("summary", {}),
        )

    async def detail_rows(self, thesis_id: int) -> ThesisDetailRows | None:
        thesis = self._thesis(thesis_id)
        if thesis is None:
            return None
        return ThesisDetailRows(
            thesis=thesis,
            citations=tuple(self.rows.get("evidence", [])),
            outcomes=tuple(self.rows.get("outcomes", [])),
            precedent_ids=tuple(self.rows.get("precedent_ids", [])),
            neighbours={row.id: row for row in self.rows.get("neighbours", [])},
            runs={row.id: row for row in self.rows.get("runs", [])},
        )

    async def graph_rows(self, thesis_id: int) -> ThesisGraphRows | None:
        thesis = self._thesis(thesis_id)
        if thesis is None:
            return None
        return ThesisGraphRows(
            center=thesis,
            citations=tuple(self.rows.get("evidence", [])),
            edges=tuple(self.rows.get("precedents", [])),
            neighbours={row.id: row for row in self.rows.get("neighbours", [])},
            grades={row.thesis_id: row for row in self.rows.get("grades", [])},
        )


def app_with(fake: FakeRepository):
    built = container()
    # provider override가 먹는다는 것 자체가 wiring이 풀렸다는 증거다.
    built.thesis_repository.override(providers.Object(fake))
    return create_app(built)


def client(**rows: Any) -> httpx.AsyncClient:
    app = app_with(FakeRepository(**rows))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_the_route_set_is_what_we_meant_to_publish():
    """라우터를 등록 안 한 실수를 잡는다. DAG 구조 테스트와 같은 성격이다."""
    app = create_app(container())

    published = {route.path for route in app.routes if route.path.startswith(("/api", "/healthz"))}

    assert published == {"/healthz", "/api/theses", "/api/theses/{thesis_id}", "/api/theses/{thesis_id}/graph"}


@pytest.mark.asyncio
async def test_health_does_not_touch_the_database():
    """DB를 보면 깜빡임이 컨테이너 재시작 루프가 된다. 리포지토리 없이도 200이어야 한다."""
    async with client() as http:
        reply = await http.get("/healthz")

    assert reply.status_code == 200
    assert reply.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_the_service_arrives_by_injection_not_a_lookup():
    """마커가 안 풀리면 주입 자리에 `Provide` 객체가 들어와 조용히 틀린다.

    서비스가 리포지토리를 생성자로 받은 것도 함께 확인된다 — 안 그러면 조회가 비어 온다.
    """
    async with client(theses=[thesis_row()]) as http:
        reply = await http.get("/api/theses")

    assert reply.status_code == 200
    assert reply.json()["items"][0]["id"] == 1


@pytest.mark.asyncio
async def test_the_list_carries_the_expected_return_and_the_grade_summary():
    async with client(theses=[thesis_row()], summary={1: (2, 1, 0.51)}) as http:
        payload = (await http.get("/api/theses")).json()

    item = payload["items"][0]
    assert item["up_return_pct"] == 0.8
    assert item["down_return_pct"] == 1.2
    assert (item["graded_horizons"], item["narrated_horizons"], item["mean_brier"]) == (2, 1, 0.51)
    # 이유와 관측 상태는 목록에 없다. 100건이면 응답이 수백 KB가 된다.
    assert "up_reasoning" not in item
    assert "input_state" not in item


@pytest.mark.asyncio
async def test_times_end_with_z_not_an_offset():
    """프로젝트 규칙이 `Z`를 요구한다. Pydantic 기본 직렬화는 `+00:00`이다."""
    async with client(theses=[thesis_row()]) as http:
        payload = (await http.get("/api/theses")).json()

    assert payload["items"][0]["as_of_at"].endswith("Z")
    assert "+00:00" not in payload["items"][0]["as_of_at"]


@pytest.mark.asyncio
async def test_probabilities_are_json_numbers_not_strings():
    """`Decimal`을 그대로 두면 클라이언트가 매번 파싱한다."""
    async with client(theses=[thesis_row()]) as http:
        payload = (await http.get("/api/theses")).json()

    assert isinstance(payload["items"][0]["prob_up"], float)


@pytest.mark.asyncio
async def test_a_limit_over_the_cap_is_refused():
    """날짜 구간이 넓을 때의 폭주를 막는다."""
    async with client(theses=[]) as http:
        assert (await http.get("/api/theses", params={"limit": 201})).status_code == 422
        assert (await http.get("/api/theses", params={"limit": 200})).status_code == 200


@pytest.mark.asyncio
async def test_the_default_window_is_two_weeks_of_kst_days():
    """`run_date`가 KST 세션 날짜라 UTC 날짜로 창을 잡으면 하루가 어긋난다."""
    fake = FakeRepository(theses=[])
    app = app_with(fake)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        await http.get("/api/theses")

    window = fake.calls[0]
    assert (window["run_date_to"] - window["run_date_from"]).days == 13


@pytest.mark.asyncio
async def test_a_missing_thesis_is_a_404_not_an_empty_body():
    async with client(theses=[]) as http:
        assert (await http.get("/api/theses/999")).status_code == 404
        assert (await http.get("/api/theses/999/graph")).status_code == 404


@pytest.mark.asyncio
async def test_the_detail_splits_the_original_citations_from_the_narrative_ones():
    """지평별 사후 인용이 원 판단의 근거에 섞이면 "왜 그 결론인가"가 흐려진다."""
    rows = {
        "theses": [thesis_row()],
        "evidence": [evidence_row(), evidence_row(horizon=1, rank=1)],
        "outcomes": [outcome_row(0), outcome_row(1, narration_run_id=10)],
        "runs": [llm_run_row(), llm_run_row(10)],
    }
    async with client(**rows) as http:
        payload = (await http.get("/api/theses/1")).json()

    assert len(payload["evidence"]) == 1
    assert payload["evidence"][0]["direction"] == "down"
    horizon_one = next(row for row in payload["outcomes"] if row["horizon_days"] == 1)
    assert len(horizon_one["evidence"]) == 1
    # 사후 인용에는 방향·경로가 없다(DB에서 NULL이다).
    assert horizon_one["evidence"][0]["direction"] is None


@pytest.mark.asyncio
async def test_the_size_grade_only_shows_up_on_horizon_zero():
    """크기의 창이 확률과 같은 창이라 5영업일 누적에 대조하면 항상 과소로 나온다."""
    rows = {"theses": [thesis_row()], "outcomes": [outcome_row(0), outcome_row(1)]}
    async with client(**rows) as http:
        payload = (await http.get("/api/theses/1")).json()

    by_horizon = {row["horizon_days"]: row for row in payload["outcomes"]}
    assert by_horizon[0]["return_error_pct"] == 0.3
    assert by_horizon[1]["return_error_pct"] is None


@pytest.mark.asyncio
async def test_the_detail_links_its_conversation_without_copying_the_tool_calls():
    """대화 하나가 여러 thesis를 만든다. 같은 배열을 모든 상세에 복제하지 않는다."""
    async with client(theses=[thesis_row()], runs=[llm_run_row()]) as http:
        payload = (await http.get("/api/theses/1")).json()

    assert payload["llm_run"]["id"] == 9
    assert payload["llm_run"]["tool_calls"] == 11
    assert "arguments" not in str(payload["llm_run"])


@pytest.mark.asyncio
async def test_a_thesis_from_before_the_ledger_still_renders():
    """리비전 전 행은 `llm_run_id`가 NULL이다. 화면 전체를 실패시키지 않는다."""
    async with client(theses=[thesis_row(llm_run_id=None)]) as http:
        payload = (await http.get("/api/theses/1")).json()

    assert payload["llm_run"] is None


@pytest.mark.asyncio
async def test_the_graph_uses_the_names_stage_four_pinned():
    """`4-graph.md` 2절이 정한 라벨·관계 이름을 글자 그대로 쓴다."""
    rows = {
        "theses": [thesis_row()],
        "evidence": [evidence_row(), evidence_row(horizon=1)],
        "precedents": [ThesisPrecedent(thesis_id=1, precedent_id=2)],
        "neighbours": [thesis_row(2, "KOSDAQ", llm_run_id=None)],
        "grades": [outcome_row(0)],
    }
    async with client(**rows) as http:
        payload = (await http.get("/api/theses/1/graph")).json()

    assert payload["center"] == "thesis:1"
    labels = {label for node in payload["nodes"] for label in node["labels"]}
    assert labels == {"Thesis", "Evidence"}
    assert {edge["type"] for edge in payload["edges"]} == {"CITES", "INFORMED_BY"}


@pytest.mark.asyncio
async def test_the_graph_ids_reuse_the_evidence_ref_syntax():
    """`Evidence` 노드 id가 `evidence_ref` 그 자체다. 접두가 kind와 같은 것을 모델이 보장한다."""
    async with client(theses=[thesis_row()], evidence=[evidence_row()]) as http:
        payload = (await http.get("/api/theses/1/graph")).json()

    assert {node["id"] for node in payload["nodes"]} == {"thesis:1", "document:4471"}


@pytest.mark.asyncio
async def test_the_graph_never_carries_narrative_citations():
    """지평마다 같은 ref가 반복돼 엣지가 부푼다. 사후 인용은 상세에만 남는다."""
    rows = {"theses": [thesis_row()], "evidence": [evidence_row(horizon=1), evidence_row(horizon=3)]}
    async with client(**rows) as http:
        payload = (await http.get("/api/theses/1/graph")).json()

    assert payload["edges"] == []
    assert {node["id"] for node in payload["nodes"]} == {"thesis:1"}


@pytest.mark.asyncio
async def test_the_graph_node_carries_the_zero_horizon_grade():
    """`(:Thesis)` 속성은 단수인데 채점은 지평 넷짜리 다중 행이다. 지평 0을 싣는다."""
    async with client(theses=[thesis_row()], grades=[outcome_row(0)]) as http:
        payload = (await http.get("/api/theses/1/graph")).json()

    center = next(node for node in payload["nodes"] if node["id"] == "thesis:1")
    assert center["properties"]["brier_score"] == 0.51
    # projection이지 미러가 아니다. 프롬프트 스냅샷은 안 싣는다.
    assert "input_state" not in center["properties"]


@pytest.mark.asyncio
async def test_every_graph_edge_is_unique_within_the_response():
    """프런트가 `type:start:end`로 안정적인 id를 만들 수 있어야 한다.

    같은 (thesis, horizon, kind, ref)가 두 번 오는 일은 `uq_thesis_evidence_ref`가 DB에서
    막는다. 그래서 투영이 방어적으로 중복을 지우지 않는다 — 지우면 그 UNIQUE가 깨진 날을
    조용히 덮는다.
    """
    rows = {
        "theses": [thesis_row()],
        "evidence": [evidence_row(rank=1), evidence_row(rank=2, ref="disclosure:2026")],
        "precedents": [ThesisPrecedent(thesis_id=1, precedent_id=2)],
        "neighbours": [thesis_row(2, "KOSDAQ", llm_run_id=None)],
    }
    async with client(**rows) as http:
        payload = (await http.get("/api/theses/1/graph")).json()

    keys = [(edge["type"], edge["start"], edge["end"]) for edge in payload["edges"]]
    assert len(keys) == len(set(keys))
