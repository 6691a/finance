"""품질 집계의 매핑. **나눗셈이 여기 있다.**

리포지토리는 합과 건수만 준다 — 비율을 마지막에 한 번만 계산해야 주를 합칠 때 평균의
평균이 되지 않는다.

**표본이 0이면 비율은 `None`이다.** 0.0으로 두면 "다 틀렸다"로 읽힌다.
"""

from collections.abc import Sequence
from datetime import date

from apps.api.repository import (
    ForecastGrade,
    QualityReadRepository,
    QualityRows,
    ReviewStat,
)
from apps.api.schemas import ForecastQualityRow, QualityResponse, ReviewQualityRow
from apps.api.schemas.quality import COIN_FLIP_HIT_RATE


def ratio(numerator: float | None, denominator: int, *, digits: int = 4) -> float | None:
    """표본이 없으면 `None`. **0.0을 돌려주지 않는다.**"""
    if denominator == 0 or numerator is None:
        return None
    return round(numerator / denominator, digits)


def forecast_row_of(row: ForecastGrade) -> ForecastQualityRow:
    hit_rate = ratio(row.hits, row.graded)
    return ForecastQualityRow(
        week_start=row.week_start,
        slot=row.slot,
        llm_model=row.llm_model,
        prompt_version=row.prompt_version,
        graded=row.graded,
        pending=row.pending,
        hits=row.hits,
        hit_rate=hit_rate,
        beats_coin_flip=None if hit_rate is None else hit_rate > COIN_FLIP_HIT_RATE,
        within_band=row.within_band,
        band_rate=ratio(row.within_band, row.graded),
        mean_abs_error=ratio(row.abs_error_sum, row.graded),
        mean_band_pct=ratio(row.band_sum, row.graded),
        mean_expected_pct=ratio(row.expected_sum, row.graded),
        weak=row.weak,
        rejected_reasons=row.rejected_reasons,
    )


def review_row_of(row: ReviewStat) -> ReviewQualityRow:
    return ReviewQualityRow(
        week_start=row.week_start,
        llm_model=row.llm_model,
        prompt_version=row.prompt_version,
        runs=row.runs,
        observations_written=row.observations_written,
        mean_observations=ratio(row.observations_written, row.runs, digits=2),
        memories_written=row.memories_written,
        memories_rejected=row.memories_rejected,
        memories_dropped=row.memories_dropped,
        memories_expired=row.memories_expired,
        rejected=row.rejected,
        mean_tool_calls=ratio(row.tool_calls_sum, row.runs, digits=2),
        truncated=row.truncated,
    )


def build_summary(rows: QualityRows) -> QualityResponse:
    return QualityResponse(
        forecast=tuple(forecast_row_of(row) for row in rows.forecast),
        review=tuple(review_row_of(row) for row in rows.review),
    )


class QualityReadService:
    """품질 집계를 읽어 응답 계약으로 준다."""

    def __init__(self, repository: QualityReadRepository) -> None:
        self._repository = repository

    async def summary(
        self, *, run_date_from: date, run_date_to: date, slots: Sequence[str] = ()
    ) -> QualityResponse:
        rows = await self._repository.summary_rows(
            run_date_from=run_date_from, run_date_to=run_date_to, slots=slots
        )
        return build_summary(rows)
