"""주간 방향성 요약. 모델은 안 부르고 검증·조립·저장만 본다.

이 흐름에서 틀릴 수 있는 것은 넷이다 — 모델이 낸 값을 그대로 믿는 것, 대상이 빠진 답을
성공으로 넘기는 것, 코드가 세야 할 숫자를 모델에게 맡기는 것, 그리고 JSONB 경계에서
값이 깨지는 것.

계약은 docs/analysis/market-thesis/17-graph-query.md §3이다.
"""

import json
from datetime import date
from typing import Any, Self

import pytest

from modules.causal import direction as flow
from modules.causal import store
from modules.causal.direction import (
    DirectionAnswer,
    DirectionError,
    DirectionReply,
    DirectionSummarizer,
)
from modules.causal.domain import Direction
from modules.graph_query import Chain, DirectionInput, Landing

WEEK = date(2026, 8, 17)


def landing(**overrides: Any) -> Landing:
    values: dict[str, Any] = {
        "path_id": 1,
        "sign": "up",
        "confidence": "observed",
        "reasoning": "이익 기대가 받쳤다",
        "channel": "이익 기대",
        "source": "AI 수요 기대 확대",
        "source_kind": "Event",
    }
    values.update(overrides)
    return Landing(**values)


def found(code: str = "005930", **overrides: Any) -> DirectionInput:
    values: dict[str, Any] = {
        "kind": "instrument",
        "code": code,
        "week_start": WEEK,
        "landings": (landing(), landing(path_id=2, sign="down", channel="할인율")),
        "chains": (),
    }
    values.update(overrides)
    return DirectionInput(**values)


def summarizer() -> DirectionSummarizer:
    # 모델은 안 부른다. 검증과 조립만 보는 테스트라 생성자에 아무것이나 넣는다.
    return DirectionSummarizer(model=object())


def answer(code: str = "005930", bias: str = "mixed", reasoning: str = "이익 기대와 할인율이 맞섰다") -> DirectionAnswer:
    return DirectionAnswer(code=code, bias=bias, reasoning=reasoning)


# --- 검증 --------------------------------------------------------------------


def test_a_missing_target_fails_the_whole_answer():
    """온 것만 저장하면 나머지가 "방향성 없음"으로 조용히 내려간다.

    그것은 "그 주에 경로가 없었다"와 구별되지 않는다(모듈 docstring).
    """
    inputs = {"005930": found("005930"), "000660": found("000660")}

    with pytest.raises(DirectionError, match="000660"):
        summarizer().verify(DirectionReply(directions=[answer("005930")]), inputs)


def test_a_target_we_did_not_ask_for_is_dropped():
    """툴 결과 레지스트리와 같은 규칙이다. 목록 밖은 버린다."""
    inputs = {"005930": found("005930")}

    directions = summarizer().verify(
        DirectionReply(directions=[answer("005930"), answer("KOSPI")]), inputs
    )

    assert [item.target_code for item in directions] == ["005930"]


def test_an_unknown_bias_is_rejected():
    inputs = {"005930": found("005930")}

    with pytest.raises(DirectionError, match="unknown bias"):
        summarizer().verify(DirectionReply(directions=[answer(bias="sideways")]), inputs)


def test_an_empty_reasoning_is_rejected():
    """빈 문장은 종합을 안 한 것이다. 그것을 저장하면 재료만 남고 판단이 사라진다."""
    inputs = {"005930": found("005930")}

    with pytest.raises(DirectionError, match="no reasoning"):
        summarizer().verify(DirectionReply(directions=[answer(reasoning="   ")]), inputs)


def test_a_long_reasoning_is_cut_not_rejected():
    """상한은 프롬프트에도 실려 있다. 넘겼다고 그 대상을 통째로 버리지 않는다."""
    inputs = {"005930": found("005930")}

    directions = summarizer().verify(
        DirectionReply(directions=[answer(reasoning="가" * 500)]), inputs
    )

    assert len(directions[0].reasoning) == flow.MAX_DIRECTION_REASONING_CHARS


# --- 조립 --------------------------------------------------------------------


def test_the_counts_come_from_the_graph_not_the_model():
    """모델이 내는 것은 `bias` 하나와 문장 하나뿐이다(설계 §3.1)."""
    inputs = {"005930": found("005930")}

    direction = summarizer().verify(DirectionReply(directions=[answer()]), inputs)[0]

    assert (direction.up_count, direction.down_count, direction.flat_count) == (1, 1, 0)
    assert direction.path_ids == (1, 2)
    assert direction.channels == (
        {"name": "이익 기대", "up": 1, "down": 0},
        {"name": "할인율", "up": 0, "down": 1},
    )


