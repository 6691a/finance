"""급변의 사후 원인 분석. 저장소의 표준 호출–교정 그래프다.

설계는 `docs/analysis/market-shock-capture.md` §7이다.

## 툴이 없다

볼 것이 "포착 시각 이후의 문서와 검색 결과"로 이미 정해져 있어서 모델이 고를 것이 없다.
그래서 노드가 둘이다.

    START → call → (조건부) repair → call → END

`_call`이 묻고 **검증까지 한다.** 쓸 것이 없으면 빈 결과를 상태에 남긴다. `_next`가
`attempts == 0`일 때만 `repair`로 보낸다 — **교정은 한 번뿐이고 재시도는 Airflow가 한다.**

## 판정은 코드가 한다

모델은 값만 낸다. 준 목록 밖의 id·인덱스를 버리는 것, 남은 근거가 0이면 답을 통째로
내리는 것, 문장 길이를 자르는 것이 전부 여기 있다.

**"근거 중 최소 하나는 사건 이후"라는 규칙은 두지 않는다.** 조회 창의 하한이 이미 포착
시각이라(`shock_documents/select_after_event.sql`, `search.collect`) 검증에서 다시 보면
같은 규칙이 두 곳에 산다.
"""

import json
import logging
import operator
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from modules.llm import invoke, model_name, shock_model
from modules.prompt import read_prompt
from modules.schema import json_object, response_format
from modules.shock.domain import (
    CAUSE_PROMPT_VERSION,
    MAX_CAUSE_CHARS,
    MAX_DOCUMENTS,
    MAX_SEARCH_RESULTS,
    CauseAnswer,
    CauseInput,
    CauseKind,
    Direction,
)
from modules.shock.render import render_cause_prompt_blocks

logger = logging.getLogger(__name__)

PROMPT = read_prompt("shock_cause")
RESPONSE_FORMAT = response_format(CauseAnswer, "shock_cause")

DIRECTION_LABELS = {Direction.DROP: "급락", Direction.SURGE: "급등"}


class _State(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], operator.add]
    answer: CauseAnswer | None
    rejected: Annotated[list[str], operator.add]
    attempts: int


