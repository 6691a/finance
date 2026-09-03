"""전망과 관찰을 만드는 두 그래프.

흐름은 LangGraph `StateGraph`이고 그래프는 생성자에서 한 번 compile한다. 모양이 둘 다 같다 —
저장소 기준형에 툴이 붙은 것이다.

```
investigate → (조건부) tools → answer → (조건부) repair → answer
```

- `investigate`는 툴만 바인딩한다. `answer`는 툴을 빼고 `response_format`을 강제한다.
  한 요청에 둘을 섞지 않는다(`llm.invoke`가 그것을 막는다).
- **교정은 한 번뿐이고 재시도는 Airflow가 한다.**
- **슬롯으로 분기하지 않는다.** 슬롯은 값으로 흘러 관측 상태와 지시문을 고를 뿐이다.

**저장은 여기 없다.** 만든 초안을 쓰는 것은 `kospi/store.py`와 `kospi/graph.py`다 —
트랜잭션 경계를 부르는 쪽이 쥐어야 한다.

**문장도 여기 없다.** 프롬프트는 `modules/prompts/kospi_forecast.yaml`·`kospi_review.yaml`이
갖는다. 고치면 `kospi.domain`의 판을 올리고 `tests/modules/test_prompt_versions.py`의 해시를
같은 커밋에서 바꾼다.

## 검증이 이 흐름의 값어치다

모델이 값을 돌려줬다고 그 값이 맞는 것이 아니다. 이유가 요인을 인용하면 **이번 실행에서 그
요인을 툴로 봤거나 관측 상태에 있어야** 남긴다. 관찰도 같다 — 툴로 조회하지 않은 요인의
관찰은 버린다. 버린 건수는 원장에 남는다(로그로만 남기면 분모가 사라진다).
"""

import logging
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from modules import llm
from modules.kospi.domain import (
    MAX_BAND_PCT,
    MAX_EXPECTED_CHANGE_PCT,
    MAX_MEMORY_CHARS,
    MAX_NOTE_CHARS,
    MAX_REASON_CHARS,
    MAX_STATEMENT_CHARS,
    MAX_STRENGTH,
    MAX_TOOL_CALLS,
    MAX_TOOL_ROUNDS,
    MIN_BAND_PCT,
    MIN_STRENGTH,
    OBSERVATION_REQUIRED_PCT,
    Direction,
    Factor,
    KospiError,
    MemoryVerdict,
    ObservationSign,
    RunSlot,
    normalize_text,
    quantize_change,
)
from modules.kospi.state import ObservedState, ReviewState
from modules.kospi.toolbox import KospiToolbox, tool_node
from modules.llm import UnsupportedResponseFormat
from modules.prompt import json_dump, read_prompt
from modules.schema import SchemaError, json_object, response_format

logger = logging.getLogger(__name__)

FORECAST_PROMPTS = read_prompt("kospi_forecast")
REVIEW_PROMPTS = read_prompt("kospi_review")

# 출력 금지어. **프롬프트에만 있는 금지는 가드레일이 아니다** — 코드가 안 보면 어겼는지도
# 모른다. 투자 조언·매매 권유·목표가가 섞인 이유 문장은 버리고 건수를 센다.
#
# 완벽한 검사가 아니고 그래도 된다. 여기서 막는 것은 "명백한 것"이고, 애매한 것은 프롬프트가
# 맡는다 — 넓게 잡으면 정상 문장("매수세가 유입됐다")까지 사라진다.
FORBIDDEN_PHRASES = ("목표가", "매수 추천", "매도 추천", "매수를 권", "매도를 권", "투자 의견", "비중 확대 권")

