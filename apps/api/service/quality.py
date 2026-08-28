"""품질 집계의 매핑. **두 표를 섞지 않는 자리다.**

예측 품질의 키에는 `thesis`의 모델·판만, 해설 품질의 키에는 `thesis_outcome`의 모델·판만
들어간다. 합치는 것은 예측 품질 안의 채점 집계와 툴 집계 둘뿐이고, 그 둘은 같은 키를 갖는다.
"""

from collections.abc import Sequence
from datetime import date

from apps.api.repository import (
    ForecastGrade,
    ForecastRunStat,
    NarrativeGrade,
    QualityReadRepository,
    QualityRows,
)
from apps.api.schemas import (
    UNIFORM_BRIER,
    ForecastQualityRow,
    NarrativeQualityRow,
    QualityResponse,
)

# 채점 집계와 툴 집계를 잇는 키. 둘의 조회가 같은 칸으로 group by 한다.
ForecastKey = tuple[date, int, str, str, str]


def forecast_key(row: ForecastGrade | ForecastRunStat) -> ForecastKey:
    return (row.week_start, row.horizon_days, row.run_slot, row.llm_model, row.prompt_version)


def beats_uniform(mean_brier: float | None, samples: int) -> bool | None:
    """**표본이 없으면 판단하지 않는다.** `False`로 채우면 "안 재 봤다"가 "졌다"로 읽힌다."""
    if mean_brier is None or samples == 0:
        return None
    return mean_brier < UNIFORM_BRIER


def forecast_row_of(grade: ForecastGrade, stat: ForecastRunStat | None) -> ForecastQualityRow:
    return ForecastQualityRow(
        week_start=grade.week_start,
        horizon_days=grade.horizon_days,
        run_slot=grade.run_slot,
        llm_model=grade.llm_model,
        prompt_version=grade.prompt_version,
        mean_brier=grade.mean_brier,
        brier_samples=grade.brier_samples,
        beats_uniform=beats_uniform(grade.mean_brier, grade.brier_samples),
        mean_return_error_pct=grade.mean_return_error_pct,
        mae_return_pct=grade.mae_return_pct,
        return_samples=grade.return_samples,
        mean_tool_calls=None if stat is None else stat.mean_tool_calls,
        mean_tool_result_chars=None if stat is None else stat.mean_tool_result_chars,
        run_samples=0 if stat is None else stat.run_samples,
    )


def narrative_row_of(row: NarrativeGrade) -> NarrativeQualityRow:
    return NarrativeQualityRow(
        week_start=row.week_start,
        horizon_days=row.horizon_days,
        llm_model=row.llm_model,
        prompt_version=row.prompt_version,
        supported=row.supported,
        contradicted=row.contradicted,
        unresolved=row.unresolved,
        verdict_samples=row.verdict_samples,
    )


def build_quality(rows: QualityRows) -> QualityResponse:
    """**채점 행이 기준이다.** 툴 집계에만 있는 키(전부 미채점인 주)는 행을 만들지 않는다 —
    보여 줄 정확도가 없는 행이 표에 생기면 표본 0짜리 줄만 늘어난다."""
    stats = {forecast_key(row): row for row in rows.forecast_runs}
    return QualityResponse(
        forecast=tuple(forecast_row_of(row, stats.get(forecast_key(row))) for row in rows.forecast_grades),
        narrative=tuple(narrative_row_of(row) for row in rows.narrative),
    )


class QualityReadService:
    """주 단위 품질 집계를 읽어 응답 계약으로 준다."""

    def __init__(self, repository: QualityReadRepository) -> None:
        self._repository = repository

    async def summary(
        self,
        *,
        run_date_from: date,
        run_date_to: date,
        run_slots: Sequence[str] = (),
        subject_codes: Sequence[str] = (),
        horizon_days: Sequence[int] = (),
    ) -> QualityResponse:
        rows = await self._repository.quality_rows(
            run_date_from=run_date_from,
            run_date_to=run_date_to,
            run_slots=run_slots,
            subject_codes=subject_codes,
            horizon_days=horizon_days,
        )
        return build_quality(rows)
