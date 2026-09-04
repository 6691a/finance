"""LLM 실행 원장 조회. **조회 단위가 대화다.**

세션 팩토리를 생성자로 받고, 응답 하나가 세션 하나이며, `relationship()` 대신 `IN` 배치
조회로 읽고 파이썬이 그룹핑한다.

**읽는 표가 `kospi_llm_run`·`kospi_tool_call`이다**(2026-09-03에 갈아 끼웠다). 옛
`thesis_llm_run`을 읽던 자리이고 경로(`/api/llm-runs`)와 화면 모양은 그대로다 — 바뀐 것은
대화의 종류가 둘(`forecast`·`review`)로 좁아지고 **메모 칸 일곱이 는 것**이다.

**실행일 필터의 축은 `started_at`이다.** `run_date`가 아니다 — 장후 관찰의 `run_date`는
그날 세션 날짜라 자정을 넘겨 도는 실행이 목록에서 빠진다. KST 날짜를 UTC 경계로 바꾸는
것은 `apps/core/utility.kst_day_bounds`가 하고 조회문은 `>=`·`<`만 본다.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from pydantic import Field
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.repository.common import DEFAULT_LIMIT, RowBundle
from apps.models.analysis import (
    KospiForecast,
    KospiLlmRun,
    KospiLlmRunStatus,
    KospiToolCall,
)


class LlmRunListRows(RowBundle):
    runs: tuple[KospiLlmRun, ...] = ()
    has_more: bool = False
    # 실행 id → 그 대화가 만든 전망 수. 관찰 대화는 0이다.
    produced: dict[int, int] = Field(default_factory=dict)


class LlmRunDetailRows(RowBundle):
    run: KospiLlmRun
    tool_calls: tuple[KospiToolCall, ...] = ()
    forecasts: tuple[KospiForecast, ...] = ()


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

        `limit + 1`을 읽어 다음 쪽이 있는지만 본다. 총 건수는 세지 않는다.
        """
        statement = select(KospiLlmRun).where(
            KospiLlmRun.started_at >= started_from,
            KospiLlmRun.started_at < started_to,
        )
        if kinds:
            statement = statement.where(KospiLlmRun.kind.in_(kinds))
        if statuses:
            statement = statement.where(KospiLlmRun.status.in_(statuses))
        if run_slots:
            # **관찰 대화는 슬롯이 null이다.** 슬롯으로 거르면 그것들이 빠지는데, 그것이
            # 이 필터의 뜻이다 — "그 슬롯의 전망 대화만".
            statement = statement.where(KospiLlmRun.slot.in_(run_slots))
        return (
            statement.order_by(KospiLlmRun.started_at.desc(), KospiLlmRun.id.desc())
            .limit(limit + 1)
            .offset(offset)
        )

    @staticmethod
    def produced_statement(run_ids: Sequence[int]) -> Select[Any]:
        """대화가 만든 전망 수. 관찰 대화는 행이 없어 0으로 읽힌다."""
        return (
            select(KospiForecast.llm_run_id, func.count(KospiForecast.id))
            .where(KospiForecast.llm_run_id.in_(run_ids))
            .group_by(KospiForecast.llm_run_id)
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
        """실행 목록 한 쪽. 왕복 둘이고 한 세션 안이다."""
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
                return LlmRunListRows(has_more=has_more)
            produced = {
                run_id: count
                for run_id, count in await session.execute(
                    self.produced_statement([run.id for run in found])
                )
                if run_id is not None
            }
        return LlmRunListRows(runs=tuple(found), has_more=has_more, produced=produced)

    async def detail_rows(self, llm_run_id: int) -> LlmRunDetailRows | None:
        """상세에 필요한 행 전부. **결과 전문은 뺀다** — 단건 조회가 준다."""
        async with self._session_factory() as session:
            run = (
                await session.execute(select(KospiLlmRun).where(KospiLlmRun.id == llm_run_id))
            ).scalar_one_or_none()
            if run is None:
                return None
            tool_calls = list(
                (
                    await session.execute(
                        select(KospiToolCall)
                        .where(KospiToolCall.llm_run_id == llm_run_id)
                        .order_by(KospiToolCall.seq)
                    )
                ).scalars()
            )
            forecasts = list(
                (
                    await session.execute(
                        select(KospiForecast)
                        .where(KospiForecast.llm_run_id == llm_run_id)
                        .order_by(KospiForecast.run_date, KospiForecast.as_of_at)
                    )
                ).scalars()
            )
        return LlmRunDetailRows(
            run=run, tool_calls=tuple(tool_calls), forecasts=tuple(forecasts)
        )

    async def tool_call_row(self, llm_run_id: int, seq: int) -> KospiToolCall | None:
        """툴 호출 하나. **결과 전문이 여기에만 있다.**"""
        async with self._session_factory() as session:
            return (
                await session.execute(
                    select(KospiToolCall).where(
                        KospiToolCall.llm_run_id == llm_run_id,
                        KospiToolCall.seq == seq,
                    )
                )
            ).scalar_one_or_none()


# 상태 값을 라우트가 리터럴로 적지 않게 모델 Enum에서 뽑는다.
LLM_RUN_STATUSES: tuple[str, ...] = tuple(status.value for status in KospiLlmRunStatus)