# wire 스키마 경계. Pydantic 검증이 아니라 제공처에 보내는 JSON Schema에만 나간다.
#
# **Pydantic 검증은 느슨하게 둔다.** 여기서 조이면 크기 하나가 규칙을 어긴 순간 답 전체가
# `ValidationError`가 되어 이유까지 사라진다. 정합성은 정규화가 그 칸만 버리는 것으로 본다.
CHANGE_BOUNDS = {"minimum": -float(MAX_EXPECTED_CHANGE_PCT), "maximum": float(MAX_EXPECTED_CHANGE_PCT)}
BAND_BOUNDS = {"minimum": float(MIN_BAND_PCT), "maximum": float(MAX_BAND_PCT)}


# ---------------------------------------------------------------------------
# 모델이 내는 것 (검증 전)
# ---------------------------------------------------------------------------


class ReasonAnswer(BaseModel):
    """이유 하나. **셋 중 하나로 근거를 가리킨다** — 요인, 메모, 오늘 앞 슬롯."""

    model_config = ConfigDict(frozen=True)

    factor: str | None = None
    memory_id: int | None = None
    slot_ref: str | None = None
    direction: Literal["up", "down"]
    statement: str = ""


class ForecastAnswer(BaseModel):
    """전망 하나. **확률이 없다**(`domain.Direction` 참고)."""

    model_config = ConfigDict(frozen=True)

    direction: Literal["up", "down"]
    expected_change_pct: float = Field(json_schema_extra=CHANGE_BOUNDS)
    band_pct: float = Field(json_schema_extra=BAND_BOUNDS)
    reasons: tuple[ReasonAnswer, ...] = ()


class ObservationAnswer(BaseModel):
    """오늘 그 요인이 코스피와 어떻게 움직였나."""

    model_config = ConfigDict(frozen=True)

    factor: str
    sign: Literal["same", "inverse"]
    strength: int = Field(ge=MIN_STRENGTH, le=MAX_STRENGTH)
    note: str = ""


class MemoryAnswer(BaseModel):
    """새 메모 하나."""

    model_config = ConfigDict(frozen=True)

    text: str = ""
    factor: str | None = None
    reason: str = ""


class MemoryReviewAnswer(BaseModel):
    """활성 메모 하나에 대한 판정."""

    model_config = ConfigDict(frozen=True)

    id: int
    verdict: Literal["keep", "drop"]
    reason: str = ""


class ReviewAnswer(BaseModel):
    """장후 관찰 하나. 관찰·새 메모·메모 판정이 한 답에 온다.

    셋을 한 호출에 묶는 이유는 **같은 것을 보고 내는 판단**이기 때문이다. 나누면 오늘 종가와
    관계 표를 두 번 싣게 되고 비용이 두 배다.
    """

    model_config = ConfigDict(frozen=True)

    observations: tuple[ObservationAnswer, ...] = ()
    memories: tuple[MemoryAnswer, ...] = ()
    memory_reviews: tuple[MemoryReviewAnswer, ...] = ()


# ---------------------------------------------------------------------------
# 검증을 마친 것
# ---------------------------------------------------------------------------


class Reason(BaseModel):
    """검증을 통과한 이유 하나. 그대로 `kospi_forecast.reasons` JSONB의 항목이 된다."""

    model_config = ConfigDict(frozen=True)

    direction: Direction
    statement: str
    factor: Factor | None = None
    memory_id: int | None = None
    slot_ref: RunSlot | None = None


class ForecastDraft(BaseModel):
    """검증·정규화를 마친 전망. 그대로 `kospi_forecast` 행이 된다."""

    model_config = ConfigDict(frozen=True)

    direction: Direction
    expected_change_pct: Decimal
    band_pct: Decimal
    # **개수 상한이 없다.** 모델이 본 것을 다 적게 두고 전부 저장한다. 순서가 중요도이고
    # 프롬프트가 "결론에 가장 크게 작용한 것부터"를 요구한다. Slack만 위에서 셋을 보인다.
    reasons: tuple[Reason, ...] = ()
    # 이유가 0건으로 저장된 답. **정상 답과 같아 보이면 안 된다.**
    weak: bool = False
    rejected: int = 0
    # 버린 이유의 사유. **DB에 안 간다** — 교정 문구가 싣는 재료다. 사유 없는 교정을 받은
    # 모델은 "형식이 틀렸나"만 보고 같은 답을 다시 낸다.
    dropped: tuple[str, ...] = ()
    tool_rounds: int = 0
    truncated: bool = False


