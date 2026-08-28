"""지표 시계열 조회.

**조회는 언제나 `(provider, series_id)` 둘을 함께 건다.** `series_id`는 제공처 안에서만
고유해서 그 하나로 거는 쿼리는 제공처가 늘어나면 조용히 틀린다 — 이 저장소가 여섯 곳에서
받고 있고 미국 국채와 독일 국채의 만기 표기가 겹칠 수 있다.

**곡선 조회는 `kind = government_bond`와 `maturity_months IS NOT NULL`을 고정으로 건다.**
단기 자금시장 금리(CD 91일)와 만기 개념이 없는 물가지수가 곡선에 섞이면, 단위가 다른 값이
한 축에 올라가 화면이 조용히 거짓말을 한다.
"""

from collections.abc import Sequence
from datetime import date
from typing import Any

from pydantic import Field
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.repository.common import DEFAULT_LIMIT, RowBundle, page_slice
from apps.models.market import IndicatorObservation
from apps.models.reference import IndicatorSeries, SeriesKind

# 곡선에 태울 종류. 하나뿐이지만 상수로 두는 이유는 조회문 셋이 같은 값을 봐야 해서다.
CURVE_KIND = SeriesKind.GOVERNMENT_BOND


class IndicatorSeriesRows(RowBundle):
    series: tuple[IndicatorSeries, ...] = ()
    has_more: bool = False
    # (provider, series_id) → (행 수, 처음, 마지막, 단위)
    coverage: dict[tuple[str, str], tuple[int, date, date, str | None]] = Field(default_factory=dict)


class CurveRows(RowBundle):
    as_of: date
    series: tuple[IndicatorSeries, ...] = ()
    # (provider, series_id) → (관측일, 값, 단위)
    latest: dict[tuple[str, str], tuple[date, float, str | None]] = Field(default_factory=dict)


