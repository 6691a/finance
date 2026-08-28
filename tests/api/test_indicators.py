"""지표 시계열 라우트.

주제 셋: ① `kind`가 다른 계열이 한 축에 섞이지 않는다 ② 조회가 `provider`를 함께 건다
③ 곡선에서 만기 없는 계열이 빠진다.
"""

from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest
from dependency_injector import providers

from apps.api.app import create_app
from apps.api.repository import CurveRows, IndicatorSeriesRows
from apps.api.repository.indicator import IndicatorReadRepository
from apps.models.reference import IndicatorSeries, SeriesKind
from tests.api.conftest import container


def series_row(
    series_id: str = "DGS10",
    provider: str = "fred",
    kind: SeriesKind = SeriesKind.GOVERNMENT_BOND,
    country: str = "US",
    maturity: int | None = 120,
) -> IndicatorSeries:
    return IndicatorSeries(
        provider=provider,
        series_id=series_id,
        country=country,
        country_name="미국",
        maturity_months=maturity,
        kind=kind,
        label=f"{country} {series_id}",
    )


class FakeRepository:
    def __init__(self, **rows: Any) -> None:
        self.rows = rows
        self.calls: list[dict[str, Any]] = []

    async def series_rows(self, kinds=(), countries=(), **kwargs: Any) -> IndicatorSeriesRows:
        self.calls.append({"kinds": list(kinds), "countries": list(countries), **kwargs})
        return IndicatorSeriesRows(
            series=tuple(self.rows.get("series", [])),
            coverage=self.rows.get("coverage", {}),
        )

    async def series(self, provider: str, series_id: str) -> IndicatorSeries | None:
        return next(
            (
                row
                for row in self.rows.get("series", [])
                if row.provider == provider and row.series_id == series_id
            ),
            None,
        )

    async def observation_rows(self, **kwargs: Any) -> tuple[tuple[Any, ...], ...]:
        self.calls.append(kwargs)
        return tuple(self.rows.get("observations", []))

    async def curve_rows(self, *, as_of: date, countries=()) -> CurveRows:
        self.calls.append({"as_of": as_of, "countries": list(countries)})
        return CurveRows(
            as_of=as_of,
            series=tuple(self.rows.get("series", [])),
            latest=self.rows.get("latest", {}),
        )


def app_with(fake: FakeRepository):
    built = container()
    built.indicator_repository.override(providers.Object(fake))
    return create_app(built)


def client(**rows: Any) -> httpx.AsyncClient:
    app = app_with(FakeRepository(**rows))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_the_series_list_carries_kind_and_unit():
    """단위는 마스터가 아니라 관측값이 갖는다. 화면이 축 라벨에 그것을 쓴다."""
    rows = {
        "series": [series_row()],
        "coverage": {("fred", "DGS10"): (78, date(2026, 2, 1), date(2026, 8, 25), "Percent")},
    }
    async with client(**rows) as http:
        item = (await http.get("/api/indicators/series")).json()["items"][0]

    assert item["kind"] == "government_bond"
    assert item["unit"] == "Percent"
    assert item["rows"] == 78
    assert item["maturity_months"] == 120


@pytest.mark.asyncio
async def test_a_series_without_a_maturity_stays_null_not_zero():
    """0으로 채우면 만기별 비교가 그 시계열을 "0개월물"로 그린다."""
    rows = {"series": [series_row("CPI_M", kind=SeriesKind.PRICE_INDEX, maturity=None)]}
    async with client(**rows) as http:
        item = (await http.get("/api/indicators/series")).json()["items"][0]

    assert item["maturity_months"] is None


@pytest.mark.asyncio
async def test_the_kind_filter_reaches_the_repository():
    """`kind`를 안 걸면 단위가 다른 값이 한 축에 섞인다."""
    fake = FakeRepository(series=[])
    app = app_with(fake)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        await http.get("/api/indicators/series", params={"kind": "government_bond", "country": "US"})

    assert fake.calls[0]["kinds"] == ["government_bond"]
    assert fake.calls[0]["countries"] == ["US"]


