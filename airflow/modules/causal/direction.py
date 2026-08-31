"""주간 인과 그래프를 대상별 방향성으로 접는다.

**LangChain을 import한다.** 그래서 `domain.py`가 이 모듈을 모르고, DAG 테스트와 순수 함수
테스트가 이 무게 없이 돈다(`generation.py`와 같은 자리다).

설계는 `docs/analysis/market-thesis/17-graph-query.md` §3이다. 프롬프트 문장은 코드가 아니라
`modules/prompts/causal_direction.yaml`에 있다.

## 왜 LLM인가 — 다수결로 안 갈려서다

2026-08-31 운영 실측에서 (주,대상) 13개 중 6개가 엇갈렸고 005930의 08-17 주는 `up` 4 /
`down` 4였다. `confidence`로도 안 갈렸다(`observed` 3 대 3, `endpoint_observed` 1 대 1).
SQL 집계로는 방향이 안 나오고, 그것이 이 흐름에 모델이 남는 유일한 이유다.

**모델이 만드는 것은 `bias` 하나와 문장 하나뿐이다.** 세기·경로 목록·채널 집계는 코드가
Cypher 결과에서 만든다(설계 §3.1). 저장소 규칙대로 숫자는 모델이 만들지 않는다.

## 대상 전부를 대화 하나로 본다

`generation.py`와 같은 판단이다. 쪼개면 "005930은 이익 기대가 받쳤는데 000660은 안 받쳤나"
같은 대조를 못 한다 — 실제로 두 종목이 같은 주장을 공유한다(설계 §6.9).

**그래서 일부만 온 응답은 실패다.** 온 것만 저장하면 나머지 대상이 "방향성 없음"으로 조용히
내려가고, 그것은 "그 주에 경로가 없었다"와 구별되지 않는다.
"""

import logging
from collections.abc import Sequence
from datetime import date
from typing import Any, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from modules import llm
from modules.causal.domain import DIRECTION_PROMPT_VERSION, MAX_DIRECTION_REASONING_CHARS, Direction

# `generation`에서 가져온다. 같은 일을 하는 함수가 이미 둘이라(`briefing/disclosure_picks._text`)
# 셋째를 만들지 않는다. 둘 다 LangChain을 import하는 흐름이라 무게가 늘지 않는다 — 셋째
# 소비자가 생기면 그때 `llm.py`로 올린다.
from modules.causal.generation import reply_text
from modules.graph_query import DirectionInput
from modules.prompt import read_prompt
from modules.schema import json_object, response_format

logger = logging.getLogger(__name__)

# import 시점에 읽고 검증한다. 칸이 빠지면 이 모듈을 쓰는 DAG이 DagBag 단계에서 죽는다.
PROMPTS = read_prompt("causal_direction")

BIAS_VALUES = ("up", "down", "mixed", "flat")


class DirectionError(RuntimeError):
    """모델이 쓸 수 있는 방향성을 끝내 내지 않았다. 다시 불러도 같은 결과다."""


class DirectionAnswer(BaseModel):
    """모델이 낸 대상 하나의 방향. 저장 전에 `verify` 가 거른다."""

    code: str = Field(description="대상 코드. 준 목록 안이어야 한다")
    bias: str = Field(description="up·down·mixed·flat 중 하나")
    reasoning: str = Field(description="어느 채널이 우위였는지 한 문장(한국어)")


class DirectionReply(BaseModel):
    directions: list[DirectionAnswer] = Field(description="받은 대상 전부. 하나도 빠뜨리지 않는다")


class DirectionState(TypedDict):
    messages: list[BaseMessage]
    inputs: dict[str, DirectionInput]
    directions: list[Direction] | None
    error: str | None
    attempts: int


