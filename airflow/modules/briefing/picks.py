"""하루치 문서 목록에서 읽을 것과 주의할 것을 고르는 층.

**고르기는 상대 판단이라 목록을 함께 놓고 봐야 한다.** 그래서 하루치 후보를 한 번에 보여
주고 그 안에서 고르게 한다.

실측(2026-08-18, 24시간 449건)에서 점수 분포는 8점 6건, 7점 8건, 6점 20건, 5점 28건,
4점 48건, 3점 54건, 2점 224건, 1점 38건, 0점 23건이다. **점수는 잘 갈라진다.** 그러니 점수가
못 쓸 값이라서 선별을 붙이는 것이 아니다. 이유는 둘이다.

- **상위 구간은 거의 동점이다.** 후보 60건의 최저 점수가 5점이고 그 안에 5점만 28건이다.
  동점 구간의 순서는 `assessed_at`이 정하므로 사실상 최신순이다. 그 구간의 순위를 여기서 정한다.
- **점수가 답하지 않는 질문이 있다.** 무엇이 위험 쪽인지(`watch`), 왜 고를 값이 있는지(`why`),
  같은 사건을 여러 매체가 쓴 것 중 무엇 하나인지. 이 셋은 목록을 함께 놓고 봐야 답이 나온다.

`value_score`는 그대로 쓴다. 후보를 몇십 건으로 자르는 것이 점수의 몫이고, 그 안의 순위와
분류만 여기서 정한다.

## 문장은 여기 없다

프롬프트는 `modules/prompts/document_picks.yaml`이 갖는다. 문장을 고치는 일과 흐름을
고치는 일은 주기가 다르고, 섞어 두면 문장만 바꾼 변경도 코드 diff가 된다. 읽는 방법은
`modules/prompt.py`에 있다.

## 두 종류를 고른다

- **읽을 것**(`watch=false`): 오늘 알아 둘 값이 있는 문서.
- **주의**(`watch=true`): 위험 쪽으로 움직일 수 있어 눈여겨봐야 하는 문서.

건수는 범위로만 준다. 고정 개수를 요구하면 한산한 날 억지로 채우고, 그건 지금 점수가
천장에 몰린 것과 같은 문제를 다른 자리에서 반복하는 것이다.

## 목록 밖의 id는 버린다

모델이 없는 `document_id`를 지어내면 그 건만 버리고 나머지는 보낸다. `assessment.filter_tags`가
마스터에 없는 태그만 버리는 것과 같은 규칙이다. 전부 버려지면 그건 형식 실패라 한 번
교정을 요청한다.

## 실패는 리포트를 막지 않는다

부르는 쪽(DAG)이 `PickError`를 잡아 점수 정렬 상위 몇 건으로 떨어진다. 발송이 태스크의
마지막 단계라 여기서 태스크를 죽이면 재시도가 같은 표를 한 번 더 채널에 보낸다.
`comment.py`의 판단과 같은 이유다.
"""

import logging
from typing import Any, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, ValidationError

from modules import llm
from modules.llm import UnsupportedResponseFormat
from modules.prompt import read_prompt
from modules.schema import SchemaError, json_object, response_format

logger = logging.getLogger(__name__)

# 한 번에 그릴 수 있는 상한. 모델에는 범위로 지시하고 여기서 잘라 낸다.
MAX_READS = 5
MAX_WATCHES = 3

# 고른 이유 한 문장의 상한. 넘으면 그 건만 잘라 낸다.
MAX_WHY_CHARS = 200

PROMPTS = read_prompt("document_picks")

SYSTEM_PROMPT = PROMPTS.render(
    "system", max_reads=MAX_READS, max_watches=MAX_WATCHES, number_style=llm.NUMBER_STYLE
)
REPAIR_INSTRUCTION = PROMPTS.repair


class PickError(RuntimeError):
    """모델이 쓸 수 있는 선별 결과를 내지 않았다."""


class Pick(BaseModel):
    """고른 문서 한 건. `document_id`는 후보 목록 안의 값이다."""

    model_config = ConfigDict(frozen=True)

    document_id: int
    why: str = ""
    watch: bool = False


class Picks(BaseModel):
    """모델 응답. 스키마를 강제하되 강제가 안 되는 제공처를 위해 검증도 남긴다."""

    model_config = ConfigDict(frozen=True)

    picks: tuple[Pick, ...] = ()


class PickState(TypedDict):
    """선별 한 번의 상태. 설정 객체는 넣지 않는다. 상태는 트레이스 입력으로 나간다.

    `allowed_ids`는 후보 문서의 id다. 노드가 응답을 거를 때 쓰므로 상태에 있어야 한다.
    """

    messages: list[BaseMessage]
    allowed_ids: frozenset[int]
    picks: tuple[Pick, ...] | None
    error: str | None
    attempts: int