class Observation(BaseModel):
    """검증을 통과한 관찰 하나."""

    model_config = ConfigDict(frozen=True)

    factor: Factor
    sign: ObservationSign
    strength: int
    note: str


class DraftMemory(BaseModel):
    """검증을 통과한 새 메모 하나."""

    model_config = ConfigDict(frozen=True)

    text: str
    reason: str
    factor: Factor | None = None


class MemoryReview(BaseModel):
    """활성 메모 하나에 대한 판정(검증 후)."""

    model_config = ConfigDict(frozen=True)

    id: int
    verdict: MemoryVerdict
    reason: str


class ReviewDraft(BaseModel):
    """검증을 마친 장후 관찰."""

    model_config = ConfigDict(frozen=True)

    observations: tuple[Observation, ...] = ()
    memories: tuple[DraftMemory, ...] = ()
    reviews: tuple[MemoryReview, ...] = ()
    rejected: int = 0
    memories_rejected: int = 0
    tool_rounds: int = 0
    truncated: bool = False


# ---------------------------------------------------------------------------
# 그래프 상태
# ---------------------------------------------------------------------------


class _GraphState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    tool_rounds: int
    truncated: bool
    attempts: int
    error: str | None
    draft: Any


class _Builder:
    """두 흐름이 같은 그래프 모양을 쓴다. 다른 것은 프롬프트와 검증뿐이다.

    **상태가 컴파일된 그래프라 클래스다.** 모델·툴박스·그래프가 실행 동안 안 변해서
    생성자가 받고, 관측 상태처럼 호출마다 바뀌는 것은 메서드 인자다.
    """

    schema_name = "answer"
    run_name = "kospi"

    def __init__(self, model: BaseChatModel, toolbox: KospiToolbox) -> None:
        self._model = model
        self._toolbox = toolbox
        self._tool_node = tool_node(toolbox)
        # 토큰은 그래프 밖 콜백이 센다. **실패한 대화에도 그때까지 부른 만큼이 남는다.**
        self._usage = UsageMetadataCallbackHandler()
        self._graph = self._build_graph()

    @property
    def usage(self) -> llm.TokenUsage:
        return llm.token_usage(self._usage)

    def _build_graph(self):
        graph = StateGraph(_GraphState)
        graph.add_node("investigate", self._investigate)
        graph.add_node("tools", self._tools)
        # 상한에서 끊긴 사실을 남기는 노드. 조건부 엣지는 상태를 못 바꾸므로 답변 앞에 둔다.
        graph.add_node("close_investigation", self._mark_truncation)
        graph.add_node("answer", self._answer)
        graph.add_node("repair", self._repair)
        graph.add_edge(START, "investigate")
        graph.add_conditional_edges(
            "investigate",
            self._after_investigate,
            {"tools": "tools", "answer": "close_investigation"},
        )
        graph.add_edge("close_investigation", "answer")
        graph.add_edge("tools", "investigate")
        graph.add_conditional_edges("answer", self._after_answer, {"repair": "repair", END: END})
        graph.add_edge("repair", "answer")
        return graph.compile()

    def _run(self, system: str, instruction: str, metadata: dict[str, Any]) -> Any:
        state: _GraphState = {
            "messages": [SystemMessage(system), HumanMessage(instruction)],
            "tool_rounds": 0,
            "truncated": False,
            "attempts": 0,
            "error": None,
            "draft": None,
        }
        final = self._graph.invoke(
            state,
            config={
                "run_name": self.run_name,
                "tags": ["kospi"],
                "metadata": metadata,
                "callbacks": [self._usage],
            },
        )
        draft = final.get("draft")
        if draft is None:
            raise KospiError(final.get("error") or "모델이 쓸 수 있는 답을 내지 않았다")
        return draft

    # --- 노드 -------------------------------------------------------------

    def _investigate(self, state: _GraphState) -> dict[str, Any]:
        """툴만 바인딩해 부른다. 스키마는 넣지 않는다(`llm.invoke`가 막는다)."""
        reply = llm.invoke(self._model, state["messages"], tools=self._toolbox.tools)
        return {"messages": [reply]}

    def _tools(self, state: _GraphState) -> dict[str, Any]:
        """`ToolNode`가 tool_call을 돌리고 `ToolMessage`를 만든다. 우리는 왕복만 센다.

        원장이 여기서 열리고 닫힌다. **래퍼만으로는 부족하다** — 모르는 툴과 인자 검증 실패는
        함수에 도달하기 전에 `ToolNode`가 오류 `ToolMessage`로 바꾼다.
        """
        reply = state["messages"][-1]
        self._toolbox.begin_round(getattr(reply, "tool_calls", None) or [])
        update = self._tool_node.invoke(state)
        self._toolbox.finish_round(update["messages"])
        return {"messages": update["messages"], "tool_rounds": state["tool_rounds"] + 1}

    def _answer(self, state: _GraphState) -> dict[str, Any]:
        """툴을 빼고 스키마를 강제한다. **조사 단계가 이미 답을 냈으면 다시 묻지 않는다.**

        조사 루프의 마지막 응답은 툴을 더 안 부르겠다는 뜻이고, 모델이 거기서 답 JSON을
        통째로 내는 경우가 많다. 그것이 검증을 통과하면 그대로 쓴다 — 재요청은 같은 답을
        두 번 사는 자리이고, 값을 잃는 것도 봤다(2026-08-27 옛 추론 트레이스).
        """
        messages = state["messages"]
        reply = messages[-1]
        if isinstance(reply, AIMessage) and not getattr(reply, "tool_calls", None):
            try:
                draft = self.parse(_text(reply))
            except KospiError as error:
                logger.info("조사 단계 답을 그대로 쓰지 못해 스키마로 다시 묻는다: %s", error)
            else:
                # 그 응답은 이미 상태에 있다. 다시 넣으면 대화가 한 번 더 늘어난다.
                return self._accept(state, draft, messages=[])

        try:
            reply = llm.invoke(self._model, messages, schema=self._schema())
        except UnsupportedResponseFormat as error:
            logger.warning("provider does not accept a response schema; falling back to validation: %s", error)
            reply = llm.invoke(self._model, messages)

        try:
            draft = self.parse(_text(reply))
        except KospiError as error:
            return {"messages": [reply], "draft": None, "error": str(error)}
        return self._accept(state, draft, messages=[reply])

    def _accept(self, state: _GraphState, draft: Any, *, messages: list[BaseMessage]) -> dict[str, Any]:
        """읽어 낸 답을 받을지 한 번 되물을지 정한다. 조사 답과 스키마 답이 같은 판정을 받는다.

        **"쓸 수는 있지만 모자란 답"이 여기서 갈린다.** 형식이 깨진 답은 `parse`가 이미
        거절했다. 형식은 맞는데 근거가 전부 버려졌거나(전망) 크게 움직인 날 관찰이 0건이면
        (관찰) 첫 답에 한해 한 번 되묻는다. 두 번째도 같으면 그대로 받는다 — 전망은 `weak`로
        저장되고 관찰은 부르는 쪽이 죽인다. 교정은 한 번뿐이고 재시도는 Airflow가 한다.
        """
        reason = self._needs_repair(draft)
        if reason and state["attempts"] == 0:
            logger.warning("답이 모자라 한 번 되묻는다: %s", reason)
            return {"messages": messages, "draft": None, "error": reason}
        return {"messages": messages, "draft": draft, "error": None}

    def _needs_repair(self, draft: Any) -> str | None:
        """형식은 맞는데 모자란 답이면 그 사유. 기본은 되묻지 않는다."""
        return None

    def _repair(self, state: _GraphState) -> dict[str, Any]:
        """한 번만 다시 묻는다. **사유를 그대로 싣는다.**

        사유가 없으면 모델은 "형식이 틀렸나"만 보고 같은 답을 다시 낸다.
        """
        logger.warning("retrying once after %s", state["error"])
        return {
            "messages": [HumanMessage(self._prompts().render("repair", reason=state["error"] or ""))],
            "attempts": state["attempts"] + 1,
        }

    @staticmethod
    def _after_investigate(state: _GraphState) -> str:
        reply = state["messages"][-1]
        if getattr(reply, "tool_calls", None) and state["tool_rounds"] < MAX_TOOL_ROUNDS:
            return "tools"
        return "answer"

    @staticmethod
    def _mark_truncation(state: _GraphState) -> dict[str, Any]:
        """상한에서 끊겼으면 그 사실을 상태에 남긴다.

        **경로 판정에서 쓰기를 하지 않는다** — 조건부 엣지의 반환값은 다음 노드 이름이라
        상태를 못 바꾼다. 스스로 끝낸 조사와 잘린 조사가 구분되지 않으면 상한을 올릴지
        판단할 근거가 없다.
        """
        reply = state["messages"][-1]
        if getattr(reply, "tool_calls", None) and state["tool_rounds"] >= MAX_TOOL_ROUNDS:
            logger.warning("investigation truncated after %s rounds", state["tool_rounds"])
            return {"truncated": True}
        return {}

    @staticmethod
    def _after_answer(state: _GraphState) -> str:
        if state["draft"] is not None:
            return END
        return "repair" if state["attempts"] == 0 else END

    # --- 하위 클래스가 채운다 ---------------------------------------------

    def _prompts(self):
        raise NotImplementedError

    def _schema(self) -> dict[str, Any]:
        raise NotImplementedError

    def parse(self, raw: str) -> Any:
        raise NotImplementedError