class ShockCauseBuilder:
    """그래프를 소유한다. 생성자에서 한 번 컴파일한다."""

    def __init__(self) -> None:
        self._model = shock_model()
        graph = StateGraph(_State)
        graph.add_node("call", self._call)
        graph.add_node("repair", self._repair)
        graph.add_edge(START, "call")
        graph.add_conditional_edges("call", self._next, {"repair": "repair", END: END})
        graph.add_edge("repair", "call")
        self._graph = graph.compile()

    @property
    def model_name(self) -> str:
        return model_name(self._model)

    def run(self, payload: CauseInput) -> tuple[CauseAnswer, list[str]]:
        """한 이벤트의 원인을 묻는다. 답과 **버린 사유**를 함께 돌려준다.

        버린 사유가 비어 있지 않은데 답이 `found=False`면 "모델이 답했으나 못 쓴 것"이고,
        둘 다 비면 "모델이 스스로 못 찾았다"다. 원장이 그 둘을 갈라야 한다.
        """
        self._payload = payload
        state = self._graph.invoke(
            {
                "messages": [
                    SystemMessage(content=PROMPT.system),
                    HumanMessage(content=self._instruction(payload)),
                ],
                "answer": None,
                "rejected": [],
                "attempts": 0,
            },
            config={
                "run_name": "shock_cause",
                "tags": ["shock", "cause"],
                # 자격 증명은 안 싣는다. 트레이스 입력으로 나간다.
                "metadata": {
                    "shock_event_id": payload.shock_event_id,
                    "attempt": payload.attempt,
                    "prompt_version": CAUSE_PROMPT_VERSION,
                },
            },
        )
        answer = state.get("answer") or CauseAnswer(found=False)
        return answer, list(state.get("rejected") or [])

    @staticmethod
    def _instruction(payload: CauseInput) -> str:
        blocks = render_cause_prompt_blocks(payload)
        return PROMPT.render(
            "instruction",
            symbol=payload.symbol,
            direction_label=DIRECTION_LABELS[payload.direction],
            detected_kst=blocks["detected_kst"],
            detected_hhmm=blocks["detected_hhmm"],
            extreme_kst=blocks["extreme_kst"],
            extreme_price=blocks["extreme_price"],
            trigger_price=blocks["trigger_price"],
            move_pct=payload.move_pct,
            window_change_pct=blocks["window_change_pct"],
            peers=blocks["peers"],
            attempt_no=payload.attempt,
            as_of_kst=blocks["as_of_kst"],
            deadline=blocks["deadline"],
            document_count=len(payload.documents),
            documents=blocks["documents"],
            search_count=len(payload.search_hits),
            search_hits=blocks["search_hits"],
            max_documents=MAX_DOCUMENTS,
            max_search_results=MAX_SEARCH_RESULTS,
            max_cause_chars=MAX_CAUSE_CHARS,
        )

    def _call(self, state: _State) -> dict[str, Any]:
        message = invoke(self._model, state["messages"], schema=RESPONSE_FORMAT)
        answer, rejected = self._verify(message)
        return {
            "messages": [message],
            "answer": answer,
            "rejected": rejected,
            "attempts": state.get("attempts", 0) + 1,
        }

    def _repair(self, state: _State) -> dict[str, Any]:
        rejected = "\n".join(f"- {reason}" for reason in state.get("rejected") or [])
        return {"messages": [HumanMessage(content=PROMPT.render("repair", rejected=rejected))]}

    @staticmethod
    def _next(state: _State) -> str:
        answer = state.get("answer")
        # 모델이 스스로 "못 찾았다"고 한 것은 정상 결과다. 되묻지 않는다 — 되물으면
        # 없는 답을 만들라고 압박하는 것이 된다.
        if answer is not None and (answer.found or not state.get("rejected")):
            return END
        return "repair" if state.get("attempts", 0) < 2 else END

    def _verify(self, message: AIMessage) -> tuple[CauseAnswer | None, list[str]]:
        """**판정은 코드가 한다.** 준 목록 밖의 근거를 버리고 사유를 올린다."""
        rejected: list[str] = []
        raw = message.content if isinstance(message.content, str) else json.dumps(message.content)
        try:
            answer = CauseAnswer.model_validate_json(json_object(raw))
        except ValueError as error:
            return None, [f"응답이 스키마와 다르다: {error}"]

        if not answer.found:
            return CauseAnswer(found=False), rejected

        allowed_documents = {document.id for document in self._payload.documents}
        allowed_indexes = set(range(1, len(self._payload.search_hits) + 1))

        documents = [value for value in answer.document_ids if value in allowed_documents]
        for value in answer.document_ids:
            if value not in allowed_documents:
                rejected.append(f"문서 id {value}는 준 목록에 없다")

        indexes = [value for value in answer.search_indexes if value in allowed_indexes]
        for value in answer.search_indexes:
            if value not in allowed_indexes:
                rejected.append(f"검색 번호 {value}는 준 범위(1~{len(allowed_indexes)}) 밖이다")

        if not documents and not indexes:
            rejected.append("근거가 하나도 안 남았다")
            return CauseAnswer(found=False), rejected

        text = answer.cause_text.strip()
        if not text:
            rejected.append("원인 문장이 비었다")
            return CauseAnswer(found=False), rejected
        if len(text) > MAX_CAUSE_CHARS:
            rejected.append(f"원인 문장이 {MAX_CAUSE_CHARS}자를 넘어 잘랐다")
            text = text[:MAX_CAUSE_CHARS]

        return (
            CauseAnswer(
                found=True,
                cause_text=text,
                cause_kind=answer.cause_kind or CauseKind.UNCLEAR,
                document_ids=documents,
                search_indexes=indexes,
            ),
            rejected,
        )
