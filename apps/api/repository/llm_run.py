"""LLM 실행 원장 조회. **조회 단위가 대화다.**

`thesis.py`와 같은 형태다 — 세션 팩토리를 생성자로 받고, 응답 하나가 세션 하나이며,
`relationship()` 대신 `IN` 배치 조회로 읽고 파이썬이 그룹핑한다.

**실행일 필터의 축은 `started_at`이다.** `run_date`가 아니다 — T+5 해설의 `run_date`는
과거 원 추론일이라 그것으로 거르면 오늘 실행한 해설이 목록에서 빠진다. KST 날짜를
UTC 경계로 바꾸는 것은 `apps/core/utility.kst_day_bounds`가 하고 조회문은 `>=`·`<`만 본다.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from pydantic import Field
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.repository.common import DEFAULT_LIMIT, RowBundle
from apps.models.analysis import (
    LlmRunStatus,
    Thesis,
    ThesisLlmRun,
    ThesisOutcome,
    ThesisToolCall,
)


class LlmRunListRows(RowBundle):
    runs: tuple[ThesisLlmRun, ...] = ()
    has_more: bool = False
    # 실행 id → 산출물 수(생성 대화는 추론 수, 해설 대화는 해설한 지평 수)
    produced: dict[int, int] = Field(default_factory=dict)


class LlmRunDetailRows(RowBundle):
    run: ThesisLlmRun
    tool_calls: tuple[ThesisToolCall, ...] = ()
    theses: tuple[Thesis, ...] = ()
    outcomes: tuple[ThesisOutcome, ...] = ()
    # 해설된 지평의 원 추론. 라벨과 대상 코드가 outcome 행에 없다.
    subjects: dict[int, Thesis] = Field(default_factory=dict)


class LlmRunReadRepository:
    """실행 원장을 읽는다. 쓰기 경로는 없다."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    # --- 조회문 (테스트가 컴파일해서 본다) ---------------------------------------

    @staticmethod
    def list_statement(
        *,
        started_from: datetime,
        started_to: datetime,
        kinds: Sequence[str] = (),
        statuses: Sequence[str] = (),
        run_slots: Sequence[str] = (),
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> Select[Any]:
        """목록 조회문. **끝 경계가 열려 있다**(`< started_to`).

        `subject_code` 필터를 두지 않는다 — 대화 하나가 여러 대상을 다루고 실패·중단
        대화에는 산출물이 아예 없다. 대상으로 찾는 것은 `/api/theses`가 답한다.

        `limit + 1`을 읽어 다음 쪽이 있는지만 본다. 총 건수는 세지 않는다.
        """
        statement = select(ThesisLlmRun).where(
            ThesisLlmRun.started_at >= started_from,
            ThesisLlmRun.started_at < started_to,
        )
        if kinds:
            statement = statement.where(ThesisLlmRun.kind.in_(kinds))
        if statuses:
            statement = statement.where(ThesisLlmRun.status.in_(statuses))
        if run_slots:
            statement = statement.where(ThesisLlmRun.run_slot.in_(run_slots))
        return (
            statement.order_by(ThesisLlmRun.started_at.desc(), ThesisLlmRun.id.desc())
            .limit(limit + 1)
            .offset(offset)
        )

    @staticmethod
    def produced_thesis_statement(run_ids: Sequence[int]) -> Select[Any]:
        """생성 대화의 산출물 수."""
        return (
            select(Thesis.llm_run_id, func.count(Thesis.id))
            .where(Thesis.llm_run_id.in_(run_ids))
            .group_by(Thesis.llm_run_id)
        )

    @staticmethod
    def narrated_outcome_statement(run_ids: Sequence[int]) -> Select[Any]:
        """해설 대화의 산출물 수."""
        return (
            select(ThesisOutcome.narration_run_id, func.count(ThesisOutcome.id))
            .where(ThesisOutcome.narration_run_id.in_(run_ids))
            .group_by(ThesisOutcome.narration_run_id)
        )

    # --- 공개 조회 -----------------------------------------------------------

    async def list_rows(
        self,
        *,
        started_from: datetime,
        started_to: datetime,
        kinds: Sequence[str] = (),
        statuses: Sequence[str] = (),
        run_slots: Sequence[str] = (),
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> LlmRunListRows:
        """실행 목록 한 쪽. 왕복 셋이고 한 세션 안이다."""
        async with self._session_factory() as session:
            found = list(
                (
                    await session.execute(
                        self.list_statement(
                            started_from=started_from,
                            started_to=started_to,
                            kinds=kinds,
                            statuses=statuses,
                            run_slots=run_slots,
                            limit=limit,
                            offset=offset,
                        )
                    )
                ).scalars()
            )
            has_more = len(found) > limit
            found = found[:limit]
            if not found:
                return LlmRunListRows()
            run_ids = [run.id for run in found]
            produced = {
                run_id: count
                for run_id, count in await session.execute(self.produced_thesis_statement(run_ids))
            }
            produced |= {
                run_id: count
                for run_id, count in await session.execute(self.narrated_outcome_statement(run_ids))
            }
        return LlmRunListRows(runs=tuple(found), has_more=has_more, produced=produced)

    async def detail_rows(self, llm_run_id: int) -> LlmRunDetailRows | None:
        """상세에 필요한 행 전부. **결과 전문은 뺀다** — 단건 조회가 준다."""
        async with self._session_factory() as session:
            run = (
                await session.execute(select(ThesisLlmRun).where(ThesisLlmRun.id == llm_run_id))
            ).scalar_one_or_none()
            if run is None:
                return None
            tool_calls = list(
                (
                    await session.execute(
                        select(ThesisToolCall)
                        .where(ThesisToolCall.llm_run_id == llm_run_id)
                        .order_by(ThesisToolCall.seq)
                    )
                ).scalars()
            )
            theses = list(
                (
                    await session.execute(
                        select(Thesis)
                        .where(Thesis.llm_run_id == llm_run_id)
                        .order_by(Thesis.subject_kind, Thesis.subject_code)
                    )
                ).scalars()
            )
            outcomes = list(
                (
                    await session.execute(
                        select(ThesisOutcome)
                        .where(ThesisOutcome.narration_run_id == llm_run_id)
                        .order_by(ThesisOutcome.thesis_id, ThesisOutcome.horizon_days)
                    )
                ).scalars()
            )
            subject_ids = [row.thesis_id for row in outcomes]
            subjects = (
                {}
                if not subject_ids
                else {
                    row.id: row
                    for row in (
                        await session.execute(select(Thesis).where(Thesis.id.in_(subject_ids)))
                    ).scalars()
                }
            )
        return LlmRunDetailRows(
            run=run,
            tool_calls=tuple(tool_calls),
            theses=tuple(theses),
            outcomes=tuple(outcomes),
            subjects=subjects,
        )

    async def tool_call_row(self, llm_run_id: int, seq: int) -> ThesisToolCall | None:
        """툴 호출 하나. **결과 전문이 여기에만 있다.**"""
        async with self._session_factory() as session:
            return (
                await session.execute(
                    select(ThesisToolCall).where(
                        ThesisToolCall.llm_run_id == llm_run_id,
                        ThesisToolCall.seq == seq,
                    )
                )
            ).scalar_one_or_none()


# 상태 값을 라우트가 리터럴로 적지 않게 모델 Enum에서 뽑는다.
LLM_RUN_STATUSES: tuple[str, ...] = tuple(status.value for status in LlmRunStatus)
