"""인과 그래프를 대상 기준으로 읽는 쪽. 실제 Neo4j는 안 띄운다.

가짜 드라이버/세션으로 **무엇을 물었는지**와 **무엇을 셌는지**를 본다. 이 모듈에서 틀릴 수
있는 것은 넷이다 — 다중 홉에 조건 셋이 빠지는 것, 세기를 잘못 세는 것, 상한을 조용히
넘기는 것, 그리고 예외를 재시도 가능/불가능으로 잘못 가르는 것.

계약은 docs/analysis/market-thesis/17-graph-query.md §2다.
"""

from datetime import UTC, date, datetime
from typing import Any, Self

import pytest
from neo4j.exceptions import ClientError, ServiceUnavailable, TransientError

from modules.graph import query as graph_query

WEEK = date(2026, 8, 17)
AS_OF = datetime(2026, 9, 1, tzinfo=UTC)


def landing(**overrides: Any) -> dict[str, Any]:
    row = {
        "path_id": 1,
        "sign": "up",
        "confidence": "observed",
        "reasoning": "이익 기대가 받쳤다",
        "channel": "이익 기대",
        "source": "AI 수요 기대 확대",
        "source_kind": "Event",
    }
    row.update(overrides)
    return row


def chain(**overrides: Any) -> dict[str, Any]:
    row = {"path_ids": [85, 82], "sign": "down", "chain": ["US10Y", "할인율", "SOX", "투자심리", "KOSPI"]}
    row.update(overrides)
    return row


class FakeResult:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def __iter__(self):
        return iter(FakeRecord(row) for row in self._rows)


class FakeRecord:
    def __init__(self, row: dict) -> None:
        self._row = row

    def data(self) -> dict:
        return self._row


class FakeSession:
    def __init__(self, driver: "FakeDriver") -> None:
        self._driver = driver

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def run(self, query: Any, **parameters: Any) -> FakeResult:
        text = getattr(query, "text", query)
        self._driver.calls.append((text, parameters, getattr(query, "timeout", None)))
        if self._driver.error is not None:
            raise self._driver.error
        if "HITS]->(t:Target" in text:
            return FakeResult(self._driver.landings)
        if "week_start AS week_start" in text:
            return FakeResult(self._driver.weeks)
        return FakeResult(self._driver.chains)


