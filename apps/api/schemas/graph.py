"""이웃 그래프의 응답 계약.

**이름은 `docs/analysis/market-thesis/4-graph.md` 2절 그대로다.** 지금은 Postgres에서 읽고,
Neo4j를 조회 원본으로 채택할 때 읽는 곳만 갈아끼운다. 그때 응답이 바뀌면 클라이언트를 같이
고쳐야 하므로 처음부터 그쪽 모양으로 낸다.

노드 id 규약만 그 문서가 안 정했다 — Neo4j element id는 불투명해 응답에 실을 수 없고 두
라벨의 키 모양도 다르다(`Thesis.id` 정수 vs `Evidence (kind, ref)` 복합). 저장소에 이미
있는 문법을 재사용한다.
"""

from typing import Any, Literal

from pydantic import Field

from apps.api.schemas.common import ApiModel


class GraphNode(ApiModel):
    """그래프 노드 하나. 라벨은 Thesis 또는 Evidence다."""

    id: str = Field(
        description=(
            "`thesis:812` 또는 `document:4471`. **Evidence 노드의 id는 `evidence_ref` "
            "그 자체다** — 상세 응답의 `evidence[].ref`와 글자 그대로 같아 그것으로 잇는다. "
            "`thesis`는 근거 종류 넷에 없어 충돌하지 않는다."
        )
    )
    labels: tuple[str, ...] = Field(
        description="Neo4j 라벨. `(\"Thesis\",)` 또는 `(\"Evidence\",)` 하나뿐이다."
    )
    properties: dict[str, Any] = Field(
        description=(
            "노드 속성. **라벨마다 모양이 다르다** — Thesis는 run_date·run_slot·subject_code·"
            "label·확률 셋, Evidence는 kind·title·url이다."
        )
    )


class GraphEdge(ApiModel):
    """그래프 간선 하나. `(type, start, end)`가 응답 안에서 유일해 id를 만들지 않는다."""

    type: Literal["CITES", "INFORMED_BY"] = Field(
        description=(
            "CITES는 추론 → 근거(인용), INFORMED_BY는 추론 → 추론(프롬프트에서 봄)."
        )
    )
    start: str = Field(description="출발 노드 id. **둘 다 Thesis 노드다.**")
    end: str = Field(description="도착 노드 id. CITES는 Evidence, INFORMED_BY는 Thesis다.")
    properties: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "간선 속성. CITES는 rank·direction·mechanism, INFORMED_BY는 비어 있다."
        ),
    )


class GraphResponse(ApiModel):
    """중심 추론의 1홉."""

    center: str = Field(description="중심 노드 id(`thesis:<id>`). nodes 안에 반드시 들어 있다.")
    nodes: tuple[GraphNode, ...] = Field(
        default=(),
        description="중심과 1홉 이웃 전부. 실측 최대 18개다.",
    )
    edges: tuple[GraphEdge, ...] = Field(
        default=(),
        description=(
            "1홉 간선 전부. **`INFORMED_BY`는 나가는 것과 들어오는 것 양쪽이다** — "
            "나가는 쪽만 주면 \"이 판단을 누가 참고했나\"를 못 본다."
        ),
    )
