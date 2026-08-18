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

## 여기서만 모델 실패를 삼킨다

`document_assessment_hourly`는 모델에 닿지 못한 실패를 그대로 올려 태스크를 죽인다. 거기서는
모델 출력이 산출물의 전부라 삼키면 "0건 처리" 성공이 된다. 브리핑은 반대다. 표가 본체이고
요약은 덧붙임이라, 요약이 없어도 리포트는 제 값을 한다.

**결정적인 이유는 재시도가 중복 발송을 만든다는 것이다.** 발송이 태스크의 마지막 단계라
요약 실패로 태스크를 죽이면 재시도가 같은 표를 한 번 더 채널에 보낸다. 요약 한 단락을
얻으려고 리포트를 두 번 보내는 것은 손해다.

대신 **조용히 빠지지 않는다.** 실패 사유가 Slack 메시지의 context 줄로 나가고 경고 로그가
남는다(`blocks.comment_blocks`). 그래서 이건 규칙이 금지하는 "조용한 성공"이 아니다.
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

**읽는 사람은 이미 표를 봤다.** 표에 있는 것을 문장으로 옮기면 아무 것도 보태지 않는다.
네 일은 표가 스스로 말하지 못하는 것을 말하는 것이다. 그건 셋 중 하나다.

1. **무엇이 이례적인가** — 여러 줄 중 어느 것이 평소와 다른가. 그 하나로 시작한다.
2. **무엇과 무엇이 엮이는가** — 같은 방향으로 움직인 것들, 반대로 갈린 것들. 표는 줄을
   따로 보여 줄 뿐 둘 사이를 말해 주지 않는다.
3. **무엇이 달라졌는가** — 며칠 이어지던 흐름이 꺾였거나 이어지고 있다면 그것.

## 하지 마라

- **줄을 훑어 나열하지 마라.** "A는 올랐고 B는 내렸고 C는 보합이었다"는 표를 다시 읽는 것이다.
- **움직이지 않은 것을 언급하지 마라.** "환율은 특별한 움직임이 없었다"는 지면 낭비다.
  조용했다는 사실 자체가 이례적일 때만(예: 큰 이벤트 뒤의 무반응) 쓴다.
- 입력에 없는 숫자를 만들지 마라. 뉴스·정책·실적 같은 바깥 사정을 지어내지 마라. 너는
  숫자만 받았고 원인은 모른다. **모르는 원인을 추측해 붙이지 마라.**
- 투자 조언, 매수·매도 권유, 목표가, 앞으로의 방향 예측을 쓰지 마라.

## 형태

- 한국어. 마크다운 제목(#)은 쓰지 않는다.
- **짧을수록 좋다.** 할 말이 하나면 한 문장으로 끝낸다. 길이를 채우려 하지 마라.
- 쓸 말이 정말 없으면 "특별히 눈에 띄는 움직임은 없다" 한 줄이 정답이다. 억지로 이야기를
  만드는 것보다 낫다.
- 숫자를 인용할 때는 그 숫자가 주장의 근거일 때만 쓴다. 장식으로 넣지 마라.

## trend를 읽는 법

값마다 붙은 `trend`는 그 움직임이 최근 구간에서 어느 정도인지를 미리 계산해 둔 것이다.
**무엇을 언급할지 고르는 데 쓴다.**

- `move_percentile`: 이번 변화의 크기가 구간 안에서 몇 번째인가(0~100). 80을 넘으면 평소보다
  큰 움직임이라 언급할 값어치가 있고, 40 아래면 조용한 날이라 굳이 쓰지 않는다.
- `streak_days`: 같은 방향이 이어진 날 수이고 부호가 방향이다(`-5`는 5일 연속 하락·순매도).
  사흘 넘게 이어졌으면 하루치 값보다 그 사실이 더 중요하다.
- `window_low`/`window_high`: 구간의 최저·최고. 현재 값이 어느 쪽 끝에 붙어 있으면 그것을 쓴다.
- `observations`: 표본 수. **`thin`이 true면 그 백분위를 근거로 쓰지 마라.** 표본이 짧아
  나온 순위는 이야기가 아니라 잡음이다. 그 계열은 값만 언급하거나 아예 넘어간다.
- `trend`가 null이면 비교할 이력이 없다는 뜻이다. 없는 추세를 지어내지 마라.

## 값이 언제 것인지 본다

`as_of_kst`가 붙은 값은 그 시각에 관측된 것이다. 리포트 기준 시각과 하루 넘게 벌어져 있으면
**"오늘"이라고 쓰지 말고 며칠 자 값인지 밝히거나 아예 언급하지 마라.** 장중 스냅샷은 시세보다
며칠 묵어 있을 수 있다.

## 좋은 예와 나쁜 예

나쁨 — 줄을 훑어 옮겼을 뿐이고, 마지막 문장은 아무 것도 말하지 않는다:

> 삼성전자와 SK하이닉스가 나흘 연속 올랐다. 코스피와 코스피200도 함께 올랐고 코스닥
> 오름폭은 작았다. 항셍은 나흘째 밀렸고 닛케이225는 올랐다. 미국 선물과 환율은 소폭
> 등락에 그쳐 특별한 움직임은 없었다.

좋음 — 이례적인 것 하나로 시작하고, 표가 못 하는 연결을 하고, 나머지는 버렸다:

> 지수는 올랐지만 하락 종목이 상승 종목보다 많다(코스피 452 대 394). 오름폭이 대형주
> 몇 개에 몰려 있다는 뜻이고, 실제로 삼성전자·SK하이닉스가 나흘째 상승 중이다.
> 같은 날 항셍은 한 달 최대 낙폭으로 밀리고 닛케이는 고점을 찍어 아시아가 갈렸다."""

INSTRUCTION = (
    "아래는 {report_name}의 집계 결과다. 표는 읽는 사람이 이미 봤다.\n"
    "이 값들에서 **표가 스스로 말하지 못하는 것**을 찾아 짧게 써라.\n\n```json\n{summary}\n```"
)


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