def test_the_week_comes_from_the_graph_input():
    """모델에게 주를 묻지 않는다. 그 값은 우리가 물은 것이다."""
    inputs = {"005930": found("005930")}

    direction = summarizer().verify(DirectionReply(directions=[answer()]), inputs)[0]

    assert direction.week_start == WEEK
    assert direction.target_kind == "instrument"


# --- 프롬프트에 실리는 모양 --------------------------------------------------


def test_the_rendered_block_names_the_source_channel_and_direction():
    text = flow.render_targets([found("005930")])

    assert "### 005930 (instrument)" in text
    assert "AI 수요 기대 확대 → 이익 기대 → 005930 up (observed)" in text
    assert "up 1 / down 1" in text


def test_a_truncated_input_says_so_in_the_prompt():
    """조용히 자르면 모델이 "이것이 전부"로 읽는다."""
    text = flow.render_targets([found("005930", truncated=True)])

    assert "전부가 아니다" in text


def test_chains_are_labelled_as_carried_over_claims():
    """이어진 경로는 착지 주장의 맥락이지 별도의 한 표가 아니다."""
    text = flow.render_targets(
        [found("005930", chains=(Chain(path_ids=(85, 82), sign="down", chain=("US10Y", "할인율", "005930")),))]
    )

    assert "이어진 경로" in text
    assert "US10Y → 할인율 → 005930 down" in text


def test_an_empty_input_list_never_calls_the_model():
    """그 주에 경로가 하나도 없었다. 부를 것이 없고 저장할 것도 없다."""
    assert summarizer().summarize([], week_start=WEEK) == ()


# --- 저장 --------------------------------------------------------------------


class FakeCursor:
    def __init__(self, connection: "FakeConnection") -> None:
        self._connection = connection

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: str, parameters: Any = ()) -> None:
        self._connection.calls.append((statement, parameters))


class FakeConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.committed = 0
        self.rolled_back = 0

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.committed += 1

    def rollback(self) -> None:
        self.rolled_back += 1


def direction_row(**overrides: Any) -> Direction:
    values: dict[str, Any] = {
        "target_kind": "instrument",
        "target_code": "005930",
        "week_start": WEEK,
        "bias": "mixed",
        "reasoning": "이익 기대와 할인율이 맞섰다",
        "up_count": 1,
        "down_count": 1,
        "flat_count": 0,
        "path_ids": (1, 2),
        "channels": ({"name": "이익 기대", "up": 1, "down": 0},),
    }
    values.update(overrides)
    return Direction(**values)


def test_jsonb_columns_go_out_as_text():
    """드라이버가 dict/list를 JSONB로 안 받는다. 경계에서 한 번만 문자열로 만든다."""
    connection = FakeConnection()

    store.store_directions(connection, [direction_row()], llm_run_id=7)

    _, parameters = connection.calls[0]
    assert json.loads(parameters["path_ids"]) == [1, 2]
    assert json.loads(parameters["channels"]) == [{"name": "이익 기대", "up": 1, "down": 0}]
    assert parameters["llm_run_id"] == 7


def test_korean_channel_names_are_not_escaped():
    """`ensure_ascii=True`면 `\\ud560\\uc778\\uc728`이 저장돼 psql로 읽을 수 없다."""
    connection = FakeConnection()

    store.store_directions(connection, [direction_row()], llm_run_id=None)

    _, parameters = connection.calls[0]
    assert "이익 기대" in parameters["channels"]


def test_the_upsert_updates_instead_of_doing_nothing():
    """경로와 반대 판단이다. 그래프가 다시 밀리면 요약도 따라간다(설계 §3.2)."""
    connection = FakeConnection()

    store.store_directions(connection, [direction_row()], llm_run_id=None)

    statement, _ = connection.calls[0]
    assert "ON CONFLICT (week_start, target_kind, target_code) DO UPDATE" in statement


def test_the_rows_land_in_one_transaction():
    """방향성 여럿이 한 주의 요약이다. 절반만 들어간 주를 남기지 않는다."""
    connection = FakeConnection()

    store.store_directions(
        connection,
        [direction_row(), direction_row(target_code="000660")],
        llm_run_id=None,
    )

    assert len(connection.calls) == 2
    assert connection.committed == 1
    assert connection.rolled_back == 0