@pytest.mark.asyncio
async def test_observations_are_queried_with_the_provider_too():
    """`series_id`는 제공처 안에서만 고유하다. 하나로 걸면 제공처가 늘 때 조용히 틀린다."""
    fake = FakeRepository(series=[series_row()], observations=[(date(2026, 8, 25), Decimal("4.21"), "Percent")])
    app = app_with(fake)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        payload = (
            await http.get(
                "/api/indicators/observations", params={"provider": "fred", "series_id": "DGS10"}
            )
        ).json()

    call = next(entry for entry in fake.calls if "provider" in entry)
    assert (call["provider"], call["series_id"]) == ("fred", "DGS10")
    assert payload["values"] == [4.21]
    assert payload["dates"] == ["2026-08-25"]
    assert payload["kind"] == "government_bond"


@pytest.mark.asyncio
async def test_an_unknown_series_is_a_404():
    async with client(series=[]) as http:
        reply = await http.get(
            "/api/indicators/observations", params={"provider": "fred", "series_id": "NOPE"}
        )

    assert reply.status_code == 404


@pytest.mark.asyncio
async def test_the_curve_groups_by_country_and_sorts_by_maturity():
    rows = {
        "series": [
            series_row("DGS10", maturity=120),
            series_row("DGS2", maturity=24),
            series_row("JGB10Y", provider="mof", country="JP", maturity=120),
        ],
        "latest": {
            ("fred", "DGS10"): (date(2026, 8, 25), 4.21, "Percent"),
            ("fred", "DGS2"): (date(2026, 8, 25), 3.80, "Percent"),
            ("mof", "JGB10Y"): (date(2026, 8, 22), 1.62, "Percent"),
        },
    }
    async with client(**rows) as http:
        payload = (await http.get("/api/indicators/curve")).json()

    countries = {entry["country"]: entry for entry in payload["countries"]}
    assert set(countries) == {"US", "JP"}
    assert [point["maturity_months"] for point in countries["US"]["points"]] == [24, 120]
    # 나라마다 마지막 고시일이 다르다. 점마다 관측일이 붙는 이유다.
    assert countries["JP"]["points"][0]["observation_date"] == "2026-08-22"


@pytest.mark.asyncio
async def test_a_series_with_no_value_makes_no_point():
    """0으로 채우면 곡선이 바닥으로 꺾이고 "그 만기 금리가 0"으로 읽힌다."""
    rows = {
        "series": [series_row("DGS10", maturity=120), series_row("DGS30", maturity=360)],
        "latest": {("fred", "DGS10"): (date(2026, 8, 25), 4.21, "Percent")},
    }
    async with client(**rows) as http:
        payload = (await http.get("/api/indicators/curve")).json()

    assert [point["series_id"] for point in payload["countries"][0]["points"]] == ["DGS10"]


def test_the_curve_query_only_takes_government_bonds_with_a_maturity():
    """단기 자금시장 금리(CD 91일)와 물가지수가 곡선에 섞이면 축 단위가 무너진다."""
    series = str(IndicatorReadRepository.curve_series_statement().compile(compile_kwargs={"literal_binds": True}))
    values = str(
        IndicatorReadRepository.curve_value_statement(as_of=date(2026, 8, 27)).compile(
            compile_kwargs={"literal_binds": True}
        )
    )

    for compiled in (series, values):
        assert "government_bond" in compiled
        assert "maturity_months IS NOT NULL" in compiled


def test_the_observation_query_binds_both_key_columns():
    compiled = str(
        IndicatorReadRepository.observation_statement(
            provider="fred", series_id="DGS10", start=date(2026, 1, 1), end=date(2026, 8, 27), limit=10
        ).compile(compile_kwargs={"literal_binds": True})
    )

    assert "indicator_observation.provider = 'fred'" in compiled
    assert "indicator_observation.series_id = 'DGS10'" in compiled