class IndicatorReadRepository:
    """지표 시계열을 읽는다. 쓰기 경로는 없다."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    # --- 조회문 (테스트가 컴파일해서 본다) ---------------------------------------

    @staticmethod
    def series_statement(kinds: Sequence[str] = (), countries: Sequence[str] = ()) -> Select[Any]:
        """마스터 목록. **만기 순으로 정렬한다** — 곡선을 그릴 때 그 순서가 곧 x축이다."""
        statement = select(IndicatorSeries)
        if kinds:
            statement = statement.where(IndicatorSeries.kind.in_(kinds))
        if countries:
            statement = statement.where(IndicatorSeries.country.in_(countries))
        return statement.order_by(
            IndicatorSeries.kind,
            IndicatorSeries.country,
            IndicatorSeries.maturity_months.nulls_last(),
            IndicatorSeries.series_id,
        )

    @staticmethod
    def coverage_statement() -> Select[Any]:
        """계열별 행 수와 구간. 단위도 함께 준다 — 마스터가 아니라 관측값이 갖는 칸이다."""
        return select(
            IndicatorObservation.provider,
            IndicatorObservation.series_id,
            func.count().label("rows"),
            func.min(IndicatorObservation.observation_date).label("oldest"),
            func.max(IndicatorObservation.observation_date).label("newest"),
            func.max(IndicatorObservation.unit).label("unit"),
        ).group_by(IndicatorObservation.provider, IndicatorObservation.series_id)

    @staticmethod
    def observation_statement(
        *, provider: str, series_id: str, start: date, end: date, limit: int
    ) -> Select[Any]:
        """관측값. **`provider`와 `series_id`를 함께 건다.**"""
        return (
            select(
                IndicatorObservation.observation_date,
                IndicatorObservation.value,
                IndicatorObservation.unit,
            )
            .where(
                IndicatorObservation.provider == provider,
                IndicatorObservation.series_id == series_id,
                IndicatorObservation.observation_date >= start,
                IndicatorObservation.observation_date <= end,
            )
            .order_by(IndicatorObservation.observation_date)
            .limit(limit + 1)
        )

    @staticmethod
    def curve_series_statement(countries: Sequence[str] = ()) -> Select[Any]:
        """곡선에 태울 계열. **국채이고 만기가 있는 것만이다.**"""
        statement = select(IndicatorSeries).where(
            IndicatorSeries.kind == CURVE_KIND,
            IndicatorSeries.maturity_months.is_not(None),
        )
        if countries:
            statement = statement.where(IndicatorSeries.country.in_(countries))
        return statement.order_by(IndicatorSeries.country, IndicatorSeries.maturity_months)

    @staticmethod
    def curve_value_statement(*, as_of: date) -> Select[Any]:
        """각 계열의 `as_of` 이전 마지막 값.

        **나라마다 마지막 고시일이 다르다.** 같은 날짜를 요구하면 일본 휴장일에 일본 곡선이
        통째로 비므로, 계열마다 그 날짜까지의 마지막 값을 집는다. 그래서 응답의 점마다
        관측일이 따로 붙는다 — 한 곡선 안에서도 날짜가 갈릴 수 있고 그것이 사실이다.
        """
        ranked = (
            select(
                IndicatorObservation.provider,
                IndicatorObservation.series_id,
                IndicatorObservation.observation_date,
                IndicatorObservation.value,
                IndicatorObservation.unit,
                func.row_number()
                .over(
                    partition_by=(IndicatorObservation.provider, IndicatorObservation.series_id),
                    order_by=IndicatorObservation.observation_date.desc(),
                )
                .label("rank"),
            )
            .join(
                IndicatorSeries,
                (IndicatorSeries.provider == IndicatorObservation.provider)
                & (IndicatorSeries.series_id == IndicatorObservation.series_id),
            )
            .where(
                IndicatorObservation.observation_date <= as_of,
                IndicatorSeries.kind == CURVE_KIND,
                IndicatorSeries.maturity_months.is_not(None),
            )
            .subquery()
        )
        return select(
            ranked.c.provider,
            ranked.c.series_id,
            ranked.c.observation_date,
            ranked.c.value,
            ranked.c.unit,
        ).where(ranked.c.rank == 1)

    # --- 공개 조회 -----------------------------------------------------------

    async def series_rows(
        self,
        kinds: Sequence[str] = (),
        countries: Sequence[str] = (),
        *,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> IndicatorSeriesRows:
        """마스터에 실제 쌓인 것을 붙인다. 왕복 둘이고 한 세션 안이다."""
        async with self._session_factory() as session:
            found = list(
                (
                    await session.execute(
                        self.series_statement(kinds, countries).limit(limit + 1).offset(offset)
                    )
                ).scalars()
            )
            series, has_more = page_slice(found, limit)
            coverage = {
                (row.provider, row.series_id): (row.rows, row.oldest, row.newest, row.unit)
                for row in await session.execute(self.coverage_statement())
            }
        return IndicatorSeriesRows(series=series, coverage=coverage, has_more=has_more)

    async def series(self, provider: str, series_id: str) -> IndicatorSeries | None:
        async with self._session_factory() as session:
            return (
                await session.execute(
                    select(IndicatorSeries).where(
                        IndicatorSeries.provider == provider,
                        IndicatorSeries.series_id == series_id,
                    )
                )
            ).scalar_one_or_none()

    async def observation_rows(
        self, *, provider: str, series_id: str, start: date, end: date, limit: int
    ) -> tuple[tuple[Any, ...], ...]:
        async with self._session_factory() as session:
            rows = await session.execute(
                self.observation_statement(
                    provider=provider, series_id=series_id, start=start, end=end, limit=limit
                )
            )
            return tuple(tuple(row) for row in rows)

    async def curve_rows(self, *, as_of: date, countries: Sequence[str] = ()) -> CurveRows:
        """곡선에 필요한 행 전부. 왕복 둘이고 한 세션 안이다."""
        async with self._session_factory() as session:
            series = list((await session.execute(self.curve_series_statement(countries))).scalars())
            latest = {
                (row.provider, row.series_id): (row.observation_date, float(row.value), row.unit)
                for row in await session.execute(self.curve_value_statement(as_of=as_of))
            }
        return CurveRows(as_of=as_of, series=tuple(series), latest=latest)
