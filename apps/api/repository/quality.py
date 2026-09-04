"""품질 집계 조회. **조회 둘을 나눠 읽고 합치는 것은 서비스가 한다.**

한 조회로 합치지 않는 이유는 둘의 단위가 다르기 때문이다 — 전망 채점은 `kospi_forecast`
행 단위, 관찰 통계는 `kospi_llm_run` 실행 단위다. 억지로 한 SELECT에 넣으면 실행 하나가
전망 수만큼 곱해져 평균이 조용히 편향된다.

**행 모양은 여기서 Pydantic 모델로 못 박는다.** SQLAlchemy `Row`를 그대로 서비스로
넘기면 칸 이름 오타가 실행 시점까지 산다.

**평균이 아니라 합과 건수를 가져온다.** 서비스가 마지막에 한 번만 나눈다 — 그래야 주를
합칠 때 평균의 평균이 되지 않는다.
"""

from collections.abc import Sequence
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Date, Select, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.models.analysis import (
    KospiForecast,
    KospiLlmRun,
    KospiLlmRunKind,
    KospiLlmRunStatus,
)


class QualityRow(BaseModel):
    """집계 행의 공통 형태. 만든 뒤 바뀌지 않는다."""

    model_config = ConfigDict(frozen=True)


class ForecastGrade(QualityRow):
    """전망 채점 집계 한 행. 키는 `(week_start, slot, llm_model, prompt_version)`."""

    week_start: date
    slot: str
    llm_model: str
    prompt_version: str
    graded: int = 0
    pending: int = 0
    hits: int = 0
    within_band: int = 0
    abs_error_sum: float | None = None
    band_sum: float | None = None
    expected_sum: float | None = None
    weak: int = 0
    rejected_reasons: int = 0


class ReviewStat(QualityRow):
    """관찰 실행 집계 한 행. 키는 `(week_start, llm_model, prompt_version)`."""

    week_start: date
    llm_model: str
    prompt_version: str
    runs: int = 0
    observations_written: int = 0
    memories_written: int = 0
    memories_rejected: int = 0
    memories_dropped: int = 0
    memories_expired: int = 0
    rejected: int = 0
    tool_calls_sum: int = 0
    truncated: int = 0


class QualityRows(QualityRow):
    """한 응답이 필요로 하는 집계 둘."""

    forecast: tuple[ForecastGrade, ...] = ()
    review: tuple[ReviewStat, ...] = ()


def week_of(column: Any) -> Any:
    """주의 시작(월요일). `run_date`가 이미 KST 세션 날짜라 여기서 시간대를 다시 바꾸지 않는다."""
    return cast(func.date_trunc("week", column), Date).label("week_start")


