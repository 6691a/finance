"""요인 관계·메모 라우트와 감쇠 평균.

**가중치 식이 두 곳에 있다.** 원본은 `airflow/modules/kospi/domain.relation_weight`이고
`apps/`는 그 트리를 import하지 못한다 — 그래서 같은 식을 한 벌 더 두고 **여기서 대조한다.**
상수가 어긋나면 화면과 프롬프트가 같은 요인에 다른 값을 말한다.
"""

from datetime import date
from typing import Any

import httpx
import pytest
from dependency_injector import providers

from apps.api.app import create_app
from apps.api.repository import MemoryRow, ObservationRow
from apps.api.service import relation as service
from tests.api.conftest import container

AS_OF = date(2026, 9, 3)


def observation(
    factor: str = "FOREIGN_NET_BUY",
    observed_on: date = AS_OF,
    sign: str = "same",
    strength: int = 3,
    note: str = "외국인 순매수가 상승을 주도",
    llm_run_id: int | None = 13,
) -> ObservationRow:
    return ObservationRow(
        factor=factor,
        observed_on=observed_on,
        sign=sign,
        strength=strength,
        note=note,
        llm_run_id=llm_run_id,
    )


def memory(memory_id: int = 17, retired: bool = False) -> MemoryRow:
    return MemoryRow(
        id=memory_id,
        created_on=date(2026, 9, 1),
        text="목요일(09-04) 밤 미국 8월 CPI 발표",
        factor="US10Y",
        verify_count=2,
        unreviewed_count=0,
        last_verified_on=date(2026, 9, 2),
        retired_on=AS_OF if retired else None,
        retire_reason="dropped" if retired else None,
        llm_run_id=13,
    )


class FakeGraph:
    """드라이버 없이 행만 준다. 그 위의 진짜 서비스가 접고 계약을 만든다."""

    def __init__(self, observations=(), memories=(), enabled: bool = True) -> None:
        self._observations = tuple(observations)
        self._memories = tuple(memories)
        self.enabled = enabled

    def observations(self, **_: Any) -> tuple[ObservationRow, ...]:
        return self._observations

    def memories(self) -> tuple[MemoryRow, ...]:
        return self._memories


def client(**kwargs: Any) -> httpx.AsyncClient:
    built = container()
    built.kospi_graph_repository.override(providers.Object(FakeGraph(**kwargs)))
    app = create_app(built)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


# --- 감쇠 평균 ---------------------------------------------------------------


def test_the_weight_matches_the_airflow_constants():
    """상수 넷이 원본과 같아야 한다. 어긋나면 화면과 프롬프트가 다른 값을 말한다.

    **`airflow/`를 import하지 않는다** — 그 트리는 `/opt/airflow`로 마운트되고 이쪽에
    없다. 그래서 값을 여기에 적어 두고 대조한다.
    """
    assert service.RELATION_HALF_LIFE_DAYS == 5
    assert service.RELATION_WINDOW == 15
    assert service.RECENT_SIGN_COUNT == 3
    assert service.MAX_STRENGTH == 3


def test_the_decay_halves_every_half_life():
    assert service.decay_weight(AS_OF, AS_OF) == 1.0
    assert service.decay_weight(date(2026, 8, 29), AS_OF) == 0.5
    assert service.decay_weight(date(2026, 8, 24), AS_OF) == 0.25


def test_a_future_observation_keeps_full_weight_instead_of_being_hidden():
    """그런 행이 오면 조회가 잘못된 것이다. 조용히 줄이면 그 결함이 숨는다."""
    assert service.decay_weight(date(2026, 9, 10), AS_OF) == 1.0


def test_the_newest_observation_dominates_an_old_streak():
    """단순 평균이면 반전을 2주 뒤에야 보여 준다."""
    rows = [
        observation(observed_on=AS_OF, sign="inverse", strength=3),
        *[
            observation(observed_on=date(2026, 8, 10 + day), sign="same", strength=3)
            for day in range(5)
        ],
    ]

    folded = service.fold("FOREIGN_NET_BUY", rows, as_of_date=AS_OF)

    assert folded.weight < 0
    assert folded.n_obs == 6


def test_the_fold_does_not_trust_the_incoming_order():
    """조회가 순서를 바꿔도 값이 흔들리지 않아야 한다."""
    rows = [
        observation(observed_on=date(2026, 8, 20), sign="same"),
        observation(observed_on=AS_OF, sign="inverse"),
    ]

    assert service.fold("X", rows, as_of_date=AS_OF) == service.fold(
        "X", list(reversed(rows)), as_of_date=AS_OF
    )


def test_a_factor_with_no_observations_is_zero_and_says_so():
    """0은 "관계 없음"이 아니라 "아직 모른다"다. `n_obs`가 그 둘을 가른다."""
    folded = service.fold("VIX", [], as_of_date=AS_OF)

    assert (folded.weight, folded.n_obs) == (0.0, 0)
    assert folded.last_date is None