def render_targets(inputs: Sequence[DirectionInput]) -> str:
    """대상들을 프롬프트에 실을 텍스트로 편다. **순수 함수다.**

    JSON이 아니라 텍스트인 이유는 이 값이 사람이 읽는 설계 문서의 예시와 같은 모양이어야
    하기 때문이다(설계 §4.3). 프롬프트 예산도 텍스트 쪽이 싸다.
    """
    blocks = []
    for found in inputs:
        lines = [f"### {found.code} ({found.kind})"]
        counts = f"착지한 주장 {len(found.landings)}개 — up {found.up_count} / down {found.down_count}"
        if found.flat_count:
            counts += f" / 그 밖 {found.flat_count}"
        lines.append(counts)
        if found.truncated:
            # 조용히 자르지 않는다. 모델이 "이것이 전부"로 읽으면 안 된다.
            lines.append("**행 상한에 걸려 일부만 보인다. 아래가 전부가 아니다.**")
        for landing in found.landings:
            lines.append(
                f"- {landing.source} → {landing.channel} → {found.code} "
                f"{landing.sign} ({landing.confidence}) · {landing.reasoning}"
            )
        if found.chains:
            lines.append("이어진 경로(앞 주의 결과가 다음 원인이 된 것):")
            for chain in found.chains:
                lines.append(f"- {' → '.join(chain.chain)} {chain.sign}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


class DirectionSummarizer:
    """방향성 요약 한 번. 컴파일된 그래프를 소유한다.

    **호출이 하나여도 그래프다.** `if`로 교정을 쓰면 트레이스에 이름 없는 호출만 남는다
    (저장소 규칙: `writing-llm-flows`).
    """

    def __init__(self, model: BaseChatModel) -> None:
        self._model = model
        self._schema = response_format(DirectionReply, "causal_direction")
        self._graph = self._build_graph()

    def summarize(self, inputs: Sequence[DirectionInput], *, week_start: date) -> tuple[Direction, ...]:
        """대상 전부의 방향성. 두 번째도 실패하면 `DirectionError`를 올린다."""
        if not inputs:
            # 그 주에 경로가 하나도 없었다. 부를 것이 없고 저장할 것도 없다.
            return ()
        state: DirectionState = {
            "messages": self.build_messages(inputs, week_start=week_start),
            "inputs": {found.code: found for found in inputs},
            "directions": None,
            "error": None,
            "attempts": 0,
        }
        final = self._graph.invoke(
            state,
            config={
                "run_name": "causal_direction",
                "tags": ["causal", "direction"],
                "metadata": {"week_start": week_start.isoformat(), "prompt_version": DIRECTION_PROMPT_VERSION},
            },
        )
        directions = final.get("directions")
        if directions is None:
            raise DirectionError(final.get("error") or "model returned no usable directions")
        return tuple(directions)

    @staticmethod
    def build_messages(inputs: Sequence[DirectionInput], *, week_start: date) -> list[BaseMessage]:
        system = PROMPTS.render("system", max_reasoning_chars=MAX_DIRECTION_REASONING_CHARS)
        human = PROMPTS.render(
            "instruction",
            week_start=week_start.isoformat(),
            targets=render_targets(inputs),
        )
        return [SystemMessage(system), HumanMessage(human)]

    def verify(self, reply: DirectionReply, inputs: dict[str, DirectionInput]) -> tuple[Direction, ...]:
        """모델의 답과 코드가 센 값을 합친다. 쓸 수 없으면 `DirectionError`.

        **대상이 하나라도 빠지면 실패다**(모듈 docstring). 목록 밖 코드는 버린다 — 툴 결과
        레지스트리와 같은 규칙이다.
        """
        by_code: dict[str, DirectionAnswer] = {}
        for answer in reply.directions:
            if answer.code not in inputs:
                logger.warning("dropping a direction for %s that was not asked for", answer.code)
                continue
            by_code[answer.code] = answer

        missing = sorted(set(inputs) - set(by_code))
        if missing:
            raise DirectionError(f"missing directions for {', '.join(missing)}")

        directions = []
        for code, found in inputs.items():
            answer = by_code[code]
            if answer.bias not in BIAS_VALUES:
                raise DirectionError(f"{code} has an unknown bias {answer.bias!r}")
            reasoning = answer.reasoning.strip()
            if not reasoning:
                raise DirectionError(f"{code} has no reasoning")
            directions.append(
                Direction(
                    target_kind=found.kind,
                    target_code=code,
                    week_start=found.week_start,
                    bias=answer.bias,
                    # 길면 그 건만 자른다. 상한은 프롬프트에도 실려 있다.
                    reasoning=reasoning[:MAX_DIRECTION_REASONING_CHARS].rstrip(),
                    up_count=found.up_count,
                    down_count=found.down_count,
                    flat_count=found.flat_count,
                    path_ids=found.path_ids,
                    channels=found.channel_counts,
                )
            )
        return tuple(directions)

    def _build_graph(self):
        graph = StateGraph(DirectionState)
        graph.add_node("call", self._call)
        graph.add_node("repair", self._repair)
        graph.add_edge(START, "call")
        graph.add_conditional_edges("call", self._next, {"repair": "repair", END: END})
        graph.add_edge("repair", "call")
        return graph.compile()

    def _call(self, state: DirectionState) -> dict[str, Any]:
        """스키마를 강제해 한 번 부른다. 제공처가 스키마를 안 받으면 그때만 한 번 더."""
        messages = state["messages"]
        try:
            reply = llm.invoke(self._model, messages, schema=self._schema)
        except llm.UnsupportedResponseFormat as error:
            logger.warning("provider does not accept a response schema; falling back to validation: %s", error)
            reply = llm.invoke(self._model, messages)

        try:
            parsed = DirectionReply.model_validate_json(json_object(reply_text(reply)))
            directions = self.verify(parsed, state["inputs"])
        except (ValueError, DirectionError) as error:
            return {"messages": [*messages, reply], "directions": None, "error": str(error)}
        return {"messages": [*messages, reply], "directions": list(directions), "error": None}

    def _repair(self, state: DirectionState) -> dict[str, Any]:
        logger.warning("retrying the causal direction once after %s", state["error"])
        return {
            "messages": [*state["messages"], HumanMessage(PROMPTS.render("repair", reason=state["error"] or ""))],
            "attempts": state["attempts"] + 1,
        }

    @staticmethod
    def _next(state: DirectionState) -> str:
        if state["directions"] is not None:
            return END
        # 교정은 한 번뿐이고 재시도는 Airflow가 한다.
        return "repair" if state["attempts"] == 0 else END
