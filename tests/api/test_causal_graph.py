"""인과 그래프 조회.

주제 셋: ① 체인이 `position` 순서로 펴진다 ② 실현 등락에 단위가 함께 나간다
③ 경로가 0인 사건·채널도 목록에 남는다.
"""

from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest
from dependency_injector import providers

from apps.api.app import create_app
from apps.api.container import ApiContainer
from apps.api.repository import PathRows
from apps.api.repository.causal import MarketCausalReadRepository
from apps.models.analysis import MarketCausalPath, MarketEvent

WEEK = date(2026, 8, 10)


def event(event_id: int = 1, title: str = "미국 7월 소비자물가 상승률 둔화") -> MarketEvent:
    """**ORM 인스턴스를 그대로 만든다.** 행 묶음이 모델 타입을 요구하고, 그 요구 자체가
    "리포지토리는 행을 준다"는 계약이라 가짜로 우회하지 않는다. 세션은 필요 없다."""
    return MarketEvent(
        id=event_id, title=title, occurred_on=date(2026, 8, 12), first_seen_week=WEEK
    )


def path(path_id: int = 1, unit: str = "percent") -> MarketCausalPath:
    return MarketCausalPath(
        id=path_id,
        week_start=WEEK,
        event_id=1,
        source_key="e:1",
        target_kind="quote",
        target_code="US10Y",
        sign="down",
        confidence="observed",
        reasoning="물가 둔화로 긴축 우려가 낮아졌다.",
        return_week_change=Decimal("-0.0638"),
        return_t1_change=Decimal("0.5962"),
        return_t5_change=Decimal("0.8944"),
        return_unit=unit,
        llm_run_id=None,
    )


def target_path(path_id: int = 51) -> MarketCausalPath:
    """대상에서 출발한 경로. 원인 쪽이 문서가 아니라 우리가 가진 값이다."""
    row = path(path_id)
    row.event_id = None
    row.source_key = "t:quote:US10Y:down"
    row.source_target_kind = "quote"
    row.source_target_code = "US10Y"
    row.source_sign = "down"
    row.target_kind = "instrument"
    row.target_code = "005930"
    row.sign = "up"
    row.confidence = "endpoint_observed"
    return row


class FakeCausal:
    def __init__(self, **rows: Any) -> None:
        self.rows = rows
        self.calls: list[dict[str, Any]] = []

    async def path_rows(self, **kwargs: Any) -> PathRows:
        self.calls.append(kwargs)
        return self.rows.get("paths", PathRows())

    async def detail_rows(self, path_id: int) -> PathRows | None:
        self.calls.append({"path_id": path_id})
        return self.rows.get("detail")

    async def event_rows(self, **kwargs: Any) -> tuple[tuple[Any, ...], bool]:
        self.calls.append(kwargs)
        return tuple(self.rows.get("events", [])), False

    async def channel_rows(self, **kwargs: Any) -> tuple[tuple[Any, ...], bool]:
        self.calls.append(kwargs)
        return tuple(self.rows.get("channels", [])), False


def client(fake: FakeCausal) -> httpx.AsyncClient:
    built = ApiContainer()
    built.causal_repository.override(providers.Object(fake))
    app = create_app(built)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_the_chain_comes_out_in_step_order():
    """단계는 `position` 순서가 곧 사건에서 대상까지의 순서다. 섞이면 이야기가 거꾸로 읽힌다."""
    rows = PathRows(
        paths=(path(),),
        events={1: event()},
        chains={1: ("통화정책 기대", "할인율")},
    )
    async with client(FakeCausal(paths=rows)) as http:
        payload = (await http.get("/api/causal/paths")).json()

    item = payload["items"][0]
    assert item["channels"] == ["통화정책 기대", "할인율"]
    assert item["event_title"] == "미국 7월 소비자물가 상승률 둔화"


@pytest.mark.asyncio
async def test_the_change_carries_its_unit():
    """`KTB10Y`의 7bp와 KOSPI의 10%가 한 칸에 들어가면 크기 비교가 조용히 무의미해진다."""
    rows = PathRows(paths=(path(unit="basis_point"),), events={1: event()}, chains={})
    async with client(FakeCausal(paths=rows)) as http:
        item = (await http.get("/api/causal/paths")).json()["items"][0]

    assert item["return_unit"] == "basis_point"
    assert item["return_week_change"] == -0.0638