class FakeDriver:
    def __init__(
        self,
        landings: list[dict] | None = None,
        chains: list[dict] | None = None,
        weeks: list[dict] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.landings = landings or []
        self.chains = chains or []
        self.weeks = weeks or []
        self.error = error
        self.calls: list[tuple[str, dict, Any]] = []

    def session(self) -> FakeSession:
        return FakeSession(self)


def read(driver: FakeDriver, **overrides: Any) -> graph_query.DirectionInput:
    values: dict[str, Any] = {"kind": "index", "code": "KOSPI", "week_start": WEEK, "as_of_at": AS_OF}
    values.update(overrides)
    return graph_query.read_direction_input(driver, **values)


# --- 쿼리가 무엇을 거는가 ---------------------------------------------------


def test_the_multi_hop_query_carries_all_three_conditions():
    """셋 중 하나라도 빠지면 **조용히 틀린 답**이 나온다(설계 §2.3).

    `created_at`은 미래를 막고, `week_start` 단조는 시각 역행을 막고, `path_id` 접점 규칙은
    아무도 하지 않은 인과를 막는다.
    """
    statement = graph_query.CHAIN_QUERY

    assert "x.created_at <= $as_of_at" in statement
    assert "r[i].week_start <= r[i + 1].week_start" in statement
    assert "r[i].path_id = r[i + 1].path_id OR nodes(p)[i + 1]:Target" in statement


def test_the_path_boundary_opens_at_targets_not_everywhere():
    """`path_id`를 **전부 같게** 걸면 주장 사이의 홉이 통째로 사라진다.

    그것이 이 단계의 존재 이유라, 6홉에 닿는 노드가 5.1에서 멈춘다(접점 규칙은 6.7).
    """
    assert "all(x IN r WHERE x.path_id = r[0].path_id)" not in graph_query.CHAIN_QUERY
    assert "nodes(p)[i + 1]:Target" in graph_query.WALK_GUARD


def test_the_multi_hop_query_only_returns_paths_that_cross_a_boundary():
    """주장 하나 안의 2단 체인은 착지 쿼리가 이미 준 것이라 새 정보가 아니다."""
    assert "any(i IN range(0, size(r) - 2) WHERE r[i].path_id <> r[i + 1].path_id)" in graph_query.CHAIN_QUERY


def test_the_variable_length_match_is_bounded():
    """채널 그래프에 사이클이 있어 상한이 없으면 응답이 폭발한다."""
    assert f"*2..{graph_query.MAX_QUERY_DEPTH}]" in graph_query.CHAIN_QUERY


def test_the_source_is_anchored_by_label():
    """"들어오는 LEADS_TO가 없는 노드"로 찾으면 2단 체인의 첫 채널이 함께 잡힌다.

    그러면 `title`도 `code`도 없는 행이 나온다(2026-08-31 실측).
    """
    assert "s:Event OR s:Target" in graph_query.LANDING_QUERY
    assert "(s:Event OR s:Target)" in graph_query.CHAIN_QUERY


def test_both_queries_bind_the_cutoff_and_a_timeout():
    driver = FakeDriver(landings=[landing()])
    read(driver)

    assert len(driver.calls) == 2
    for _, parameters, timeout in driver.calls:
        assert parameters["as_of_at"] == AS_OF
        assert parameters["week_start"] == WEEK
        assert timeout == graph_query.QUERY_TIMEOUT_SECONDS


# --- 무엇을 세는가 -----------------------------------------------------------


def test_counts_come_from_the_landings_not_the_model():
    """숫자는 코드가 센다(설계 §3.1). 모델이 만드는 것은 `bias`와 문장뿐이다."""
    driver = FakeDriver(
        landings=[landing(path_id=1, sign="up"), landing(path_id=2, sign="down"), landing(path_id=3, sign="up")]
    )

    found = read(driver)

    assert (found.up_count, found.down_count, found.flat_count) == (2, 1, 0)


def test_an_unknown_sign_is_counted_apart_from_up_and_down():
    """`market_causal_path.sign`이 지금은 up/down뿐이지만 그 CHECK가 넓어질 수 있다."""
    driver = FakeDriver(landings=[landing(sign="flat")])

    found = read(driver)

    assert (found.up_count, found.down_count, found.flat_count) == (0, 0, 1)


def test_path_ids_join_the_landings_and_the_chain_seams():
    """다중 홉은 주장 여럿을 이은 것이라 근거도 여럿이다(설계 §6.7 발견 ②)."""
    driver = FakeDriver(
        landings=[landing(path_id=82)],
        chains=[chain(path_ids=[85, 82]), chain(path_ids=[54, 82])],
    )

    found = read(driver)

    # 착지가 먼저, 그 뒤 이음매. 같은 값은 한 번만.
    assert found.path_ids == (82, 85, 54)


def test_channel_counts_are_ordered_by_weight():
    """추론이 종합을 못 믿을 때 보는 재료다. 많이 민 채널이 앞이다."""
    driver = FakeDriver(
        landings=[
            landing(channel="투자심리", sign="up"),
            landing(channel="투자심리", sign="down"),
            landing(channel="할인율", sign="down"),
        ]
    )

    found = read(driver)

    assert found.channel_counts == (
        {"name": "투자심리", "up": 1, "down": 1},
        {"name": "할인율", "up": 0, "down": 1},
    )


# --- 상한과 실패 -------------------------------------------------------------


def test_hitting_the_row_cap_is_recorded_not_swallowed():
    """조용히 자르면 모델이 "이것이 전부"로 읽는다(저장소 규칙: 조용한 성공 금지)."""
    driver = FakeDriver(landings=[landing(path_id=index) for index in range(graph_query.MAX_ROWS_PER_TARGET + 1)])

    found = read(driver)

    assert found.truncated
    assert len(found.landings) == graph_query.MAX_ROWS_PER_TARGET


def test_a_result_under_the_cap_is_not_marked_truncated():
    driver = FakeDriver(landings=[landing()])

    assert read(driver).truncated is False


@pytest.mark.parametrize(
    "error",
    [ServiceUnavailable("down"), TransientError("busy")],
)
def test_transient_failures_become_connection_errors(error: Exception):
    """잠시 뒤 다시 부르면 될 실패다. Airflow가 재시도하게 그대로 올린다."""
    driver = FakeDriver(error=error)

    with pytest.raises(ConnectionError):
        read(driver)


def test_client_errors_become_graph_query_errors():
    """인증·쿼리 오류·timeout. 다시 불러도 같은 답이다."""
    driver = FakeDriver(error=ClientError("bad cypher"))

    with pytest.raises(graph_query.GraphQueryError):
        read(driver)
