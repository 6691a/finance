"""추론 기록의 매핑과 그래프 투영. **리포지토리를 생성자로 받는다.**

층의 경계와 파일 규칙은 `apps/api/service/__init__.py`가 갖는다.
"""

from collections import defaultdict
from collections.abc import Sequence
from datetime import date

from apps.api.repository import (
    DEFAULT_LIMIT,
    ThesisDetailRows,
    ThesisGraphRows,
    ThesisListRows,
    ThesisReadRepository,
)
from apps.api.schemas import (
    EvidenceCitation,
    GraphEdge,
    GraphNode,
    GraphResponse,
    LlmRunSummary,
    PrecedentRef,
    ThesisDetail,
    ThesisList,
    ThesisOutcomeItem,
    ThesisSummary,
)
from apps.api.service.common import number
from apps.core.utility import utc_text
from apps.models.analysis import (
    Thesis,
    ThesisEvidence,
    ThesisLlmRun,
    ThesisOutcome,
    ThesisPrecedent,
)


class ThesisReadService:
    """추론 기록을 읽어 응답 계약으로 준다.

    지금은 조회 하나에 매핑 하나씩이라 얇다. **그래도 층을 두는 이유**는 리포지토리가
    응답 모양을 알면 store를 갈아끼울 때 계약까지 함께 흔들려서다 — 그 경계가 이
    기능의 전제(`4-graph.md`의 "Neo4j로 갈아끼워도 응답은 그대로")를 지탱한다.
    """

    def __init__(self, repository: ThesisReadRepository) -> None:
        self._repository = repository

    async def list_page(
        self,
        *,
        run_date_from: date,
        run_date_to: date,
        run_slots: Sequence[str] = (),
        subject_codes: Sequence[str] = (),
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> ThesisList:
        rows = await self._repository.list_rows(
            run_date_from=run_date_from,
            run_date_to=run_date_to,
            run_slots=run_slots,
            subject_codes=subject_codes,
            limit=limit,
            offset=offset,
        )
        return build_list(rows, limit=limit, offset=offset)

    async def detail(self, thesis_id: int) -> ThesisDetail | None:
        rows = await self._repository.detail_rows(thesis_id)
        return None if rows is None else build_detail(rows)

    async def graph(self, thesis_id: int) -> GraphResponse | None:
        rows = await self._repository.graph_rows(thesis_id)
        return None if rows is None else project_graph(rows)


# --- 모양 바꾸기 ---------------------------------------------------------------


def summary_of(thesis: Thesis, grades: tuple[int, int, float | None] = (0, 0, None)) -> ThesisSummary:
    graded, narrated, mean_brier = grades
    return ThesisSummary(
        id=thesis.id,
        run_date=thesis.run_date,
        run_slot=thesis.run_slot.value,
        as_of_at=thesis.as_of_at,
        subject_kind=thesis.subject_kind.value,
        subject_code=thesis.subject_code,
        label=thesis.label,
        prob_up=float(thesis.prob_up),
        prob_down=float(thesis.prob_down),
        prob_flat=float(thesis.prob_flat),
        up_return_pct=number(thesis.up_return_pct),
        down_return_pct=number(thesis.down_return_pct),
        graded_horizons=graded,
        narrated_horizons=narrated,
        mean_brier=mean_brier,
    )


def citation_of(row: ThesisEvidence) -> EvidenceCitation:
    return EvidenceCitation(
        rank=row.rank,
        kind=row.evidence_kind.value,
        ref=row.evidence_ref,
        title=row.evidence_title,
        url=row.evidence_url,
        direction=None if row.direction is None else row.direction.value,
        mechanism=row.mechanism,
        detail=dict(row.detail or {}),
    )


def precedent_of(thesis: Thesis) -> PrecedentRef:
    return PrecedentRef(
        id=thesis.id,
        run_date=thesis.run_date,
        run_slot=thesis.run_slot.value,
        subject_kind=thesis.subject_kind.value,
        subject_code=thesis.subject_code,
        label=thesis.label,
        prob_up=float(thesis.prob_up),
        prob_down=float(thesis.prob_down),
        prob_flat=float(thesis.prob_flat),
    )


def llm_run_of(run: ThesisLlmRun) -> LlmRunSummary:
    return LlmRunSummary(
        id=run.id,
        kind=run.kind.value,
        status=run.status.value,
        llm_model=run.llm_model,
        prompt_version=run.prompt_version,
        try_number=run.try_number,
        started_at=run.started_at,
        finished_at=run.finished_at,
        tool_rounds=run.tool_rounds,
        tool_calls=run.tool_calls,
        tool_result_chars=run.tool_result_chars,
        error=run.error,
    )


def outcome_of(
    row: ThesisOutcome,
    *,
    evidence: Sequence[ThesisEvidence] = (),
    narration_run: ThesisLlmRun | None = None,
) -> ThesisOutcomeItem:
    return ThesisOutcomeItem(
        horizon_days=row.horizon_days,
        as_of_at=row.as_of_at,
        evaluated_at=row.evaluated_at,
        actual_return_pct=number(row.actual_return_pct),
        actual_outcome=None if row.actual_outcome is None else row.actual_outcome.value,
        brier_score=number(row.brier_score),
        predicted_return_pct=number(row.predicted_return_pct),
        return_error_pct=number(row.return_error_pct),
        narrative=row.narrative,
        verdict=None if row.verdict is None else row.verdict.value,
        narrative_at=row.narrative_at,
        llm_model=row.llm_model,
        prompt_version=row.prompt_version,
        narration_run=None if narration_run is None else llm_run_of(narration_run),
        evidence=tuple(citation_of(item) for item in evidence),
    )


def build_list(rows: ThesisListRows, *, limit: int, offset: int) -> ThesisList:
    return ThesisList(
        items=tuple(summary_of(thesis, rows.grades.get(thesis.id, (0, 0, None))) for thesis in rows.theses),
        limit=limit,
        offset=offset,
        has_more=rows.has_more,
    )


def build_detail(rows: ThesisDetailRows) -> ThesisDetail:
    """원 추론의 인용과 지평별 사후 인용을 가른다.

    섞으면 "그래서 왜 이 결론인가"가 흐려진다 — Slack 렌더가 결론 방향의 근거만 그리는
    것과 같은 판단이다.
    """
    by_horizon: dict[int, list[ThesisEvidence]] = defaultdict(list)
    original: list[ThesisEvidence] = []
    for row in rows.citations:
        if row.outcome_horizon_days is None:
            original.append(row)
        else:
            by_horizon[row.outcome_horizon_days].append(row)

    thesis = rows.thesis
    graded = [row for row in rows.outcomes if row.evaluated_at is not None]
    narrated = [row for row in rows.outcomes if row.narrative is not None]
    scores = [float(row.brier_score) for row in graded if row.brier_score is not None]
    summary = summary_of(thesis, (len(graded), len(narrated), sum(scores) / len(scores) if scores else None))

    return ThesisDetail(
        **summary.model_dump(),
        up_reasoning=thesis.up_reasoning,
        down_reasoning=thesis.down_reasoning,
        flat_reasoning=thesis.flat_reasoning,
        input_state=dict(thesis.input_state or {}),
        tool_rounds=thesis.tool_rounds,
        llm_model=thesis.llm_model,
        prompt_version=thesis.prompt_version,
        dag_run_id=thesis.dag_run_id,
        llm_run=None
        if thesis.llm_run_id is None or thesis.llm_run_id not in rows.runs
        else llm_run_of(rows.runs[thesis.llm_run_id]),
        evidence=tuple(citation_of(row) for row in original),
        outcomes=tuple(
            outcome_of(
                row,
                evidence=by_horizon.get(row.horizon_days, ()),
                narration_run=None if row.narration_run_id is None else rows.runs.get(row.narration_run_id),
            )
            for row in rows.outcomes
        ),
        precedents=tuple(
            precedent_of(rows.neighbours[value]) for value in rows.precedent_ids if value in rows.neighbours
        ),
    )


# --- 그래프 투영 ---------------------------------------------------------------


def thesis_node_id(thesis_id: int) -> str:
    """`thesis:812`. `thesis`는 `ThesisEvidenceKind` 값 넷에 없어 근거 ref와 충돌하지 않는다."""
    return f"thesis:{thesis_id}"


def _thesis_node(thesis: Thesis, grade: ThesisOutcome | None) -> GraphNode:
    """`(:Thesis)` 속성은 4-graph.md 2절 목록 그대로다.

    `input_state`·`llm_model`·`prompt_version`·`thesis_evidence.detail`은 싣지 않는다 —
    그 문서가 "미러가 아니라 projection"이라고 정했고 상세 응답이 이미 준다.
    **채점 넷은 지평 0의 값이다**(같은 문서 각주).
    """
    return GraphNode(
        id=thesis_node_id(thesis.id),
        labels=("Thesis",),
        properties={
            "id": thesis.id,
            "run_date": thesis.run_date.isoformat(),
            "run_slot": thesis.run_slot.value,
            "as_of_at": utc_text(thesis.as_of_at),
            "subject_kind": thesis.subject_kind.value,
            "subject_code": thesis.subject_code,
            "label": thesis.label,
            "prob_up": float(thesis.prob_up),
            "prob_down": float(thesis.prob_down),
            "prob_flat": float(thesis.prob_flat),
            "up_reasoning": thesis.up_reasoning,
            "down_reasoning": thesis.down_reasoning,
            "flat_reasoning": thesis.flat_reasoning,
            "evaluated_at": None if grade is None else utc_text(grade.evaluated_at),
            "actual_return_pct": None if grade is None else number(grade.actual_return_pct),
            "actual_outcome": None
            if grade is None or grade.actual_outcome is None
            else grade.actual_outcome.value,
            "brier_score": None if grade is None else number(grade.brier_score),
        },
    )


def _cites_edge(row: ThesisEvidence) -> GraphEdge:
    return GraphEdge(
        type="CITES",
        start=thesis_node_id(row.thesis_id),
        end=row.evidence_ref,
        properties={
            "rank": row.rank,
            "direction": None if row.direction is None else row.direction.value,
            "mechanism": row.mechanism,
        },
    )


def _evidence_node(row: ThesisEvidence) -> GraphNode:
    return GraphNode(
        id=row.evidence_ref,
        labels=("Evidence",),
        properties={
            "kind": row.evidence_kind.value,
            "ref": row.evidence_ref,
            "title": row.evidence_title,
            "url": row.evidence_url,
        },
    )


def _informed_by_edge(edge: ThesisPrecedent) -> GraphEdge:
    return GraphEdge(
        type="INFORMED_BY",
        start=thesis_node_id(edge.thesis_id),
        end=thesis_node_id(edge.precedent_id),
    )


def project_graph(rows: ThesisGraphRows) -> GraphResponse:
    """1홉 이웃 그래프. **이름은 4-graph.md 2절 그대로다.**

    `CITES`는 **원 추론의 인용만**이다(`outcome_horizon_days IS NULL`). 해설의 인용은 원
    판단을 만든 근거가 아니고 지평마다 같은 ref가 반복될 수 있어 이 그래프에 섞지 않는다 —
    상세 응답의 각 지평 안에 남는다.
    """
    nodes: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []

    for thesis in (rows.center, *rows.neighbours.values()):
        node = _thesis_node(thesis, rows.grades.get(thesis.id))
        nodes[node.id] = node

    for row in rows.citations:
        if row.outcome_horizon_days is not None:
            continue
        nodes.setdefault(row.evidence_ref, _evidence_node(row))
        edges.append(_cites_edge(row))

    for edge in rows.edges:
        start, end = thesis_node_id(edge.thesis_id), thesis_node_id(edge.precedent_id)
        if start in nodes and end in nodes:
            edges.append(_informed_by_edge(edge))

    return GraphResponse(
        center=thesis_node_id(rows.center.id),
        nodes=tuple(nodes.values()),
        edges=tuple(edges),
    )