@pytest.mark.asyncio
async def test_a_target_origin_path_is_not_dropped():
    """**사건이 없는 경로가 정상이다.** 대상에서 출발한 경로가 그렇다.

    전에는 사건을 못 찾은 행을 버렸는데, 그 규칙이 대상 출발 경로를 통째로 삼켰다
    (2026-08-30 실측: 그 주 24개 중 8개가 대상 출발이다).
    """
    rows = PathRows(paths=(target_path(),), events={}, chains={51: ("위험선호",)})
    async with client(FakeCausal(paths=rows)) as http:
        item = (await http.get("/api/causal/paths")).json()["items"][0]

    assert item["source_kind"] == "target"
    assert item["event_id"] is None
    assert item["source_target_code"] == "US10Y"
    assert item["source_sign"] == "down"
    assert item["confidence"] == "endpoint_observed"


@pytest.mark.asyncio
async def test_an_event_with_no_path_stays_in_the_list():
    """"사건은 있는데 경로가 안 나왔다"가 사실이다. 그 행이 사라지면 그것을 못 본다."""
    async with client(FakeCausal(events=[(event(9, "미국 재정적자 확대"), 0)])) as http:
        payload = (await http.get("/api/causal/events")).json()

    assert payload["items"][0]["paths"] == 0


@pytest.mark.asyncio
async def test_the_week_window_reaches_the_repository():
    fake = FakeCausal()
    async with client(fake) as http:
        await http.get("/api/causal/paths", params={"from": "2026-08-01", "to": "2026-08-27"})

    assert fake.calls[0]["start"] == date(2026, 8, 1)
    assert fake.calls[0]["end"] == date(2026, 8, 27)


def test_the_event_count_is_a_left_join():
    """안쪽 조인이면 경로 없는 사건이 목록에서 사라진다."""
    compiled = str(MarketCausalReadRepository.event_statement(start=WEEK, end=WEEK))
    assert "LEFT OUTER JOIN" in compiled

    channels = str(MarketCausalReadRepository.channel_statement(start=WEEK, end=WEEK))
    assert "LEFT OUTER JOIN" in channels


def test_the_chain_query_orders_by_position():
    compiled = str(MarketCausalReadRepository.chain_statement([1, 2]))
    assert "ORDER BY market_causal_step.path_id, market_causal_step.position" in compiled


@pytest.mark.asyncio
async def test_the_detail_carries_the_whole_week():
    """**주 단위로 묶는다.** 사건으로 묶으면 대상이 다시 원인이 되는 사슬이 조각난다 —
    `US10Y → 005930`은 그 주의 다른 경로가 만든 `US10Y`에 이어져야 뜻이 산다."""
    rows = PathRows(
        paths=(path(1), path(2), target_path(51)),
        events={1: event(), 2: event()},
        chains={1: ("통화정책 기대",), 2: ("통화정책 기대", "할인율"), 51: ("위험선호",)},
    )
    async with client(FakeCausal(detail=rows)) as http:
        payload = (await http.get("/api/causal/paths/2")).json()

    assert payload["path"]["id"] == 2
    assert [row["id"] for row in payload["siblings"]] == [1, 2, 51]
    # 사건 출발과 대상 출발이 한 판에 함께 온다.
    assert {row["source_kind"] for row in payload["siblings"]} == {"event", "target"}


@pytest.mark.asyncio
async def test_the_detail_resolves_the_evidence_ref():
    """`document:189`만 보이면 사람이 못 읽는다. **규약을 응답이 한 번만 푼다.**"""
    rows = PathRows(
        paths=(path(1),),
        events={1: event()},
        chains={},
        evidence=((1, "document:189"), (1, "disclosure:20260826000445"), (1, "unknown:7")),
        document_titles={189: "물가 둔화에 국채금리 하락"},
    )
    async with client(FakeCausal(detail=rows)) as http:
        payload = (await http.get("/api/causal/paths/1")).json()

    document, disclosure, unknown = payload["evidence"]
    assert document["kind"] == "document"
    assert document["title"] == "물가 둔화에 국채금리 하락"
    assert document["url"] == "/documents/189"
    # 공시는 접수번호가 곧 주소다.
    assert disclosure["url"].endswith("rcpNo=20260826000445")
    # 모르는 종류는 링크를 만들지 않는다. 없는 곳으로 가는 링크는 없는 것보다 나쁘다.
    assert unknown["url"] is None
    assert unknown["ref"] == "unknown:7"


@pytest.mark.asyncio
async def test_an_unknown_path_is_404():
    async with client(FakeCausal()) as http:
        reply = await http.get("/api/causal/paths/999")

    assert reply.status_code == 404


@pytest.mark.asyncio
async def test_the_static_paths_route_wins_over_the_path_id():
    """정적 경로가 뒤면 `paths`를 정수로 파싱하려다 422가 된다."""
    async with client(FakeCausal()) as http:
        assert (await http.get("/api/causal/paths")).status_code == 200
