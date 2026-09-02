"""툴 호출 원장. **툴박스도 DB도 없이 돈다** — 그것이 클래스로 뗀 이유다.

`ThesisToolbox` 안에 있을 때는 원장 하나를 보려고 연결·기준 시각·대상 목록을 갖춘 툴박스를
만들어야 했다. 여기 있는 것은 요청 shell을 열고(`begin_round`), 실행을 감싸고(`record`),
`ToolMessage`로 닫는(`finish_round`) 세 단계뿐이다.

툴박스를 통과하는 같은 경로는 `test_thesis_pipeline.py`가 그대로 덮는다.
"""

from datetime import UTC, datetime

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from modules.thesis.domain import ToolCallErrorKind, ToolLimitExceeded
from modules.thesis.tool_ledger import ToolCallLedger

KNOWN = frozenset({"recent_documents"})


def call(name: str = "recent_documents", call_id: str = "call_1", **args: object) -> dict:
    return {"name": name, "args": args or {"hours": 6}, "id": call_id}


def test_a_round_opens_one_record_per_requested_call():
    ledger = ToolCallLedger()

    ledger.begin_round([call(call_id="call_1"), call(call_id="call_2")])

    assert ledger.round_count == 1
    assert [record.seq for record in ledger.calls] == [1, 2]
    assert [record.round_no for record in ledger.calls] == [1, 1]
    # 모델이 보낸 원본 인자다. `StructuredTool` 검증 전이라 기본값이 안 채워져 있다.
    assert ledger.calls[0].arguments == {"hours": 6}
    assert ledger.calls[0].requested_at.tzinfo is not None


def test_the_wrapper_fills_the_result_and_the_validated_arguments():
    ledger = ToolCallLedger()
    ledger.begin_round([call()])
    wrapped = ledger.record("recent_documents", lambda **kwargs: "[]")

    assert wrapped(tool_call_id="call_1", hours=6, min_score=5) == "[]"

    record = ledger.calls[0]
    assert record.result == "[]"
    assert record.result_chars == 2
    assert record.validated_arguments == {"hours": 6, "min_score": 5}
    assert record.duration_ms is not None
    # 아직 모델에게 돌아갔는지는 모른다. `finish_round`가 채운다.
    assert record.delivered is False


def test_a_limit_error_is_recorded_and_re_raised():
    """예외는 기록한 뒤 다시 올린다. 삼키면 DB 끊김이 "결과 없음"으로 위장된다."""
    ledger = ToolCallLedger()
    ledger.begin_round([call()])

    def boom(**_kwargs: object) -> str:
        raise ToolLimitExceeded("상한 초과")

    with pytest.raises(ToolLimitExceeded):
        ledger.record("recent_documents", boom)(tool_call_id="call_1")

    record = ledger.calls[0]
    assert record.error_kind is ToolCallErrorKind.LIMIT
    assert "상한 초과" in (record.error or "")


def test_an_execution_error_is_recorded_with_its_own_kind():
    ledger = ToolCallLedger()
    ledger.begin_round([call()])

    def boom(**_kwargs: object) -> str:
        raise RuntimeError("connection closed")

    with pytest.raises(RuntimeError):
        ledger.record("recent_documents", boom)(tool_call_id="call_1")

    assert ledger.calls[0].error_kind is ToolCallErrorKind.EXECUTION


def test_an_unknown_tool_is_classified_by_the_names_it_is_given():
    """모르는 툴은 함수에 도달하지 않는다. 래퍼가 못 보므로 `finish_round`가 채운다."""
    ledger = ToolCallLedger()
    ledger.begin_round([call(name="nope")])

    ledger.finish_round(
        [ToolMessage(content="Error: nope is not a valid tool", tool_call_id="call_1")],
        known_tools=KNOWN,
    )

    record = ledger.calls[0]
    assert record.error_kind is ToolCallErrorKind.UNKNOWN_TOOL
    assert record.delivered is True


def test_a_known_tool_that_never_ran_is_an_argument_failure():
    ledger = ToolCallLedger()
    ledger.begin_round([call()])

    ledger.finish_round(
        [ToolMessage(content="Error: ToolInvocationError(...)", tool_call_id="call_1")],
        known_tools=KNOWN,
    )

    assert ledger.calls[0].error_kind is ToolCallErrorKind.VALIDATION


def test_only_tool_messages_close_a_record():
    ledger = ToolCallLedger()
    ledger.begin_round([call()])

    ledger.finish_round([AIMessage("사람 말")], known_tools=KNOWN)

    assert ledger.calls[0].delivered is False


def test_a_record_that_ran_but_never_reached_the_model_keeps_its_result():
    """sibling 하나가 죽으면 `ToolNode`가 나머지 결과를 버린다. 오류가 아니라 "모델만 못 봤다"다."""
    ledger = ToolCallLedger()
    ledger.begin_round([call()])
    ledger.record("recent_documents", lambda **_kwargs: "[]")(tool_call_id="call_1")

    assert ledger.calls[0].result == "[]"
    assert ledger.calls[0].delivered is False
    assert ledger.calls[0].error is None


def test_open_records_are_closed_as_cancelled():
    """결과도 오류도 없는 행은 DB CHECK(둘 중 하나는 있어야 한다)를 어긴다."""
    ledger = ToolCallLedger()
    ledger.begin_round([call()])

    ledger.close_open_records()

    record = ledger.calls[0]
    assert record.error_kind is ToolCallErrorKind.CANCELLED
    assert record.error == "sibling 실패로 실행되지 않았다"


def test_closing_does_not_touch_a_finished_record():
    ledger = ToolCallLedger()
    ledger.begin_round([call()])
    ledger.record("recent_documents", lambda **_kwargs: "[]")(tool_call_id="call_1")

    ledger.close_open_records()

    assert ledger.calls[0].error_kind is None


def test_rounds_accumulate_across_calls():
    ledger = ToolCallLedger()
    started = datetime.now(UTC)

    ledger.begin_round([call(call_id="call_1")])
    ledger.begin_round([call(call_id="call_2")])

    assert ledger.round_count == 2
    assert [record.round_no for record in ledger.calls] == [1, 2]
    assert ledger.calls[1].requested_at >= started
