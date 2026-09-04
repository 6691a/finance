"""조회 API의 라우팅·매핑·직렬화.

**가짜는 리포지토리 자리에 둔다.** 그러면 진짜 `ForecastReadService`가 그 위에서 돌아
HTTP 경로가 라우팅·매핑·직렬화를 통째로 지나간다 — 없는 것은 세션뿐이다.

끼우는 방법은 `container.forecast_repository.override(...)`다. `dependency_injector`의
문서화된 형태이고, 그것이 먹는다는 것 자체가 wiring이 풀렸다는 증거이기도 하다 —
마커가 안 풀리면 주입 자리에 `Provide` 객체가 그대로 들어온다.
"""

from datetime import date
from typing import Any

import httpx
import pytest
from dependency_injector import providers

from apps.api.app import create_app
from apps.api.repository import AccuracyRow, ForecastListRows
from apps.models.analysis import KospiSlot
from tests.api.conftest import container, forecast_row


class FakeForecasts:
    """행 묶음만 돌려준다. 그 위의 진짜 서비스가 응답 계약을 만든다."""

    def __init__(self, **rows: Any) -> None:
        self.rows = rows
        self.calls: list[dict[str, Any]] = []

    async def list_rows(self, **kwargs: Any) -> ForecastListRows:
        self.calls.append(kwargs)
        found = tuple(self.rows.get("forecasts", []))
        return ForecastListRows(
            forecasts=found,
            has_more=self.rows.get("has_more", False),
            reason_counts={row.id: len(row.reasons or ()) for row in found},
        )

    async def detail_row(self, run_date: date, slot: str) -> Any:
        return next(
            (
                row
                for row in self.rows.get("forecasts", [])
                if row.run_date == run_date and row.slot.value == slot
            ),
            None,
        )

    async def accuracy_rows(self, **kwargs: Any) -> tuple[AccuracyRow, ...]:
        self.calls.append(kwargs)
        return tuple(self.rows.get("accuracy", []))


def client(**rows: Any) -> httpx.AsyncClient:
    built = container()
    # provider override가 먹는다는 것 자체가 wiring이 풀렸다는 증거다.
    built.forecast_repository.override(providers.Object(FakeForecasts(**rows)))
    app = create_app(built)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_the_route_set_is_what_we_meant_to_publish():
    """라우터를 등록 안 한 실수를 잡는다. DAG 구조 테스트와 같은 성격이다."""
    app = create_app(container())

    published = {route.path for route in app.routes if route.path.startswith(("/api", "/healthz"))}

    assert published == {
        "/healthz",
        "/api/forecasts",
        "/api/forecasts/accuracy",
        "/api/forecasts/quality",
        "/api/forecasts/{run_date}/{slot}",
        "/api/relations",
        "/api/relations/graph",
        "/api/relations/memories",
        "/api/relations/memories/{memory_id}",
        "/api/relations/{factor}",
        "/api/llm-runs",
        "/api/llm-runs/{llm_run_id}",
        "/api/llm-runs/{llm_run_id}/tool-calls/{seq}",
        "/api/quotes/symbols",
        "/api/quotes/bars",
        "/api/quotes/daily",
        "/api/indicators/series",
        "/api/indicators/curve",
        "/api/indicators/observations",
        "/api/documents",
        "/api/documents/sources",
        "/api/documents/disclosures",
        "/api/documents/earnings",
        "/api/documents/{document_id}",
        "/api/positioning/investor-flows",
        "/api/positioning/market-movement",
        "/api/positioning/stock-flows",
        "/api/positioning/estimates",
        "/api/positioning/short-sale",
        "/api/positioning/lending",
        "/api/positioning/credit",
        "/api/positioning/funds",
        "/api/positioning/credit-ranking",
        "/api/events/claims",
        "/api/events/outcomes",
        "/api/events/extractions",
        "/api/events/signals",
        "/api/events/analyst-opinions",
        "/api/collection/health",
        "/api/collection/records",
        "/api/collection/instruments",
        "/api/collection/sessions",
    }


@pytest.mark.asyncio
async def test_health_does_not_touch_the_database():
    """DB를 보면 깜빡임이 컨테이너 재시작 루프가 된다. 리포지토리 없이도 200이어야 한다."""
    async with client() as http:
        reply = await http.get("/healthz")

    assert reply.status_code == 200
    assert reply.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_the_service_arrives_by_injection_not_a_lookup():
    """마커가 안 풀리면 주입 자리에 `Provide` 객체가 들어와 조용히 틀린다."""
    async with client(forecasts=[forecast_row()]) as http:
        reply = await http.get("/api/forecasts")

    assert reply.status_code == 200
    assert reply.json()["items"][0]["slot"] == "midday"


