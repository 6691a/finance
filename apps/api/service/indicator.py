"""지표 시계열의 매핑.

**곡선의 단위는 나라마다 하나여야 한다.** 한 나라의 계열이 서로 다른 단위를 갖고 있으면
그건 수집 쪽 결함이라 여기서 감추지 않고 첫 값을 그대로 낸다 — 화면이 단위를 표시하므로
어긋난 순간 눈에 띈다.
"""

from collections.abc import Sequence
from datetime import date
from typing import Any

from apps.api.repository import (
    DEFAULT_LIMIT,
    MAX_POINTS,
    CurveRows,
    IndicatorReadRepository,
    IndicatorSeriesRows,
)
from apps.api.schemas import (
    CurveCountry,
    CurvePoint,
    CurveResponse,
    IndicatorPoints,
    IndicatorSeriesItem,
    IndicatorSeriesList,
)
from apps.api.service.common import number
from apps.models.reference import IndicatorSeries


class UnknownSeries(Exception):
    """마스터에 없는 `(provider, series_id)`. 라우트가 404로 바꾼다."""


def series_item(
    series: IndicatorSeries, coverage: tuple[int, date, date, str | None] | None
) -> IndicatorSeriesItem:
    return IndicatorSeriesItem(
        provider=series.provider,
        series_id=series.series_id,
        kind=series.kind.value,
        country=series.country,
        country_name=series.country_name,
        label=series.label,
        maturity_months=series.maturity_months,
        unit=coverage[3] if coverage else None,
        rows=coverage[0] if coverage else 0,
        observed_from=coverage[1] if coverage else None,
        observed_to=coverage[2] if coverage else None,
    )


def build_series(rows: IndicatorSeriesRows, *, limit: int, offset: int) -> IndicatorSeriesList:
    return IndicatorSeriesList(
        limit=limit,
        offset=offset,
        has_more=rows.has_more,
        items=tuple(
            series_item(series, rows.coverage.get((series.provider, series.series_id)))
            for series in rows.series
        )
    )


def build_points(rows: Sequence[tuple[Any, ...]], series: IndicatorSeries) -> IndicatorPoints:
    return IndicatorPoints(
        provider=series.provider,
        series_id=series.series_id,
        kind=series.kind.value,
        label=series.label,
        unit=rows[0][2] if rows else None,
        points=len(rows),
        dates=tuple(row[0] for row in rows),
        values=tuple(number(row[1]) or 0.0 for row in rows),
    )


def build_curve(rows: CurveRows) -> CurveResponse:
    """나라별로 묶어 만기 순으로 편다.

    **값이 없는 계열은 점을 만들지 않는다.** 0으로 채우면 곡선이 바닥으로 꺾이고, 그것을
    "그 만기 금리가 0이다"로 읽는다.
    """
    grouped: dict[str, list[CurvePoint]] = {}
    meta: dict[str, tuple[str, str, str | None]] = {}
    for series in rows.series:
        found = rows.latest.get((series.provider, series.series_id))
        if found is None:
            continue
        observed, value, unit = found
        grouped.setdefault(series.country, []).append(
            CurvePoint(
                series_id=series.series_id,
                maturity_months=series.maturity_months or 0,
                label=series.label,
                observation_date=observed,
                value=value,
            )
        )
        meta.setdefault(series.country, (series.provider, series.country_name, unit))

    return CurveResponse(
        as_of=rows.as_of,
        countries=tuple(
            CurveCountry(
                provider=meta[country][0],
                country=country,
                country_name=meta[country][1],
                unit=meta[country][2],
                points=tuple(sorted(points, key=lambda point: point.maturity_months)),
            )
            for country, points in sorted(grouped.items())
        ),
    )


class IndicatorReadService:
    """지표 시계열을 읽어 응답 계약으로 준다."""

    def __init__(self, repository: IndicatorReadRepository) -> None:
        self._repository = repository

    async def series(
        self,
        kinds: Sequence[str] = (),
        countries: Sequence[str] = (),
        *,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> IndicatorSeriesList:
        rows = await self._repository.series_rows(kinds, countries, limit=limit, offset=offset)
        return build_series(rows, limit=limit, offset=offset)

    async def points(
        self, *, provider: str, series_id: str, start: date, end: date
    ) -> IndicatorPoints:
        master = await self._repository.series(provider, series_id)
        if master is None:
            raise UnknownSeries(f"{provider}:{series_id}")
        rows = await self._repository.observation_rows(
            provider=provider, series_id=series_id, start=start, end=end, limit=MAX_POINTS
        )
        return build_points(rows[:MAX_POINTS], master)

    async def curve(self, *, as_of: date, countries: Sequence[str] = ()) -> CurveResponse:
        return build_curve(await self._repository.curve_rows(as_of=as_of, countries=countries))


__all__ = [
    "IndicatorReadService",
    "UnknownSeries",
    "build_curve",
    "build_points",
    "build_series",
    "series_item",
]
