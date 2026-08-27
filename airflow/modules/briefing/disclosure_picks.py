"""방금 올라온 공시 중 무엇을 강조할지 고르는 층.

**여기가 무거운 쪽이다.** LangChain·LangGraph를 모듈 수준에서 import하므로 DAG 파일이
이것을 최상단에서 끌고 오면 DagBag 30초 타임아웃에 걸린다. 조회·렌더는
`disclosures.py`에 있고 그쪽은 LangChain을 모른다. `documents.py`/`picks.py`와 같은 경계다.

## 문장은 여기 없다

프롬프트는 `modules/prompts/disclosure_picks.yaml`이 갖는다. 문장을 고치는 일과 흐름을
고치는 일은 주기가 다르고, 섞어 두면 문장만 바꾼 변경도 코드 diff가 된다. 읽는 방법은
`modules/prompt.py`에 있다.

## 고르는 것이 아니라 강조하는 것이다

`picks.DocumentPicker`는 후보 수십 건에서 몇 개를 **골라 싣는다.** 여기는 다르다.
`disclosure_event`에 들어오는 것이 삼성전자·SK하이닉스 둘뿐이라 한 창의 후보가 한두 건이고
버릴 이유가 없다. 공시는 전부 실리고 모델은 **무엇에 별을 붙일지와 그 이유**만 정한다.

## 실패가 알림을 막지 않는다

부르는 쪽(DAG)이 `HighlightError`를 잡아 강조 없이 목록을 보낸다. 발송이 태스크의 마지막
단계라 여기서 태스크를 죽이면 재시도가 같은 공시를 한 번 더 채널에 보낸다.

그래서 `briefing_model()`의 키가 무효인 구간에도 이 알림은 죽지 않는다. 강조만 빠진다.
"""

import logging
from typing import Any, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from modules import llm
from modules.briefing.disclosures import MAX_REASON_CHARS, Highlight, HighlightError, Highlights
from modules.llm import UnsupportedResponseFormat
from modules.prompt import read_prompt
from modules.schema import SchemaError, json_object, response_format

logger = logging.getLogger(__name__)

PROMPTS = read_prompt("disclosure_picks")

SYSTEM_PROMPT = PROMPTS.render("system", max_reason_chars=MAX_REASON_CHARS, number_style=llm.NUMBER_STYLE)
REPAIR_INSTRUCTION = PROMPTS.repair


class HighlightState(TypedDict):
    """강조 한 번의 상태. 설정 객체는 넣지 않는다. 상태는 트레이스 입력으로 나간다.

    `allowed_ids`는 후보 공시의 접수번호다. 노드가 응답을 거를 때 쓰므로 상태에 있어야 한다.
    """

    messages: list[BaseMessage]
    allowed_ids: frozenset[str]
    highlights: tuple[Highlight, ...] | None
    error: str | None
    attempts: int


class DisclosurePicker:
    """공시 목록을 받아 무엇을 강조할지 고른다.

    흐름은 `call` → (형식이 깨지면) `repair` → `call`이다. 교정은 한 번뿐이다.
    """

    def __init__(self, model: BaseChatModel) -> None:
        self._model = model
        self._schema = response_format(Highlights, "disclosure_highlights")
        self._graph = self._build_graph()

    @staticmethod
    def build_messages(candidates_json: str) -> list[BaseMessage]:
        return [
            SystemMessage(SYSTEM_PROMPT),
            HumanMessage(PROMPTS.render("instruction", candidates=candidates_json)),
        ]

    @staticmethod
    def parse(raw: str, allowed_ids: frozenset[str]) -> tuple[Highlight, ...]:
        """응답을 검증하고 목록 밖의 접수번호를 버린다.

        전부 버려지면 `HighlightError`다. 모델이 후보를 안 보고 답했다는 뜻이라 교정을
        요청할 값어치가 있다. 반대로 **아무 것도 강조하지 않은 것**(`highlights: []`)은
        정상 응답이다. 정기 보고만 올라온 창이 그렇다.
        """
        try:
            parsed = Highlights.model_validate_json(json_object(raw))
        except SchemaError as error:
            raise HighlightError(str(error)) from error
        except ValidationError as error:
            raise HighlightError(f"Model returned an unusable object: {error}") from error

        kept = [highlight for highlight in parsed.highlights if highlight.rcept_no in allowed_ids]
        if parsed.highlights and not kept:
            raise HighlightError(
                f"Model highlighted {len(parsed.highlights)} disclosures, none of them from the candidate list"
            )
        dropped = len(parsed.highlights) - len(kept)
        if dropped:
            logger.warning("dropped %s highlights that were not in the candidate list", dropped)

        return tuple(_shorten(highlight) for highlight in kept)

    def highlight(self, candidates_json: str, allowed_ids: frozenset[str]) -> tuple[Highlight, ...]:
        """강조할 공시들. 두 번째도 실패하면 `HighlightError`를 올린다."""
        state: HighlightState = {
            "messages": self.build_messages(candidates_json),
            "allowed_ids": allowed_ids,
            "highlights": None,
            "error": None,
            "attempts": 0,
        }
        final = self._graph.invoke(
            state,
            config={"run_name": "disclosure_highlights", "metadata": {"candidates": len(allowed_ids)}},
        )
        highlights = final.get("highlights")
        if highlights is None:
            raise HighlightError(final.get("error") or "Model did not return any highlights")
        return highlights

    def _build_graph(self):
        graph = StateGraph(HighlightState)
        graph.add_node("call", self._call)
        graph.add_node("repair", self._repair)
        graph.add_edge(START, "call")
        graph.add_conditional_edges("call", self._next, {"repair": "repair", END: END})
        graph.add_edge("repair", "call")
        return graph.compile()

    def _call(self, state: HighlightState) -> dict[str, Any]:
        """스키마를 강제해 한 번 부른다. 제공처가 스키마를 안 받으면 그때만 한 번 더."""
        messages = state["messages"]
        try:
            reply = llm.invoke(self._model, messages, schema=self._schema)
        except UnsupportedResponseFormat as error:
            logger.warning("provider does not accept a response schema; falling back to validation: %s", error)
            reply = llm.invoke(self._model, messages)

        try:
            highlights = self.parse(_text(reply), state["allowed_ids"])
        except HighlightError as error:
            return {"messages": [*messages, reply], "highlights": None, "error": str(error)}
        return {"messages": [*messages, reply], "highlights": highlights, "error": None}

    def _repair(self, state: HighlightState) -> dict[str, Any]:
        logger.warning("retrying the disclosure highlights once after %s", state["error"])
        return {
            "messages": [*state["messages"], HumanMessage(REPAIR_INSTRUCTION)],
            "attempts": state["attempts"] + 1,
        }

    @staticmethod
    def _next(state: HighlightState) -> str:
        if state["highlights"] is not None:
            return END
        return "repair" if state["attempts"] == 0 else END


def _shorten(highlight: Highlight) -> Highlight:
    """이유가 길면 그 건만 잘라 낸다. 상한은 프롬프트에도 실려 있다."""
    if len(highlight.reason) <= MAX_REASON_CHARS:
        return highlight
    return highlight.model_copy(update={"reason": highlight.reason[:MAX_REASON_CHARS].rstrip()})


def _text(reply: Any) -> str:
    """응답 본문. 제공처에 따라 문자열이 아니라 조각 리스트로 온다."""
    content = reply.content
    if isinstance(content, str):
        return content
    return "".join(part if isinstance(part, str) else part.get("text", "") for part in content)
