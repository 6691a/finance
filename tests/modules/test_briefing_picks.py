import json
from typing import Self

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from modules.briefing.picks import (
    MAX_READS,
    MAX_WATCHES,
    MAX_WHY_CHARS,
    REPAIR_INSTRUCTION,
    DocumentPicker,
    PickError,
)

CANDIDATES = json.dumps({"documents": [{"document_id": 41}, {"document_id": 42}]}, ensure_ascii=False)
ALLOWED = frozenset({41, 42})


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


def picker(*replies) -> tuple[DocumentPicker, ScriptedModel]:
    scripted = ScriptedModel(*replies)
    return DocumentPicker(scripted), scripted


def reply(*picks) -> str:
    return json.dumps({"picks": list(picks)}, ensure_ascii=False)


def test_returns_what_the_model_picked():
    subject, model = picker(reply({"document_id": 42, "why": "공급 축소", "watch": False}))

    picks = subject.pick(24, CANDIDATES, ALLOWED)

    assert [(pick.document_id, pick.why, pick.watch) for pick in picks] == [(42, "공급 축소", False)]
    assert len(model.calls) == 1


def test_the_prompt_carries_the_candidate_list():
    subject, model = picker(reply())

    subject.pick(24, CANDIDATES, ALLOWED)

    system, human = model.calls[0]
    assert isinstance(system, SystemMessage)
    assert isinstance(human, HumanMessage)
    assert CANDIDATES in human.content


def test_picking_nothing_is_a_valid_answer():
    """한산한 날은 한산하다고 답하는 것이 정답이다. 빈 선별로 교정을 요청하지 않는다."""
    subject, model = picker(reply())

    assert subject.pick(24, CANDIDATES, ALLOWED) == ()
    assert len(model.calls) == 1


def test_ids_outside_the_candidate_list_are_dropped():
    """마스터에 없는 태그를 버리는 것과 같은 규칙이다. 지어낸 id 하나가 전체를 죽이지 않는다."""
    subject, _ = picker(reply({"document_id": 999, "why": "지어낸 것"}, {"document_id": 41, "why": "진짜"}))

    picks = subject.pick(24, CANDIDATES, ALLOWED)

    assert [pick.document_id for pick in picks] == [41]


def test_a_pick_list_with_no_real_id_is_retried_once():
    """전부 목록 밖이면 모델이 후보를 안 보고 답한 것이다. 그건 교정할 값어치가 있다."""
    subject, model = picker(reply({"document_id": 999}), reply({"document_id": 41, "why": "진짜"}))

    picks = subject.pick(24, CANDIDATES, ALLOWED)

    assert [pick.document_id for pick in picks] == [41]
    assert len(model.calls) == 2
    assert model.calls[1][-1].content == REPAIR_INSTRUCTION


def test_a_second_failure_raises():
    """두 번째도 깨지면 포기한다. DAG가 점수 순서로 떨어진다."""
    subject, model = picker("설명만 있고 JSON이 없다", "여전히 JSON이 없다")

    with pytest.raises(PickError):
        subject.pick(24, CANDIDATES, ALLOWED)

    assert len(model.calls) == 2


def test_too_many_picks_are_cut_to_the_limit():
    many = [{"document_id": 41, "why": "", "watch": False} for _ in range(MAX_READS + 3)]
    many += [{"document_id": 42, "why": "", "watch": True} for _ in range(MAX_WATCHES + 3)]
    subject, _ = picker(reply(*many))

    picks = subject.pick(24, CANDIDATES, ALLOWED)

    assert len([pick for pick in picks if not pick.watch]) == MAX_READS
    assert len([pick for pick in picks if pick.watch]) == MAX_WATCHES


def test_a_long_reason_is_trimmed_instead_of_dropping_the_pick():
    subject, _ = picker(reply({"document_id": 41, "why": "가" * (MAX_WHY_CHARS + 50)}))

    picks = subject.pick(24, CANDIDATES, ALLOWED)

    assert len(picks[0].why) == MAX_WHY_CHARS + 1  # 말줄임표 한 칸


def test_a_fenced_reply_is_still_read():
    """스키마를 강제하지 못한 제공처는 코드 펜스를 붙여 온다."""
    subject, _ = picker(f"```json\n{reply({'document_id': 41, 'why': '진짜'})}\n```")

    assert [pick.document_id for pick in subject.pick(24, CANDIDATES, ALLOWED)] == [41]


def test_connection_errors_are_not_swallowed():
    """네트워크 실패는 형식 문제가 아니다. DAG가 종류로 갈라야 한다."""
    subject, _ = picker(ConnectionError("read timeout"))

    with pytest.raises(ConnectionError):
        subject.pick(24, CANDIDATES, ALLOWED)
