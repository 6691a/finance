from typing import Self

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from modules.briefing.comment import (
    MAX_COMMENT_CHARS,
    REPAIR_INSTRUCTION,
    BriefingCommentator,
    CommentError,
)

SUMMARY = '{"kospi": {"close": 2687.45, "change_percent": 0.82}}'


class ScriptedModel:
    """LangChain 모델 자리에 끼운다. 실제 호출은 하지 않는다."""

    def __init__(self, *replies) -> None:
        self.replies = list(replies)
        self.calls: list[list] = []

    def bind(self, **kwargs) -> Self:
        return self

    def bind_tools(self, tools) -> Self:
        return self

    def invoke(self, messages) -> AIMessage:
        self.calls.append(list(messages))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return AIMessage(reply)


def commentator(*replies) -> tuple[BriefingCommentator, ScriptedModel]:
    scripted = ScriptedModel(*replies)
    return BriefingCommentator(scripted), scripted


def test_returns_the_model_text():
    assessor, model = commentator("  코스피가 0.82% 올랐다.  ")

    assert assessor.comment("한국장 브리핑", SUMMARY) == "코스피가 0.82% 올랐다."
    assert len(model.calls) == 1


def test_prompt_carries_the_report_name_and_the_aggregated_numbers():
    assessor, model = commentator("요약")

    assessor.comment("한국장 브리핑", SUMMARY)

    system, human = model.calls[0]
    assert isinstance(system, SystemMessage)
    assert isinstance(human, HumanMessage)
    assert "한국장 브리핑" in human.content
    assert SUMMARY in human.content


def test_multiple_paragraphs_are_kept():
    """요약 길이와 모양은 모델이 정한다. 단락 수를 제한하지 않는다."""
    assessor, _ = commentator("첫 줄.\n\n- 둘째 항목\n- 셋째 항목")

    assert assessor.comment("한국장 브리핑", SUMMARY) == "첫 줄.\n\n- 둘째 항목\n- 셋째 항목"


@pytest.mark.parametrize("bad", ["", "   ", "가" * (MAX_COMMENT_CHARS + 1)])
def test_broken_output_is_repaired_once(bad):
    assessor, model = commentator(bad, "고친 요약")

    assert assessor.comment("한국장 브리핑", SUMMARY) == "고친 요약"
    assert len(model.calls) == 2
    assert model.calls[1][-1].content == REPAIR_INSTRUCTION


def test_second_failure_gives_up():
    assessor, model = commentator("", "")

    with pytest.raises(CommentError):
        assessor.comment("한국장 브리핑", SUMMARY)
    assert len(model.calls) == 2


def test_connection_errors_reach_the_caller():
    """재시도할 값어치가 있는 실패는 교정 대상이 아니다. DAG가 판단한다."""
    assessor, model = commentator(ConnectionError("chat request failed"))

    with pytest.raises(ConnectionError):
        assessor.comment("한국장 브리핑", SUMMARY)
    assert len(model.calls) == 1
