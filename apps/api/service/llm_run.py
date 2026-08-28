"""실행 원장의 매핑. **리포지토리를 생성자로 받는다.**

층의 경계와 파일 규칙은 `apps/api/service/__init__.py`가 갖는다.

**URL을 서비스가 만든다.** 프런트가 문자열을 조립하면 경로 규칙이 두 트리에 생기고,
경로를 바꾼 날 한쪽만 고쳐진다.
"""

from collections.abc import Sequence
from datetime import datetime

from apps.api.repository import (
    DEFAULT_LIMIT,
    LlmRunDetailRows,
    LlmRunListRows,
    LlmRunReadRepository,
)
from apps.api.schemas import (
    LlmRunDetail,
    LlmRunItem,
    LlmRunList,
    NarratedOutcome,
    ProducedThesis,
    ToolCallDetail,
    ToolCallSummary,
)
from apps.models.analysis import Thesis, ThesisLlmRun, ThesisOutcome, ThesisToolCall


def run_url(llm_run_id: int) -> str:
    return f"/api/llm-runs/{llm_run_id}"


def tool_call_url(llm_run_id: int, seq: int) -> str:
    return f"/api/llm-runs/{llm_run_id}/tool-calls/{seq}"


def thesis_url(thesis_id: int) -> str:
    return f"/api/theses/{thesis_id}"


def duration_ms(started_at: datetime, finished_at: datetime | None) -> int | None:
    """전체 소요. **끝나지 않았으면 null이다** — 지금 시각으로 채우면 조회할 때마다 값이 변한다."""
    if finished_at is None:
        return None
    return int((finished_at - started_at).total_seconds() * 1000)


def item_of(run: ThesisLlmRun, produced: int = 0) -> LlmRunItem:
    return LlmRunItem(
        id=run.id,
        kind=run.kind.value,
        run_date=run.run_date,
        run_slot=run.run_slot.value,
        horizon_days=run.horizon_days,
        as_of_at=run.as_of_at,
        dag_run_id=run.dag_run_id,
        try_number=run.try_number,
        llm_model=run.llm_model,
        prompt_version=run.prompt_version,
        started_at=run.started_at,
        finished_at=run.finished_at,
        duration_ms=duration_ms(run.started_at, run.finished_at),
        status=run.status.value,
        error=run.error,
        tool_rounds=run.tool_rounds,
        tool_call_count=run.tool_calls,
        tool_result_chars=run.tool_result_chars,
        investigation_truncated=run.investigation_truncated,
        produced_count=produced,
        url=run_url(run.id),
    )


def tool_call_of(row: ThesisToolCall) -> ToolCallSummary:
    """**결과 전문을 싣지 않는다.** 상세 화면의 목록이 이 모양이다."""
    return ToolCallSummary(
        seq=row.seq,
        round_no=row.round_no,
        tool_call_id=row.tool_call_id,
        tool_name=row.tool_name,
        arguments=dict(row.arguments or {}),
        validated_arguments=None if row.validated_arguments is None else dict(row.validated_arguments),
        requested_at=row.requested_at,
        duration_ms=row.duration_ms,
        result_chars=row.result_chars,
        delivered=row.delivered,
        error_kind=None if row.error_kind is None else row.error_kind.value,
        error=row.error,
        url=tool_call_url(row.llm_run_id, row.seq),
    )


def tool_call_detail_of(row: ThesisToolCall) -> ToolCallDetail:
    return ToolCallDetail(**tool_call_of(row).model_dump(), result=row.result)


def produced_of(thesis: Thesis) -> ProducedThesis:
    return ProducedThesis(
        id=thesis.id,
        run_date=thesis.run_date,
        run_slot=thesis.run_slot.value,
        subject_kind=thesis.subject_kind.value,
        subject_code=thesis.subject_code,
        label=thesis.label,
        url=thesis_url(thesis.id),
    )


def narrated_of(row: ThesisOutcome, subject: Thesis | None) -> NarratedOutcome:
    return NarratedOutcome(
        thesis_id=row.thesis_id,
        horizon_days=row.horizon_days,
        subject_code="" if subject is None else subject.subject_code,
        label="" if subject is None else subject.label,
        verdict=None if row.verdict is None else row.verdict.value,
        url=thesis_url(row.thesis_id),
    )


def build_list(rows: LlmRunListRows, *, limit: int, offset: int) -> LlmRunList:
    return LlmRunList(
        items=tuple(item_of(run, rows.produced.get(run.id, 0)) for run in rows.runs),
        limit=limit,
        offset=offset,
        has_more=rows.has_more,
    )


def build_detail(rows: LlmRunDetailRows) -> LlmRunDetail:
    """실행 상세. **산출물 둘 중 하나는 언제나 빈 배열이다** — 대화 하나가 생성이거나 해설이다."""
    return LlmRunDetail(
        **item_of(rows.run, len(rows.theses) + len(rows.outcomes)).model_dump(),
        tool_calls=tuple(tool_call_of(row) for row in rows.tool_calls),
        produced_theses=tuple(produced_of(row) for row in rows.theses),
        narrated_outcomes=tuple(narrated_of(row, rows.subjects.get(row.thesis_id)) for row in rows.outcomes),
    )


class LlmRunReadService:
    """실행 원장을 읽어 응답 계약으로 준다."""

    def __init__(self, repository: LlmRunReadRepository) -> None:
        self._repository = repository

    async def list_page(
        self,
        *,
        started_from: datetime,
        started_to: datetime,
        kinds: Sequence[str] = (),
        statuses: Sequence[str] = (),
        run_slots: Sequence[str] = (),
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> LlmRunList:
        rows = await self._repository.list_rows(
            started_from=started_from,
            started_to=started_to,
            kinds=kinds,
            statuses=statuses,
            run_slots=run_slots,
            limit=limit,
            offset=offset,
        )
        return build_list(rows, limit=limit, offset=offset)

    async def detail(self, llm_run_id: int) -> LlmRunDetail | None:
        rows = await self._repository.detail_rows(llm_run_id)
        return None if rows is None else build_detail(rows)

    async def tool_call(self, llm_run_id: int, seq: int) -> ToolCallDetail | None:
        row = await self._repository.tool_call_row(llm_run_id, seq)
        return None if row is None else tool_call_detail_of(row)
