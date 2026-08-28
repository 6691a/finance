"""품질 집계 조회. **조회 셋을 나눠 읽고 합치는 것은 서비스가 한다.**

한 조회로 합치지 않는 이유는 셋의 단위가 다르기 때문이다 — 채점은 `thesis_outcome` 행
단위, 툴 평균은 **서로 다른 실행** 단위, 판정은 해설이 붙은 행 단위다. 억지로 한 SELECT에
넣으면 실행 하나가 추론 수만큼 곱해져 툴 평균이 조용히 편향된다.

**행 모양은 여기서 Pydantic 모델로 못 박는다.** SQLAlchemy `Row`를 그대로 서비스로
넘기면 칸 이름 오타가 실행 시점까지 산다.
"""

from collections.abc import Sequence
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Date, Select, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.models.analysis import (
    LlmRunStatus,
    Thesis,
    ThesisLlmRun,
    ThesisOutcome,
    ThesisVerdict,
)


class QualityRow(BaseModel):
    """집계 행의 공통 형태. 만든 뒤 바뀌지 않는다."""

    model_config = ConfigDict(frozen=True)


class ForecastGrade(QualityRow):
    """채점 집계 한 행. 키는 `(week_start, horizon_days, run_slot, llm_model, prompt_version)`."""

    week_start: date
    horizon_days: int
    run_slot: str
    llm_model: str
    prompt_version: str
    mean_brier: float | None = None
    brier_samples: int = 0
    mean_return_error_pct: float | None = None
    mae_return_pct: float | None = None
    return_samples: int = 0


class ForecastRunStat(QualityRow):
    """툴 사용 집계 한 행. **서로 다른 실행만 센다** — 같은 키의 같은 실행은 한 번이다."""

    week_start: date
    horizon_days: int
    run_slot: str
    llm_model: str
    prompt_version: str
    mean_tool_calls: float | None = None
    mean_tool_result_chars: float | None = None
    run_samples: int = 0


class NarrativeGrade(QualityRow):
    """판정 집계 한 행. 키는 `(week_start, horizon_days, llm_model, prompt_version)`."""

    week_start: date
    horizon_days: int
    llm_model: str
    prompt_version: str
    supported: int = 0
    contradicted: int = 0
    unresolved: int = 0
    verdict_samples: int = 0


class QualityRows(QualityRow):
    """한 응답이 필요로 하는 집계 셋."""

    forecast_grades: tuple[ForecastGrade, ...] = ()
    forecast_runs: tuple[ForecastRunStat, ...] = ()
    narrative: tuple[NarrativeGrade, ...] = ()


def _week_start() -> Any:
    """주의 시작(월요일). `run_date`가 이미 KST 세션 날짜라 여기서 시간대를 다시 바꾸지 않는다."""
    return cast(func.date_trunc("week", Thesis.run_date), Date).label("week_start")