class ForecastBuilder(_Builder):
    """전망 하나를 만든다. 슬롯 셋이 이 클래스 하나를 쓴다."""

    run_name = "kospi_forecast"

    def __init__(self, model: BaseChatModel, toolbox: KospiToolbox, *, observed: ObservedState) -> None:
        self._observed = observed
        # 검증이 쓰는 목록 셋. 관측 상태에서 뽑아 둔다 — 파싱마다 다시 훑지 않는다.
        self._state_factors = frozenset(row.factor for row in observed.relations if row.n_obs > 0)
        self._memory_ids = frozenset(row.id for row in observed.memories)
        self._earlier_slots = frozenset(row.slot for row in observed.earlier_slots)
        super().__init__(model, toolbox)

    def build(self) -> ForecastDraft:
        prompts = FORECAST_PROMPTS
        system = prompts.render(
            "system",
            max_tool_calls=MAX_TOOL_CALLS,
            max_statement_chars=MAX_STATEMENT_CHARS,
            max_change_pct=MAX_EXPECTED_CHANGE_PCT,
            min_band_pct=MIN_BAND_PCT,
            max_band_pct=MAX_BAND_PCT,
        )
        variant = "instruction_pre_open" if self._observed.slot is RunSlot.PRE_OPEN else "instruction_intraday"
        instruction = prompts.render(
            "instruction",
            slot_instruction=prompts.render_variant(variant),
            state=json_dump(self._observed.model_dump(mode="json")),
        )
        draft: ForecastDraft = self._run(
            system,
            instruction,
            {"slot": self._observed.slot.value, "run_date": self._observed.run_date.isoformat()},
        )
        return draft.model_copy(
            update={"tool_rounds": self._toolbox.round_count, "truncated": draft.truncated}
        )

    def _prompts(self):
        return FORECAST_PROMPTS

    def _schema(self) -> dict[str, Any]:
        return response_format(ForecastAnswer, "kospi_forecast")

    def _needs_repair(self, draft: ForecastDraft) -> str | None:
        """이유가 전부 버려진 답은 한 번 되묻는다. **무엇을 왜 버렸는지를 싣는다.**"""
        if not draft.weak:
            return None
        detail = ", ".join(draft.dropped) if draft.dropped else "이유가 없다"
        return f"이유 {draft.rejected}건이 전부 버려졌다 — {detail}. 조회한 요인·활성 메모·오늘 앞 슬롯만 인용한다"

    def parse(self, raw: str) -> ForecastDraft:
        """응답을 검증하고 쓸 수 없는 이유를 버린다.

        **이유가 전부 버려져도 답은 남는다.** 방향·크기·폭은 그 자체로 채점되는 값이라,
        근거가 없다는 사실을 `weak`로 표시해 저장하는 편이 태스크를 죽이는 것보다 낫다.
        다만 첫 답이 그러면 한 번 다시 묻는다 — `_after_answer`가 아니라 여기서 예외를 내야
        교정이 도는데, 그것은 `attempts`를 모르는 자리라 `weak`를 담아 올린다.
        """
        try:
            answer = ForecastAnswer.model_validate_json(json_object(raw))
        except SchemaError as error:
            raise KospiError(str(error)) from error
        except ValidationError as error:
            raise KospiError(f"답을 읽을 수 없다: {error}") from error

        expected = _decimal(answer.expected_change_pct, -MAX_EXPECTED_CHANGE_PCT, MAX_EXPECTED_CHANGE_PCT)
        band = _decimal(answer.band_pct, MIN_BAND_PCT, MAX_BAND_PCT)
        if expected is None or band is None:
            raise KospiError(
                f"크기 또는 폭이 범위 밖이다: expected={answer.expected_change_pct} band={answer.band_pct}"
            )

        direction = Direction(answer.direction)
        # **부호와 방향이 어긋나면 버린다.** `down`인데 `+1.2`를 부르면 어느 쪽이 진짜인지
        # 우리가 고를 수 없다. 조용히 부호를 뒤집으면 모델이 부르지 않은 숫자가 채점된다.
        if (direction is Direction.UP and expected <= 0) or (direction is Direction.DOWN and expected >= 0):
            raise KospiError(f"방향({direction.value})과 기대 등락률({expected})의 부호가 어긋난다")

        reasons, dropped = self._verify_reasons(answer.reasons)
        return ForecastDraft(
            direction=direction,
            expected_change_pct=expected,
            band_pct=band,
            reasons=reasons,
            weak=not reasons,
            rejected=len(dropped),
            dropped=dropped,
        )

    def _verify_reasons(self, answers: Sequence[ReasonAnswer]) -> tuple[tuple[Reason, ...], tuple[str, ...]]:
        """근거를 실제로 본 것에 대조한다. **버린 사유를 돌려준다** — 분모가 있어야 유효율이
        읽히고, 사유가 있어야 교정 문구가 무엇을 고칠지 말한다."""
        seen = self._toolbox.queried_factors | self._state_factors
        kept: list[Reason] = []
        dropped: list[str] = []
        for item in answers:
            statement = normalize_text(item.statement, MAX_STATEMENT_CHARS)
            if not statement:
                dropped.append("빈 문장")
                continue
            hit = next((phrase for phrase in FORBIDDEN_PHRASES if phrase in statement), None)
            if hit:
                dropped.append(f"금지어({hit})")
                continue

            factor = _factor_or_none(item.factor)
            if item.factor and factor is None:
                dropped.append(f"모르는 요인({item.factor})")
                continue
            if factor is not None and factor not in seen:
                dropped.append(f"조회하지 않은 요인({factor.value})")
                continue
            if item.memory_id is not None and item.memory_id not in self._memory_ids:
                dropped.append(f"활성 목록 밖 메모({item.memory_id})")
                continue

            slot_ref = None
            if item.slot_ref:
                try:
                    slot_ref = RunSlot(item.slot_ref)
                except ValueError:
                    dropped.append(f"모르는 슬롯({item.slot_ref})")
                    continue
                if slot_ref not in self._earlier_slots:
                    dropped.append(f"오늘 앞 슬롯이 아니다({slot_ref.value})")
                    continue

            kept.append(
                Reason(
                    direction=Direction(item.direction),
                    statement=statement,
                    factor=factor,
                    memory_id=item.memory_id,
                    slot_ref=slot_ref,
                )
            )
        if dropped:
            logger.warning("dropped %s reasons: %s", len(dropped), dropped)
        return tuple(kept), tuple(dropped)