class QualityReadRepository:
    """주 단위 품질 집계를 읽는다. 쓰기 경로는 없다."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    # --- 조회문 (테스트가 컴파일해서 본다) ---------------------------------------

    @staticmethod
    def forecast_statement(
        *, run_date_from: date, run_date_to: date, slots: Sequence[str] = ()
    ) -> Select[Any]:
        """전망 채점 집계. 채점 전 행도 `pending`으로 센다 — 빼면 표가 "아직 없다"를 못 말한다."""
        week = week_of(KospiForecast.run_date)
        statement = select(
            week,
            KospiForecast.slot,
            KospiForecast.llm_model,
            KospiForecast.prompt_version,
            func.count().filter(KospiForecast.graded_at.is_not(None)).label("graded"),
            func.count().filter(KospiForecast.graded_at.is_(None)).label("pending"),
            func.count().filter(KospiForecast.hit.is_(True)).label("hits"),
            func.count().filter(KospiForecast.within_band.is_(True)).label("within_band"),
            func.sum(
                func.abs(KospiForecast.actual_change_pct - KospiForecast.expected_change_pct)
            ).label("abs_error_sum"),
            func.sum(KospiForecast.band_pct)
            .filter(KospiForecast.graded_at.is_not(None))
            .label("band_sum"),
            func.sum(KospiForecast.expected_change_pct)
            .filter(KospiForecast.graded_at.is_not(None))
            .label("expected_sum"),
            func.count().filter(KospiForecast.weak.is_(True)).label("weak"),
            func.coalesce(func.sum(KospiForecast.rejected_reasons), 0).label("rejected_reasons"),
        ).where(
            KospiForecast.run_date >= run_date_from,
            KospiForecast.run_date <= run_date_to,
        )
        if slots:
            statement = statement.where(KospiForecast.slot.in_(slots))
        return statement.group_by(
            week, KospiForecast.slot, KospiForecast.llm_model, KospiForecast.prompt_version
        ).order_by(week.desc(), KospiForecast.slot)

    @staticmethod
    def review_statement(*, run_date_from: date, run_date_to: date) -> Select[Any]:
        """관찰 실행 집계. **성공한 실행만 센다** — 실패 실행의 null 칸이 평균을 흔든다."""
        week = week_of(KospiLlmRun.run_date)
        return (
            select(
                week,
                KospiLlmRun.llm_model,
                KospiLlmRun.prompt_version,
                func.count().label("runs"),
                func.coalesce(func.sum(KospiLlmRun.observations_written), 0).label(
                    "observations_written"
                ),
                func.coalesce(func.sum(KospiLlmRun.memories_written), 0).label("memories_written"),
                func.coalesce(func.sum(KospiLlmRun.memories_rejected), 0).label("memories_rejected"),
                func.coalesce(func.sum(KospiLlmRun.memories_dropped), 0).label("memories_dropped"),
                func.coalesce(func.sum(KospiLlmRun.memories_expired), 0).label("memories_expired"),
                func.coalesce(func.sum(KospiLlmRun.rejected), 0).label("rejected"),
                func.coalesce(func.sum(KospiLlmRun.tool_calls), 0).label("tool_calls_sum"),
                func.count().filter(KospiLlmRun.truncated.is_(True)).label("truncated"),
            )
            .where(
                KospiLlmRun.run_date >= run_date_from,
                KospiLlmRun.run_date <= run_date_to,
                KospiLlmRun.kind == KospiLlmRunKind.REVIEW,
                KospiLlmRun.status == KospiLlmRunStatus.SUCCEEDED,
            )
            .group_by(week, KospiLlmRun.llm_model, KospiLlmRun.prompt_version)
            .order_by(week.desc())
        )

    # --- 공개 조회 -----------------------------------------------------------

    async def summary_rows(
        self, *, run_date_from: date, run_date_to: date, slots: Sequence[str] = ()
    ) -> QualityRows:
        """집계 둘. 왕복 둘이고 한 세션 안이다."""
        async with self._session_factory() as session:
            forecast = (
                await session.execute(
                    self.forecast_statement(
                        run_date_from=run_date_from, run_date_to=run_date_to, slots=slots
                    )
                )
            ).all()
            review = (
                await session.execute(
                    self.review_statement(run_date_from=run_date_from, run_date_to=run_date_to)
                )
            ).all()
        return QualityRows(
            forecast=tuple(
                ForecastGrade(
                    week_start=row.week_start,
                    slot=str(row.slot),
                    llm_model=row.llm_model,
                    prompt_version=row.prompt_version,
                    graded=row.graded or 0,
                    pending=row.pending or 0,
                    hits=row.hits or 0,
                    within_band=row.within_band or 0,
                    abs_error_sum=None if row.abs_error_sum is None else float(row.abs_error_sum),
                    band_sum=None if row.band_sum is None else float(row.band_sum),
                    expected_sum=None if row.expected_sum is None else float(row.expected_sum),
                    weak=row.weak or 0,
                    rejected_reasons=row.rejected_reasons or 0,
                )
                for row in forecast
            ),
            review=tuple(
                ReviewStat(
                    week_start=row.week_start,
                    llm_model=row.llm_model,
                    prompt_version=row.prompt_version,
                    runs=row.runs or 0,
                    observations_written=row.observations_written or 0,
                    memories_written=row.memories_written or 0,
                    memories_rejected=row.memories_rejected or 0,
                    memories_dropped=row.memories_dropped or 0,
                    memories_expired=row.memories_expired or 0,
                    rejected=row.rejected or 0,
                    tool_calls_sum=row.tool_calls_sum or 0,
                    truncated=row.truncated or 0,
                )
                for row in review
            ),
        )