def test_the_recent_signs_are_shown_without_decay():
    """가중치 하나로는 "오래 일관된 -0.5"와 "막 뒤집히는 중인 -0.15"가 안 갈린다."""
    rows = [
        observation(observed_on=date(2026, 9, 1 + day), sign="same" if day == 0 else "inverse")
        for day in range(3)
    ]
    rows.append(observation(observed_on=date(2026, 8, 31), sign="same"))

    folded = service.fold("US10Y", rows, as_of_date=AS_OF)

    # 최신 셋만, 감쇠 없이 그대로다. 넷째(08-31)는 안 보인다.
    assert folded.recent_signs == ("inverse", "inverse", "same")
    assert folded.n_obs == 4


# --- 라우트 -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_factor_gets_a_row_even_with_nothing_observed():
    """빈 칸으로 두면 "관계 없음"으로 읽힌다."""
    async with client(observations=[observation()]) as http:
        payload = (await http.get("/api/relations", params={"limit": 200})).json()

    codes = [item["factor"] for item in payload["items"]]
    assert set(codes) == set(service.FACTOR_LABELS)
    assert payload["items"][0]["factor"] == "FOREIGN_NET_BUY"
    assert payload["items"][0]["n_obs"] == 1
    assert payload["items"][-1]["n_obs"] == 0


@pytest.mark.asyncio
async def test_a_factor_outside_the_vocabulary_is_carried_not_hidden():
    """조용히 숨기면 어휘가 갈린 것을 못 본다."""
    async with client(observations=[observation(factor="MYSTERY")]) as http:
        codes = [
            item["factor"]
            for item in (await http.get("/api/relations", params={"limit": 200})).json()["items"]
        ]

    assert "MYSTERY" in codes


@pytest.mark.asyncio
async def test_the_observation_list_is_scoped_to_one_factor_and_carries_its_weight():
    rows = [observation(), observation(factor="VIX", observed_on=date(2026, 8, 29))]
    async with client(observations=rows) as http:
        payload = (await http.get("/api/relations/VIX")).json()

    assert len(payload["items"]) == 1
    assert payload["items"][0]["weight"] == 0.5
    assert payload["items"][0]["llm_run_url"] == "/api/llm-runs/13"


@pytest.mark.asyncio
async def test_the_graph_is_a_star_of_depth_one():
    """요인이 코스피 하나를 가리킨다. 관측 없는 요인은 노드만 있고 엣지가 없다."""
    async with client(observations=[observation()]) as http:
        payload = (await http.get("/api/relations/graph")).json()

    assert {node["id"] for node in payload["nodes"]} >= {"index:KOSPI", "factor:FOREIGN_NET_BUY"}
    assert len(payload["edges"]) == 1
    assert payload["edges"][0] == {
        "source": "factor:FOREIGN_NET_BUY",
        "target": "index:KOSPI",
        "weight": 1.0,
        "n_obs": 1,
    }


@pytest.mark.asyncio
async def test_the_memory_list_shows_the_retired_ones_too():
    """왜 지웠는지가 남아 있어야 한다는 것이 이 기능의 설계 의도다."""
    async with client(memories=[memory(), memory(18, retired=True)]) as http:
        every = (await http.get("/api/relations/memories")).json()
        active = (await http.get("/api/relations/memories", params={"retired": "false"})).json()
        gone = (await http.get("/api/relations/memories", params={"retired": "true"})).json()

    assert len(every["items"]) == 2
    assert [row["id"] for row in active["items"]] == [17]
    assert gone["items"][0]["retire_reason"] == "dropped"


@pytest.mark.asyncio
async def test_a_missing_memory_is_a_404():
    async with client(memories=[memory()]) as http:
        assert (await http.get("/api/relations/memories/999")).status_code == 404


@pytest.mark.asyncio
async def test_the_static_paths_win_over_the_factor_path():
    """`/graph`와 `/memories`가 `{factor}`보다 먼저 등록돼 있어야 한다."""
    async with client(observations=[observation()], memories=[memory()]) as http:
        assert "nodes" in (await http.get("/api/relations/graph")).json()
        assert "items" in (await http.get("/api/relations/memories")).json()


@pytest.mark.asyncio
async def test_no_graph_store_is_a_503_not_an_empty_list():
    """빈 목록으로 위장하면 "관측이 없다"와 "저장소가 없다"가 같아 보인다."""
    async with client(enabled=False) as http:
        for path in ("/api/relations", "/api/relations/graph", "/api/relations/memories"):
            reply = await http.get(path)
            assert reply.status_code == 503
            assert reply.json()["detail"] == "neo4j is not configured"
