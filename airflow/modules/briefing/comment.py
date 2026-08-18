"""집계 결과를 읽고 요약 한 편을 쓰는 층.

**숫자는 SQL이 만들고 LLM은 옮긴다.** 모델에 들어가는 것은 집계가 끝난 요약의 JSON뿐이다.
원시 행도, SQL도, 툴도 주지 않는다. 그래야 리포트가 데이터를 보지 않고도 그럴듯하게 들리는
글이 되지 않는다.

흐름은 `DocumentAssessor`의 축소판이다. 노드는 둘이다.

- `call`: 한 번 부르고 응답을 검증한다.
- `repair`: 빈 응답이나 너무 긴 응답이 왔을 때 교정 지시를 붙인다. **한 번만** 붙는다.

**요약의 길이와 모양은 모델이 정한다.** 단락 수를 세지 않고 상한만 본다. 표가 이미 값을
보여 주므로 요약이 몇 문장이어야 하는지는 그날 데이터가 정할 일이다.

응답이 평문이라 `response_format` 스키마는 걸지 않는다. 강제할 모양이 없다.
"""

import logging
from typing import Any, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from modules import llm

logger = logging.getLogger(__name__)

# 채널에서 스크롤 없이 읽히는 길이의 상한. 넘으면 한 번 교정을 요청한다.
MAX_COMMENT_CHARS = 1200

REPAIR_INSTRUCTION = f"이전 응답을 쓸 수 없다. {MAX_COMMENT_CHARS}자 이내의 한국어 요약을 다시 출력하라."

SYSTEM_PROMPT = """너는 시장 데이터 브리핑에 요약을 붙이는 애널리스트다.

- 입력으로 받은 집계 숫자만 근거로 쓴다. **입력에 없는 숫자를 만들지 마라.**
- 표가 이미 값을 보여 준다. 값을 다시 나열하지 말고 눈에 띄는 변화와 그 맥락만 적는다.
- 길이와 모양은 내용에 맞춘다. 문장 몇 개여도 되고 불릿이어도 된다. 쓸 말이 적으면 짧게 쓴다.
- 투자 조언, 매수·매도 권유, 목표가를 쓰지 마라.
- 한국어로 쓰고 마크다운 제목(#)은 쓰지 않는다."""

INSTRUCTION = "아래는 {report_name}의 집계 결과다. 이 값들을 읽고 요약을 써라.\n\n```json\n{summary}\n```"


class CommentError(RuntimeError):
    """모델이 쓸 수 있는 요약을 내지 않았다."""


class CommentState(TypedDict):
    """요약 한 편을 얻는 동안의 상태.

    설정 객체를 여기 넣지 않는다. 상태는 트레이스 입력으로 나간다.
    """

    messages: list[BaseMessage]
    comment: str | None
    error: str | None
    attempts: int


class BriefingCommentator:
    """집계 요약을 받아 브리핑 요약을 쓴다. 세 리포트가 같은 것을 쓴다."""

    def __init__(self, model: BaseChatModel) -> None:
        self._model = model
        self._graph = self._build_graph()

    @staticmethod
    def build_messages(report_name: str, summary_json: str) -> list[BaseMessage]:
        return [
            SystemMessage(SYSTEM_PROMPT),
            HumanMessage(INSTRUCTION.format(report_name=report_name, summary=summary_json)),
        ]

    @staticmethod
    def parse(raw: str) -> str:
        """모델 응답을 검증한다. 빈 응답과 너무 긴 응답만 거른다."""
        text = raw.strip()
        if not text:
            raise CommentError("Model returned an empty comment")
        if len(text) > MAX_COMMENT_CHARS:
            raise CommentError(f"Model returned {len(text)} chars, over the {MAX_COMMENT_CHARS} limit")
        return text

    def comment(self, report_name: str, summary_json: str) -> str:
        """요약 한 편. 두 번째도 실패하면 `CommentError`를 올린다.

        부르는 쪽(DAG)은 이 실패를 잡아 요약 없이 리포트를 보낸다. 요약이 없다고 리포트를
        멈추지 않는다.
        """
        state: CommentState = {
            "messages": self.build_messages(report_name, summary_json),
            "comment": None,
            "error": None,
            "attempts": 0,
        }
        final = self._graph.invoke(state, config={"run_name": "briefing_comment", "metadata": {"report": report_name}})
        text = final.get("comment")
        if text is None:
            raise CommentError(final.get("error") or "Model did not return a comment")
        return text

    def _build_graph(self):
        graph = StateGraph(CommentState)
        graph.add_node("call", self._call)
        graph.add_node("repair", self._repair)
        graph.add_edge(START, "call")
        graph.add_conditional_edges("call", self._next, {"repair": "repair", END: END})
        graph.add_edge("repair", "call")
        return graph.compile()

    def _call(self, state: CommentState) -> dict[str, Any]:
        messages = state["messages"]
        reply = llm.invoke(self._model, messages)
        try:
            return {"messages": [*messages, reply], "comment": self.parse(_text(reply)), "error": None}
        except CommentError as error:
            return {"messages": [*messages, reply], "comment": None, "error": str(error)}

    def _repair(self, state: CommentState) -> dict[str, Any]:
        logger.warning("retrying the briefing comment once after %s", state["error"])
        return {
            "messages": [*state["messages"], HumanMessage(REPAIR_INSTRUCTION)],
            "attempts": state["attempts"] + 1,
        }

    @staticmethod
    def _next(state: CommentState) -> str:
        if state["comment"] is not None:
            return END
        return "repair" if state["attempts"] == 0 else END


def _text(reply: Any) -> str:
    """응답 본문. 제공처에 따라 문자열이 아니라 조각 리스트로 온다."""
    content = reply.content
    if isinstance(content, str):
        return content
    return "".join(part if isinstance(part, str) else part.get("text", "") for part in content)