@pytest.mark.asyncio
async def test_the_list_carries_the_axis_the_change_is_measured_against():
    """**목록에도 축을 싣는다.** 슬롯 규칙을 몰라도 "이 0.6퍼센트가 무엇 대비인가"가 읽혀야 한다."""
    async with client(forecasts=[forecast_row()]) as http:
        item = (await http.get("/api/forecasts")).json()["items"][0]

    assert item["base_price"] == 6652.75
    assert item["so_far_pct"] == 1.37
    # 축의 시각은 `as_of_at`이 아니라 그 슬롯이 실제로 본 봉의 시각이다.
    assert item["base_at"] != item["as_of_at"]
    assert item["base_at"].endswith("Z")


@pytest.mark.asyncio
async def test_the_list_leaves_the_reasons_and_the_state_to_the_detail():
    """한 건이 수 KB다. 목록에 실으면 한 쪽이 메가 단위가 된다."""
    async with client(forecasts=[forecast_row()]) as http:
        item = (await http.get("/api/forecasts")).json()["items"][0]

    assert "reasons" not in item
    assert "input_state" not in item
    # 대신 건수만 준다 — 화면이 "이유 2건"을 그릴 수 있다.
    assert item["reason_count"] == 2


@pytest.mark.asyncio
async def test_the_pre_open_slot_has_no_so_far_because_the_market_is_shut():
    async with client(forecasts=[forecast_row(slot=KospiSlot.PRE_OPEN)]) as http:
        item = (await http.get("/api/forecasts")).json()["items"][0]

    assert item["so_far_pct"] is None


@pytest.mark.asyncio
async def test_an_ungraded_forecast_says_null_not_zero():
    """0으로 채우면 "아직 안 쟀다"와 "0이었다"가 같아 보인다."""
    async with client(forecasts=[forecast_row()]) as http:
        item = (await http.get("/api/forecasts")).json()["items"][0]

    assert item["actual_change_pct"] is None
    assert item["hit"] is None
    assert item["graded_at"] is None


@pytest.mark.asyncio
async def test_the_detail_is_addressed_by_its_natural_key():
    """`id`를 쓰면 같은 슬롯이 두 주소를 갖는다."""
    async with client(forecasts=[forecast_row()]) as http:
        reply = await http.get("/api/forecasts/2026-09-03/midday")

    payload = reply.json()
    assert reply.status_code == 200
    assert payload["reasons"][0]["factor"] == "FOREIGN_NET_BUY"
    assert payload["reasons"][1]["memory_id"] == 17
    assert payload["input_state"]["run_date"] == "2026-09-03"


@pytest.mark.asyncio
async def test_a_missing_forecast_is_a_404_not_an_empty_body():
    async with client(forecasts=[forecast_row()]) as http:
        reply = await http.get("/api/forecasts/2026-09-02/pre_open")

    assert reply.status_code == 404


@pytest.mark.asyncio
async def test_the_static_accuracy_path_wins_over_the_date_path():
    """`/accuracy`가 `{run_date}/{slot}`보다 먼저 등록돼 있어야 한다."""
    async with client(accuracy=[AccuracyRow(slot="midday", graded=2, hits=1, error_sum=1.0)]) as http:
        reply = await http.get("/api/forecasts/accuracy")

    assert reply.status_code == 200
    rows = {row["slot"]: row for row in reply.json()["rows"]}
    assert rows["midday"]["hit_rate"] == 0.5
    assert rows["midday"]["mean_abs_error"] == 0.5


@pytest.mark.asyncio
async def test_a_slot_with_no_rows_is_still_a_row_with_null_rates():
    """빠뜨리면 "그 슬롯은 안 돈다"와 "아직 채점이 없다"가 같아 보인다."""
    async with client(accuracy=[]) as http:
        rows = {row["slot"]: row for row in (await http.get("/api/forecasts/accuracy")).json()["rows"]}

    assert set(rows) == {"pre_open", "midday", "pre_close", "all"}
    assert rows["pre_open"]["hit_rate"] is None
    assert rows["pre_open"]["graded"] == 0


@pytest.mark.asyncio
async def test_the_totals_row_sums_the_slots_instead_of_averaging_them():
    """평균의 평균이 되면 슬롯마다 표본이 다를 때 값이 틀린다."""
    accuracy = [
        AccuracyRow(slot="pre_open", graded=4, hits=1, within_band=2, error_sum=4.0),
        AccuracyRow(slot="midday", graded=1, hits=1, within_band=1, error_sum=0.5),
    ]
    async with client(accuracy=accuracy) as http:
        rows = {row["slot"]: row for row in (await http.get("/api/forecasts/accuracy")).json()["rows"]}

    assert rows["all"]["graded"] == 5
    assert rows["all"]["hit_rate"] == 0.4
    assert rows["all"]["mean_abs_error"] == 0.9