class DocumentPicker:
    """후보 목록을 받아 읽을 것과 주의할 것을 고른다.

    흐름은 `call` → (형식이 깨지면) `repair` → `call`이다.
    교정은 한 번뿐이다.
    """

    def __init__(self, model: BaseChatModel) -> None:
        self._model = model
        self._schema = response_format(Picks, "document_picks")
        self._graph = self._build_graph()

    @staticmethod
    def build_messages(window_hours: float, candidates_json: str) -> list[BaseMessage]:
        return [
            SystemMessage(SYSTEM_PROMPT),
            HumanMessage(
                PROMPTS.render("instruction", window_hours=f"{window_hours:g}", candidates=candidates_json)
            ),
        ]

    @staticmethod
    def parse(raw: str, allowed_ids: frozenset[int]) -> tuple[Pick, ...]:
        """응답을 검증하고 목록 밖의 id를 버린다.

        전부 버려지면 `PickError`다. 그건 모델이 후보를 안 보고 답했다는 뜻이라 교정을
        요청할 값어치가 있다. 반대로 모델이 **아무 것도 고르지 않은 것**(`picks: []`)은
        정상 응답이다. 한산한 날이 그렇다.
        """
        try:
            parsed = Picks.model_validate_json(json_object(raw))
        except SchemaError as error:
            raise PickError(str(error)) from error
        except ValidationError as error:
            raise PickError(f"Model returned an unusable object: {error}") from error

        kept = [pick for pick in parsed.picks if pick.document_id in allowed_ids]
        if parsed.picks and not kept:
            raise PickError(f"Model picked {len(parsed.picks)} documents, none of them from the candidate list")
        dropped = len(parsed.picks) - len(kept)
        if dropped:
            logger.warning("dropped %s picks that were not in the candidate list", dropped)

        return _limit([_shorten(pick) for pick in kept])

    def pick(self, window_hours: float, candidates_json: str, allowed_ids: frozenset[int]) -> tuple[Pick, ...]:
        """고른 문서들. 두 번째도 실패하면 `PickError`를 올린다.

        부르는 쪽(DAG)은 이 실패를 잡아 점수 정렬로 떨어진다. 선별이 없다고 리포트를
        멈추지 않는다.
        """
        state: PickState = {
            "messages": self.build_messages(window_hours, candidates_json),
            "allowed_ids": allowed_ids,
            "picks": None,
            "error": None,
            "attempts": 0,
        }
        final = self._graph.invoke(
            state,
            config={"run_name": "document_picks", "metadata": {"candidates": len(allowed_ids)}},
        )
        picks = final.get("picks")
        if picks is None:
            raise PickError(final.get("error") or "Model did not return any picks")
        return picks

    def _build_graph(self):
        graph = StateGraph(PickState)
        graph.add_node("call", self._call)
        graph.add_node("repair", self._repair)
        graph.add_edge(START, "call")
        graph.add_conditional_edges("call", self._next, {"repair": "repair", END: END})
        graph.add_edge("repair", "call")
        return graph.compile()

    def _call(self, state: PickState) -> dict[str, Any]:
        """스키마를 강제해 한 번 부른다. 제공처가 스키마를 안 받으면 그때만 한 번 더."""
        messages = state["messages"]
        try:
            reply = llm.invoke(self._model, messages, schema=self._schema)
        except UnsupportedResponseFormat as error:
            logger.warning("provider does not accept a response schema; falling back to validation: %s", error)
            reply = llm.invoke(self._model, messages)

        try:
            picks = self.parse(_text(reply), state["allowed_ids"])
        except PickError as error:
            return {"messages": [*messages, reply], "picks": None, "error": str(error)}
        return {"messages": [*messages, reply], "picks": picks, "error": None}

    def _repair(self, state: PickState) -> dict[str, Any]:
        logger.warning("retrying the document picks once after %s", state["error"])
        return {
            "messages": [*state["messages"], HumanMessage(REPAIR_INSTRUCTION)],
            "attempts": state["attempts"] + 1,
        }

    @staticmethod
    def _next(state: PickState) -> str:
        if state["picks"] is not None:
            return END
        return "repair" if state["attempts"] == 0 else END


def _shorten(pick: Pick) -> Pick:
    """이유가 길면 자른다. 그 한 건 때문에 전체를 버리지 않는다."""
    why = pick.why.strip()
    if len(why) > MAX_WHY_CHARS:
        why = why[:MAX_WHY_CHARS].rstrip() + "…"
    return pick.model_copy(update={"why": why})


def _limit(picks: list[Pick]) -> tuple[Pick, ...]:
    """모델이 범위를 넘겨 골랐을 때 앞에서부터 자른다. 순서는 모델이 정한 대로다."""
    reads = [pick for pick in picks if not pick.watch]
    watches = [pick for pick in picks if pick.watch]
    return (*reads[:MAX_READS], *watches[:MAX_WATCHES])


def _text(reply: Any) -> str:
    """응답 본문. 제공처에 따라 문자열이 아니라 조각 리스트로 온다."""
    content = reply.content
    if isinstance(content, str):
        return content
    return "".join(part if isinstance(part, str) else part.get("text", "") for part in content)
