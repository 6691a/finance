"""전망의 매핑. **리포지토리를 생성자로 받는다.**

**URL을 서비스가 만든다.** 프런트가 문자열을 조립하면 경로 규칙이 두 트리에 생긴다.

**집계의 나눗셈이 여기 있다.** 리포지토리는 합과 건수만 준다 — 슬롯 합계를 만들 때
평균의 평균이 되지 않게 하려면 비율을 마지막에 한 번만 계산해야 한다.
"""

from collections.abc import Sequence
from datetime import date

from apps.api.repository import (
    DEFAULT_LIMIT,
    FORECAST_SLOTS,
    AccuracyRow,
    ForecastListRows,
    ForecastReadRepository,
)
from apps.api.schemas import (
    ForecastAccuracy,
    ForecastAccuracyRow,
    ForecastDetail,
    ForecastItem,
    ForecastList,
    ForecastReason,
)
from apps.api.service.common import number
from apps.models.analysis import KospiForecast

# 슬롯 합계 행의 이름. 슬롯 값과 겹치지 않아야 한다.
ALL_SLOTS = "all"


def forecast_url(run_date: date, slot: str) -> str:
    return f"/api/forecasts/{run_date.isoformat()}/{slot}"


def run_url(llm_run_id: int | None) -> str | None:
    return None if llm_run_id is None else f"/api/llm-runs/{llm_run_id}"


def reason_of(raw: object) -> ForecastReason:
    """저장된 이유 하나를 응답 모양으로.

    **모르는 칸은 버린다.** `reasons`는 JSONB라 프롬프트 판이 오르면 칸이 늘 수 있는데,
    응답 계약이 그것을 그대로 흘리면 화면이 무엇을 그릴지 모른다.
    """
    row = raw if isinstance(raw, dict) else {}
    return ForecastReason(
        factor=row.get("factor"),
        memory_id=row.get("memory_id"),
        slot_ref=row.get("slot_ref"),
        direction=row.get("direction"),
        statement=str(row.get("statement") or ""),
    )


def item_of(row: KospiForecast, reason_count: int = 0) -> ForecastItem:
    return ForecastItem(
        run_date=row.run_date,
        slot=row.slot.value,
        as_of_at=row.as_of_at,
        base_price=number(row.base_price) or 0.0,
        base_at=row.base_at,
        so_far_pct=number(row.so_far_pct),
        direction=row.direction.value,
        expected_change_pct=number(row.expected_change_pct) or 0.0,
        band_pct=number(row.band_pct) or 0.0,
        reason_count=reason_count,
        weak=row.weak,
        rejected_reasons=row.rejected_reasons,
        actual_change_pct=number(row.actual_change_pct),
        hit=row.hit,
        within_band=row.within_band,
        graded_at=row.graded_at,
        prompt_version=row.prompt_version,
        llm_model=row.llm_model,
        llm_run_id=row.llm_run_id,
        llm_run_url=run_url(row.llm_run_id),
        url=forecast_url(row.run_date, row.slot.value),
    )


def build_list(rows: ForecastListRows, *, limit: int, offset: int) -> ForecastList:
    return ForecastList(
        items=tuple(
            item_of(row, rows.reason_counts.get(row.id, 0)) for row in rows.forecasts
        ),
        limit=limit,
        offset=offset,
        has_more=rows.has_more,
    )


def build_detail(row: KospiForecast) -> ForecastDetail:
    reasons = tuple(reason_of(raw) for raw in (row.reasons or ()))
    return ForecastDetail(
        **item_of(row, len(reasons)).model_dump(),
        reasons=reasons,
        input_state=dict(row.input_state or {}),
        dag_run_id=row.dag_run_id,
    )


def accuracy_row_of(row: AccuracyRow) -> ForecastAccuracyRow:
    """**채점 0건이면 비율이 null이다.** 0.0으로 두면 "다 틀렸다"로 읽힌다."""
    graded = row.graded
    return ForecastAccuracyRow(
        slot=row.slot,
        graded=graded,
        hits=row.hits,
        within_band=row.within_band,
        hit_rate=None if graded == 0 else round(row.hits / graded, 4),
        band_rate=None if graded == 0 else round(row.within_band / graded, 4),
        mean_abs_error=(
            None if graded == 0 or row.error_sum is None else round(row.error_sum / graded, 4)
        ),
        pending=row.pending,
    )


def build_accuracy(
    rows: Sequence[AccuracyRow], *, since: date, until: date
) -> ForecastAccuracy:
    """슬롯 셋을 하루 순서로 세우고 마지막에 합계를 붙인다.

    **행이 없는 슬롯도 0으로 싣는다.** 빠뜨리면 화면이 "그 슬롯은 안 돈다"와 "아직
    채점이 없다"를 구별하지 못한다.
    """
    found = {row.slot: row for row in rows}
    ordered = [found.get(slot, AccuracyRow(slot=slot)) for slot in FORECAST_SLOTS]
    total = AccuracyRow(
        slot=ALL_SLOTS,
        graded=sum(row.graded for row in ordered),
        hits=sum(row.hits for row in ordered),
        within_band=sum(row.within_band for row in ordered),
        error_sum=sum(row.error_sum or 0.0 for row in ordered) or None,
        pending=sum(row.pending for row in ordered),
    )
    return ForecastAccuracy(
        since=since,
        until=until,
        rows=tuple(accuracy_row_of(row) for row in [*ordered, total]),
    )


class ForecastReadService:
    """전망을 읽어 응답 계약으로 준다."""

    def __init__(self, repository: ForecastReadRepository) -> None:
        self._repository = repository

    async def list_page(
        self,
        *,
        run_from: date,
        run_to: date,
        slots: Sequence[str] = (),
        graded: bool | None = None,
        hit: bool | None = None,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> ForecastList:
        rows = await self._repository.list_rows(
            run_from=run_from,
            run_to=run_to,
            slots=slots,
            graded=graded,
            hit=hit,
            limit=limit,
            offset=offset,
        )
        return build_list(rows, limit=limit, offset=offset)

    async def detail(self, run_date: date, slot: str) -> ForecastDetail | None:
        row = await self._repository.detail_row(run_date, slot)
        return None if row is None else build_detail(row)

    async def accuracy(self, *, run_from: date, run_to: date) -> ForecastAccuracy:
        rows = await self._repository.accuracy_rows(run_from=run_from, run_to=run_to)
        return build_accuracy(rows, since=run_from, until=run_to)