class ReviewBuilder(_Builder):
    """장후 관찰 하나를 만든다. 관찰·새 메모·메모 판정이 한 답에 온다."""

    run_name = "kospi_review"

    def __init__(self, model: BaseChatModel, toolbox: KospiToolbox, *, observed: ReviewState) -> None:
        self._observed = observed
        self._memory_ids = frozenset(row.id for row in observed.memories)
        super().__init__(model, toolbox)

    def build(self) -> ReviewDraft:
        prompts = REVIEW_PROMPTS
        system = prompts.render(
            "system",
            max_tool_calls=MAX_TOOL_CALLS,
            max_note_chars=MAX_NOTE_CHARS,
            max_memory_chars=MAX_MEMORY_CHARS,
            max_reason_chars=MAX_REASON_CHARS,
            min_strength=MIN_STRENGTH,
            max_strength=MAX_STRENGTH,
        )
        instruction = prompts.render("instruction", state=json_dump(self._observed.model_dump(mode="json")))
        draft: ReviewDraft = self._run(system, instruction, {"run_date": self._observed.run_date.isoformat()})
        return draft.model_copy(update={"tool_rounds": self._toolbox.round_count})

    def _prompts(self):
        return REVIEW_PROMPTS

    def _schema(self) -> dict[str, Any]:
        return response_format(ReviewAnswer, "kospi_review")

    def _needs_repair(self, draft: ReviewDraft) -> str | None:
        """크게 움직인 날 관찰이 0건이면 한 번 되묻는다. 조용한 날의 0건은 정상이다."""
        if draft.observations or abs(self._observed.change_pct) < OBSERVATION_REQUIRED_PCT:
            return None
        return (
            f"코스피가 {self._observed.change_pct}퍼센트 움직였는데 관찰이 0건이다"
            f"(버린 것 {draft.rejected}건). 툴로 조회한 요인 중 오늘 움직임과 이어진 것을 적는다"
        )

    def parse(self, raw: str) -> ReviewDraft:
        try:
            answer = ReviewAnswer.model_validate_json(json_object(raw))
        except SchemaError as error:
            raise KospiError(str(error)) from error
        except ValidationError as error:
            raise KospiError(f"답을 읽을 수 없다: {error}") from error

        observations, rejected = self._verify_observations(answer.observations)
        memories, memories_rejected = self._verify_memories(answer.memories)
        reviews = self._verify_reviews(answer.memory_reviews)
        return ReviewDraft(
            observations=observations,
            memories=memories,
            reviews=reviews,
            rejected=rejected,
            memories_rejected=memories_rejected,
        )

    def _verify_observations(self, answers: Sequence[ObservationAnswer]) -> tuple[tuple[Observation, ...], int]:
        """**툴로 조회하지 않은 요인의 관찰은 버린다.**

        관찰이 숫자를 봤다는 증거가 원장의 툴 호출 목록이다. 이것이 없으면 모델이 관계
        가중치만 보고 어제 것을 오늘 것으로 다시 쓴다.

        같은 요인이 두 번 오면 **첫 것이 남는다** — 하루에 요인당 엣지 하나이고, 어느 쪽이
        진짜인지 우리가 고를 수 없다.
        """
        seen = self._toolbox.queried_factors
        kept: dict[Factor, Observation] = {}
        dropped: list[str] = []
        for item in answers:
            factor = _factor_or_none(item.factor)
            if factor is None:
                dropped.append(f"모르는 요인({item.factor})")
                continue
            if factor not in seen:
                dropped.append(f"조회하지 않은 요인({factor.value})")
                continue
            if factor in kept:
                dropped.append(f"중복({factor.value})")
                continue
            kept[factor] = Observation(
                factor=factor,
                sign=ObservationSign(item.sign),
                strength=max(MIN_STRENGTH, min(MAX_STRENGTH, int(item.strength))),
                note=normalize_text(item.note, MAX_NOTE_CHARS),
            )
        if dropped:
            logger.warning("dropped %s observations: %s", len(dropped), dropped)
        return tuple(kept.values()), len(dropped)

    def _verify_memories(self, answers: Sequence[MemoryAnswer]) -> tuple[tuple[DraftMemory, ...], int]:
        """빈 문장과 모르는 요인만 여기서 버린다.

        **상한과 중복은 여기서 보지 않는다.** 그 판정에는 지금 활성인 메모가 필요하고
        그것은 `review.py`가 그래프에서 읽어 온다 — 이 클래스는 그래프를 모른다.
        """
        kept: list[DraftMemory] = []
        dropped: list[str] = []
        for item in answers:
            text = normalize_text(item.text, MAX_MEMORY_CHARS)
            if not text:
                dropped.append("빈 문장")
                continue
            factor = _factor_or_none(item.factor) if item.factor else None
            if item.factor and factor is None:
                # 요인만 틀린 것은 메모를 버릴 이유가 못 된다. 링크를 떼고 남긴다.
                logger.warning("memory points at an unknown factor, keeping it unlinked: %s", item.factor)
            kept.append(
                DraftMemory(text=text, reason=normalize_text(item.reason, MAX_REASON_CHARS), factor=factor)
            )
        if dropped:
            logger.warning("dropped %s memories: %s", len(dropped), dropped)
        return tuple(kept), len(dropped)

    def _verify_reviews(self, answers: Sequence[MemoryReviewAnswer]) -> tuple[MemoryReview, ...]:
        """활성 목록 안의 판정만 남긴다. 같은 id가 두 번이면 첫 것이 남는다."""
        kept: dict[int, MemoryReview] = {}
        dropped: list[int] = []
        for item in answers:
            if item.id not in self._memory_ids or item.id in kept:
                dropped.append(item.id)
                continue
            kept[item.id] = MemoryReview(
                id=item.id,
                verdict=MemoryVerdict(item.verdict),
                reason=normalize_text(item.reason, MAX_REASON_CHARS),
            )
        if dropped:
            logger.warning("dropped %s memory reviews for unknown ids: %s", len(dropped), dropped)
        return tuple(kept.values())


# ---------------------------------------------------------------------------
# 도우미
# ---------------------------------------------------------------------------


def _text(reply: AIMessage) -> str:
    """응답 본문. 제공처에 따라 문자열이 아니라 조각 리스트로 온다.

    Responses API를 쓰는 모델이 `[{"type": "reasoning"}, {"type": "text", ...}]`로 준다.
    `str()`을 씌우면 파이썬 repr가 되고 JSON 파싱이 죽는다.
    """
    content = reply.content
    if isinstance(content, str):
        return content
    return "".join(part if isinstance(part, str) else str(part.get("text", "")) for part in content)


def _decimal(value: float, low: Decimal, high: Decimal) -> Decimal | None:
    """범위 안이면 두 자리로 접은 `Decimal`, 밖이면 `None`.

    **clamp하지 않는다.** 조이면 모델이 부르지 않은 숫자가 저장되고 채점이 그것을 모델의
    판단으로 센다.
    """
    try:
        number = quantize_change(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return None
    return number if low <= number <= high else None


def _factor_or_none(value: str | None) -> Factor | None:
    if not value:
        return None
    try:
        return Factor(str(value).strip().upper())
    except ValueError:
        return None