class QualityReadRepository:
    """주 단위 품질 집계를 읽는다. 쓰기 경로는 없다."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    # --- 조회문 (테스트가 컴파일해서 본다) ---------------------------------------

    @staticmethod
    def _scoped(
        statement: Select[Any],
        *,
        run_date_from: date,
        run_date_to: date,
        run_slots: Sequence[str],
        subject_codes: Sequence[str],
        horizon_days: Sequence[int],
    ) -> Select[Any]:
        """세 조회가 같은 필터를 쓴다. 한 자리에 두지 않으면 표 둘이 다른 모집단을 센다."""
        statement = statement.where(
            Thesis.run_date >= run_date_from,
            Thesis.run_date <= run_date_to,
        )
        if run_slots:
            statement = statement.where(Thesis.run_slot.in_(run_slots))
        if subject_codes:
            statement = statement.where(Thesis.subject_code.in_(subject_codes))
        if horizon_days:
            statement = statement.where(ThesisOutcome.horizon_days.in_(horizon_days))
        return statement

    @classmethod
    def forecast_grade_statement(cls, **scope: Any) -> Select[Any]:
        """채점 집계. **`count()`는 컬럼을 세므로 NULL 지평이 표본에 안 들어간다.**"""
        week = _week_start()
        statement = (
            select(
                week,
                ThesisOutcome.horizon_days,
                Thesis.run_slot,
                Thesis.llm_model,
                Thesis.prompt_version,
                func.avg(ThesisOutcome.brier_score).label("mean_brier"),
                func.count(ThesisOutcome.brier_score).label("brier_samples"),
                func.avg(ThesisOutcome.return_error_pct).label("mean_return_error_pct"),
                func.avg(func.abs(ThesisOutcome.return_error_pct)).label("mae_return_pct"),
                func.count(ThesisOutcome.return_error_pct).label("return_samples"),
            )
            .select_from(Thesis)
            .join(ThesisOutcome, ThesisOutcome.thesis_id == Thesis.id)
        )
        return (
            cls._scoped(statement, **scope)
            .group_by(week, ThesisOutcome.horizon_days, Thesis.run_slot, Thesis.llm_model, Thesis.prompt_version)
            .order_by(week, ThesisOutcome.horizon_days, Thesis.run_slot)
        )

    @classmethod
    def forecast_run_statement(cls, **scope: Any) -> Select[Any]:
        """툴 사용 집계. **`DISTINCT` 뒤에 평균을 낸다.**

        실행 하나가 여러 추론을 만들므로 조인 결과에서 그대로 평균을 내면 추론이 많은
        실행이 그 수만큼 가중된다. 키와 실행 id로 먼저 눌러서 실행 하나가 한 행이 되게 한다.
        """
        week = _week_start()
        inner = (
            select(
                week,
                ThesisOutcome.horizon_days.label("horizon_days"),
                Thesis.run_slot.label("run_slot"),
                Thesis.llm_model.label("llm_model"),
                Thesis.prompt_version.label("prompt_version"),
                ThesisLlmRun.id.label("run_id"),
                ThesisLlmRun.tool_calls.label("tool_calls"),
                ThesisLlmRun.tool_result_chars.label("tool_result_chars"),
            )
            .select_from(Thesis)
            .join(ThesisOutcome, ThesisOutcome.thesis_id == Thesis.id)
            .join(ThesisLlmRun, ThesisLlmRun.id == Thesis.llm_run_id)
            .where(ThesisLlmRun.status == LlmRunStatus.SUCCEEDED)
        )
        deduped = cls._scoped(inner, **scope).distinct().subquery()
        return (
            select(
                deduped.c.week_start,
                deduped.c.horizon_days,
                deduped.c.run_slot,
                deduped.c.llm_model,
                deduped.c.prompt_version,
                func.avg(deduped.c.tool_calls).label("mean_tool_calls"),
                func.avg(deduped.c.tool_result_chars).label("mean_tool_result_chars"),
                func.count(deduped.c.run_id).label("run_samples"),
            )
            .group_by(
                deduped.c.week_start,
                deduped.c.horizon_days,
                deduped.c.run_slot,
                deduped.c.llm_model,
                deduped.c.prompt_version,
            )
            .order_by(deduped.c.week_start, deduped.c.horizon_days, deduped.c.run_slot)
        )

    @classmethod
    def narrative_statement(cls, **scope: Any) -> Select[Any]:
        """판정 집계. **키에 `run_slot`이 없다** — 해설은 슬롯이 아니라 지평으로 갈린다."""
        week = _week_start()

        def tally(value: ThesisVerdict) -> Any:
            return func.count(ThesisOutcome.verdict).filter(ThesisOutcome.verdict == value).label(value.value)

        statement = (
            select(
                week,
                ThesisOutcome.horizon_days,
                ThesisOutcome.llm_model,
                ThesisOutcome.prompt_version,
                tally(ThesisVerdict.SUPPORTED),
                tally(ThesisVerdict.CONTRADICTED),
                tally(ThesisVerdict.UNRESOLVED),
                func.count(ThesisOutcome.verdict).label("verdict_samples"),
            )
            .select_from(Thesis)
            .join(ThesisOutcome, ThesisOutcome.thesis_id == Thesis.id)
            .where(ThesisOutcome.verdict.is_not(None))
        )
        return (
            cls._scoped(statement, **scope)
            .group_by(week, ThesisOutcome.horizon_days, ThesisOutcome.llm_model, ThesisOutcome.prompt_version)
            .order_by(week, ThesisOutcome.horizon_days)
        )

    # --- 공개 조회 -----------------------------------------------------------

    async def quality_rows(
        self,
        *,
        run_date_from: date,
        run_date_to: date,
        run_slots: Sequence[str] = (),
        subject_codes: Sequence[str] = (),
        horizon_days: Sequence[int] = (),
    ) -> QualityRows:
        """집계 셋. 왕복 셋이고 한 세션 안이다."""
        scope = {
            "run_date_from": run_date_from,
            "run_date_to": run_date_to,
            "run_slots": run_slots,
            "subject_codes": subject_codes,
            "horizon_days": horizon_days,
        }
        async with self._session_factory() as session:
            grades = await self._rows(session, self.forecast_grade_statement(**scope), ForecastGrade)
            runs = await self._rows(session, self.forecast_run_statement(**scope), ForecastRunStat)
            narrative = await self._rows(session, self.narrative_statement(**scope), NarrativeGrade)
        return QualityRows(
            forecast_grades=tuple(grades),
            forecast_runs=tuple(runs),
            narrative=tuple(narrative),
        )

    @staticmethod
    async def _rows(session: AsyncSession, statement: Select[Any], model: type[QualityRow]) -> list[Any]:
        """집계 행을 모델로. 라벨 이름이 곧 필드 이름이라 대조가 여기서 한 번에 된다."""
        return [model.model_validate(row._mapping, from_attributes=True) for row in await session.execute(statement)]
