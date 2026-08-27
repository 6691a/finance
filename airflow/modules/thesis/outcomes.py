"""사후 평가 — 지평별 해설과 판정, `FollowupNarrator`.

지나간 추론을 T+1·3·5에 되돌아본다. **채점 수식은 여기 없다** — `thesis.domain`의
`classify_outcome`·`brier_score`가 순수 함수로 갖는다. LLM은 산문을 쓰는 자리에만 있다.

채점 값을 읽고 쓰는 것은 `thesis.store.ThesisStore`다.

**문장은 여기 없다.** 해설 프롬프트는 `modules/prompts/thesis_narrative.yaml`이 갖는다.
읽는 방법은 `modules/prompt.py`에 있다. **여기 문장에는 판이 붙는다** — 고치면
`NARRATIVE_PROMPT_VERSION`을 올리고 `tests/modules/test_prompt_versions.py`의 해시를
같은 커밋에서 바꾼다.
"""

"""시장 추론(thesis)을 만들고, 저장하고, 채점한다.

**목적은 정확도다 — 다만 개별 추론이 아니라 판(版)의 정확도다.** 한 건의 적중은 운과
구분되지 않으므로 "어떤 정보를 근거로 어떤 결론을 냈다"를 먼저 기록으로 남기고, 채점이
쌓이면 model·prompt 판별로 비교해 다음 변경을 유지하거나 되돌린다. **이미 쓴 추론은
고치지 않는다** — 고칠 수 있으면 나쁜 판이 사후 수정으로 좋아 보인다.

## 근거는 고정 풀이 아니라 모델이 조회한다

프롬프트에는 **관측 상태만** 준다("코스피 +1.61%", "SK하이닉스 전일 -2.1%"). 관측 상태는
전부 SQL이 계산한다. 왜인지 알아내는 데 필요한 정보는 모델이 `ThesisToolbox`의 읽기 전용 툴을
호출해 스스로 가져온다 — 어떤 것을 얼마나 볼지는 모델이 정한다.

**모델이 실제로 인용한 근거만 저장한다.** 툴이 돌려준 항목에는 전부 `ref`가 붙어 있고,
답변의 `evidence_refs`는 그 레지스트리로 검증한다. 목록 밖 ref는 버린다. 이것이 모델이 근거를
지어내지 못하게 막는 유일한 장치다.

## 조사와 답변을 나눈다

`modules/llm.py`의 원칙 그대로다. 조사 단계는 툴만 바인딩하고, 답변 단계는 툴을 빼고
`response_format`을 강제한다. 한 요청에 둘을 섞지 않는다 — `llm.invoke`가 그것을 막는다.

## 기준 시각은 벽시계가 아니다

**모든 조회의 끝은 슬롯이 정한 `as_of_at`이다.** 오후에 장전 슬롯을 다시 돌려도 장중 정보로
아침 예측을 덮지 않는다. 이것은 event-time cutoff다 — 현재 DB에서 확인 가능한 범위에서
`as_of_at` 이후 감지·평가·갱신된 행을 뺀다. 과거 시점을 완전히 복원하지는 못한다
(`document`는 본문·평가를 같은 행에 덮어쓰고 버전 이력을 두지 않는다).

## 첫 성공본은 불변이다

같은 (날짜, 슬롯)에 추론 행이 이미 있으면 LLM을 다시 부르지 않는다. LLM은 재호출마다 답이
달라서 덮어쓰면 최초 판단이 사라진다. `existing_theses`가 먼저 보고, 없을 때만 Builder를 돈다.

## 채점에 LLM이 없다

수식이 SQL이 아니라 파이썬에 있는 이유는 경계값을 DB 없이 테스트하기 위해서다(테스트에서
실 DB를 쓰지 않는 프로젝트 규칙). `select_session_return.sql`이 등락률을 주고
`update_outcome.sql`은 여기서 나온 값 넷을 쓰기만 한다.

설계는 `docs/analysis/market-thesis/1-storage.md`와 `docs/analysis/market-thesis/2-agent.md`에 있다.
"""

import logging
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, ConfigDict, ValidationError

from modules import llm
from modules.llm import UnsupportedResponseFormat
from modules.prompt import read_prompt
from modules.schema import SchemaError, json_object, response_format
from modules.thesis.domain import (
    MAX_TOOL_CALLS,
    SLOT_LABELS,
    Subject,
    ThesisDirection,
    ThesisError,
    ThesisVerdict,
    _shorten_to,
    kst_label,
)
from modules.thesis.generation import (
    _text,
)
from modules.thesis.state import (
    RunSlot,
)
from modules.thesis.toolbox import (
    ThesisToolbox,
    tool_node,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 사후 해설 — 원 추론의 이유가 이후 보도로 지지됐는가
# ---------------------------------------------------------------------------

# 해설 프롬프트를 고치면 올린다. `thesis_outcome.prompt_version`에 변형과 함께 저장된다.
#
# 2: 산문의 숫자 표기 규칙(`llm.NUMBER_STYLE`)이 붙었다(2026-08-26).
NARRATIVE_PROMPT_VERSION = "2"

# 해설 한 편의 상한. 넘으면 그 항목만 자른다.
MAX_NARRATIVE_CHARS = 1000


class NarrativeVariant(StrEnum):
    """해설 프롬프트가 실제 결과를 보느냐 마느냐.

    어느 쪽이 나은지 추측하지 않고 실측으로 갈랐다(`docs/analysis/market-thesis/5-followup.md` 12절).

    **`INFORMED`가 기본이다**(2026-08-21 2회차). `BLIND`가 사는 것이 없었다 — 툴 호출·
    레지스트리·서술의 질이 같고, 가격은 어차피 후속 기사로 새어 들어오며, 판정만 체계적으로
    약해졌다(같은 사실을 찾고도 `contradicted` 대신 `unresolved`를 골랐다).

    남겨 두는 이유는 되돌릴 수 있게 하기 위해서다. 독립 사건 둘로 정한 값이라 분기 단위로
    다시 본다.
    """

    INFORMED = "informed"
    BLIND = "blind"


class NarrativeTarget(BaseModel):
    """해설을 붙일 (추론, 지평) 하나. 프롬프트 입력이다."""

    model_config = ConfigDict(frozen=True)

    thesis_id: int
    # 원 추론의 슬롯. 한 날짜에 같은 대상의 장전·장후 추론이 둘 다 있어 `subject_code`만으로는
    # 어느 추론인지 모른다. 해설 호출은 슬롯마다 따로 한다(`FollowupNarrator.run`).
    run_slot: RunSlot
    subject: Subject
    prob_up: Decimal
    prob_down: Decimal
    prob_flat: Decimal
    up_reasoning: str
    down_reasoning: str
    flat_reasoning: str
    # 원 추론이 인용했던 근거 제목. 무엇을 보고 그 이유를 썼는지 모델이 알아야 판정할 수 있다.
    cited_titles: tuple[str, ...] = ()
    # 채점 결과. `informed` 변형만 프롬프트에 싣는다. `post_close` 추론은 채점이 없어 None이다.
    actual_return_pct: Decimal | None = None
    actual_outcome: ThesisDirection | None = None
    brier_score: Decimal | None = None
    # 크기 채점(지평 0). 해설은 지평 1·3·5라 SQL이 지평을 건너 조인해 준다. 숫자만으로는
    # 과대·과소의 **이유**가 안 남아서, 크게 어긋난 날은 해설이 그것을 다루게 한다.
    predicted_return_pct: Decimal | None = None
    return_error_pct: Decimal | None = None


class NarrativeAnswer(BaseModel):
    """모델이 대상 하나에 대해 낸 해설. 검증 전 원본이다."""

    model_config = ConfigDict(frozen=True)

    subject_code: str
    narrative: str = ""
    # 검증기가 아니라 타입으로 막는다. Literal은 스키마에 enum으로 실려 모델이 애초에
    # 다른 값을 내지 못한다(`assessment.py`의 `direction`과 같은 방식).
    verdict: Literal["supported", "contradicted", "unresolved"] = "unresolved"
    evidence_refs: tuple[str, ...] = ()


class Narratives(BaseModel):
    """모델 응답 전체."""

    model_config = ConfigDict(frozen=True)

    narratives: tuple[NarrativeAnswer, ...] = ()


class NarrativeDraft(BaseModel):
    """검증을 마친 해설 하나. 그대로 `thesis_outcome`의 해설 칸이 된다."""

    model_config = ConfigDict(frozen=True)

    thesis_id: int
    subject_code: str
    narrative: str
    verdict: ThesisVerdict
    evidence_refs: tuple[str, ...] = ()


PROMPTS = read_prompt("thesis_narrative")

NARRATIVE_SYSTEM_PROMPT = PROMPTS.render(
    "system", max_narrative_chars=MAX_NARRATIVE_CHARS, number_style=llm.NUMBER_STYLE
)
NARRATIVE_REPAIR_INSTRUCTION = PROMPTS.repair


class NarrativeState(TypedDict):
    """해설 한 번의 상태. 연결·설정 객체는 넣지 않는다."""

    # `add_messages` 리듀서를 단다. 노드는 **새로 생긴 메시지만** 돌려주고 병합은
    # 리듀서가 한다 — `ToolNode`가 그 형태로 반환하므로 이게 맞춰야 할 쪽이다.
    messages: Annotated[list[BaseMessage], add_messages]
    targets: tuple[NarrativeTarget, ...]
    drafts: tuple[NarrativeDraft, ...] | None
    error: str | None
    attempts: int


class FollowupNarrator:
    """지나간 추론에 사후 해설과 판정을 붙인다. `ThesisBuilder`와 같은 LangGraph 계보다.

    **지평마다 별도 호출이다.** 툴 조회의 기준 시각이 지평마다 달라 한 대화에 섞을 수 없다.
    한 호출 안에서는 그 지평의 모든 대상을 한 번에 준다(건별 호출 금지 규칙 그대로).

    `include_outcome`이 프롬프트 변형을 가른다. 어느 쪽이 나은지는 실측으로 정한다
    (`docs/analysis/market-thesis/5-followup.md` 12절).
    """

    def __init__(self, model: BaseChatModel, toolbox: ThesisToolbox, *, include_outcome: bool = True) -> None:
        self._model = model
        self._toolbox = toolbox
        self._include_outcome = include_outcome
        self._schema = response_format(Narratives, "thesis_narratives")
        self._tool_node = tool_node(toolbox)
        self._usage = UsageMetadataCallbackHandler()
        self._graph = self._build_graph()

    @property
    def usage(self) -> llm.TokenUsage:
        """그래프가 지금까지 청구된 토큰. **예외가 나도 읽을 수 있다**(`modules/llm.py`)."""
        return llm.token_usage(self._usage)

    @property
    def variant(self) -> NarrativeVariant:
        return NarrativeVariant.INFORMED if self._include_outcome else NarrativeVariant.BLIND

    @property
    def prompt_revision(self) -> str:
        """`thesis_outcome.prompt_version`에 저장할 값. 변형을 판에 싣는다.

        `assessment.py`의 `LlmSettings.prompt_revision`이 관점을 판에 싣는 것과 같은 방식이다.
        새 컬럼을 만들지 않고도 어느 변형이 그 행을 썼는지 DB가 증명한다.
        """
        return f"{NARRATIVE_PROMPT_VERSION}/{self.variant.value}"

    def build_messages(
        self,
        *,
        run_date: date,
        run_slot: RunSlot,
        horizon_days: int,
        as_of_at: datetime,
        targets: Sequence[NarrativeTarget],
    ) -> list[BaseMessage]:
        return [
            SystemMessage(NARRATIVE_SYSTEM_PROMPT),
            HumanMessage(
                PROMPTS.render(
                    "instruction",
                    run_date=run_date.isoformat(),
                    slot_label=SLOT_LABELS[run_slot],
                    horizon_days=horizon_days,
                    as_of_at=kst_label(as_of_at),
                    targets="\n\n".join(self._render_target(target) for target in targets),
                )
            ),
        ]

    def _render_target(self, target: NarrativeTarget) -> str:
        lines = [
            f"### {target.subject.code} ({target.subject.label})",
            f"- 상승 {target.prob_up:.0%} / 하락 {target.prob_down:.0%} / 횡보 {target.prob_flat:.0%}",
            f"- 상승 이유: {target.up_reasoning}",
            f"- 하락 이유: {target.down_reasoning}",
            f"- 횡보 이유: {target.flat_reasoning}",
        ]
        if target.cited_titles:
            lines.append("- 그때 인용한 근거: " + " · ".join(target.cited_titles))
        if self._include_outcome and target.actual_outcome is not None:
            lines.append(
                f"- **실제 결과**: {target.actual_return_pct:+.2f}% ({target.actual_outcome.value}), "
                f"Brier {target.brier_score}"
            )
            if target.return_error_pct is not None:
                # 부호가 뜻이다 — 양수면 실제가 더 컸다(과소추정), 음수면 과대추정이다.
                gap = "과소" if target.return_error_pct > 0 else "과대"
                lines.append(
                    f"- **크기 예측**: {target.predicted_return_pct}% 예상 → "
                    f"{target.return_error_pct:+.2f}%p {gap}"
                )
        return "\n".join(lines)

    def run(
        self,
        *,
        run_date: date,
        horizon_days: int,
        as_of_at: datetime,
        targets: Sequence[NarrativeTarget],
    ) -> tuple[NarrativeDraft, ...]:
        """해설들. 두 번째도 실패하면 `ThesisError`를 올린다.

        **한 호출은 슬롯 하나다.** 프롬프트 첫 줄이 슬롯을 전제하고, 응답은 `subject_code`로
        대상을 찾는데 같은 날 장전·장후 추론이 같은 대상을 갖는다. 슬롯이 섞이면 한쪽이
        다른 쪽의 해설을 받고 나머지는 영영 미해설로 남는다. 슬롯은 대상에서 읽는다 —
        부르는 쪽이 따로 넘기면 어긋날 수 있다(2026-08-23까지 `PRE_OPEN` 고정이었다).
        """
        if not targets:
            return ()
        slots = {target.run_slot for target in targets}
        if len(slots) != 1:
            raise ThesisError(f"narration targets span {len(slots)} slots; call once per slot: {sorted(slots)}")
        (run_slot,) = slots
        state: NarrativeState = {
            "messages": self.build_messages(
                run_date=run_date,
                run_slot=run_slot,
                horizon_days=horizon_days,
                as_of_at=as_of_at,
                targets=targets,
            ),
            "targets": tuple(targets),
            "drafts": None,
            "error": None,
            "attempts": 0,
        }
        final = self._graph.invoke(
            state,
            config={
                "run_name": "narrate_followups",
                "metadata": {"horizon_days": horizon_days, "run_slot": run_slot.value, "variant": self.variant.value},
                "callbacks": [self._usage],
            },
        )
        drafts = final.get("drafts")
        if drafts is None:
            raise ThesisError(final.get("error") or "Model did not return any narrative")
        return drafts

    def parse(self, raw: str, targets: Sequence[NarrativeTarget]) -> tuple[NarrativeDraft, ...]:
        """응답을 검증하고 쓸 수 없는 항목을 버린다."""
        try:
            parsed = Narratives.model_validate_json(json_object(raw))
        except SchemaError as error:
            raise ThesisError(str(error)) from error
        except ValidationError as error:
            raise ThesisError(f"Model returned an unusable object: {error}") from error

        by_code = {target.subject.code: target for target in targets}
        seen: set[str] = set()
        drafts: list[NarrativeDraft] = []
        dropped: list[str] = []

        for answer in parsed.narratives:
            target = by_code.get(answer.subject_code)
            if target is None or answer.subject_code in seen:
                dropped.append(answer.subject_code)
                continue
            seen.add(answer.subject_code)
            refs = self._known_refs(answer.subject_code, answer.evidence_refs)
            drafts.append(
                NarrativeDraft(
                    thesis_id=target.thesis_id,
                    subject_code=answer.subject_code,
                    narrative=_shorten_to(answer.narrative, MAX_NARRATIVE_CHARS),
                    verdict=_grounded_verdict(answer.subject_code, answer.verdict, refs),
                    evidence_refs=refs,
                )
            )

        if dropped:
            logger.warning("dropped %s narratives: %s", len(dropped), dropped)
        if parsed.narratives and not drafts:
            raise ThesisError(f"Model returned {len(parsed.narratives)} narratives, none of them usable")
        return tuple(drafts)

    def _known_refs(self, subject_code: str, refs: Sequence[str]) -> tuple[str, ...]:
        """레지스트리에 있는 ref만, 첫 등장 순서로 중복 없이. 순서가 곧 `rank`다."""
        registry = self._toolbox.registry
        kept: list[str] = []
        unknown: list[str] = []
        for ref in refs:
            if ref in registry:
                if ref not in kept:
                    kept.append(ref)
            else:
                unknown.append(ref)
        if unknown:
            logger.warning("%s cited %s refs that no tool returned: %s", subject_code, len(unknown), unknown)
        return tuple(kept)

    def _build_graph(self):
        graph = StateGraph(NarrativeState)
        graph.add_node("investigate", self._investigate)
        graph.add_node("tools", self._tools)
        graph.add_node("answer", self._answer)
        graph.add_node("repair", self._repair)
        graph.add_edge(START, "investigate")
        graph.add_conditional_edges("investigate", self._after_investigate, {"tools": "tools", "answer": "answer"})
        graph.add_edge("tools", "investigate")
        graph.add_conditional_edges("answer", self._after_answer, {"repair": "repair", END: END})
        graph.add_edge("repair", "answer")
        return graph.compile()

    def _investigate(self, state: NarrativeState) -> dict[str, Any]:
        reply = llm.invoke(self._model, state["messages"], tools=self._toolbox.tools)
        return {"messages": [reply]}

    def _tools(self, state: NarrativeState) -> dict[str, Any]:
        """`ThesisBuilder._tools`와 같은 노드다. 여기는 왕복을 세지 않고 상한은
        `ThesisToolbox.call_count`가 본다(`_after_investigate`).

        원장(13단계)도 같은 자리에서 열고 닫는다 — 해설 대화의 툴 호출도 남긴다.
        """
        reply = state["messages"][-1]
        self._toolbox.begin_round(getattr(reply, "tool_calls", None) or [])
        update = self._tool_node.invoke(state)
        self._toolbox.finish_round(update["messages"])
        return {"messages": update["messages"]}

    def _answer(self, state: NarrativeState) -> dict[str, Any]:
        messages = state["messages"]
        try:
            reply = llm.invoke(self._model, messages, schema=self._schema)
        except UnsupportedResponseFormat as error:
            logger.warning("provider does not accept a response schema; falling back to validation: %s", error)
            reply = llm.invoke(self._model, messages)

        try:
            drafts = self.parse(_text(reply), state["targets"])
        except ThesisError as error:
            return {"messages": [reply], "drafts": None, "error": str(error)}
        return {"messages": [reply], "drafts": drafts, "error": None}

    def _repair(self, state: NarrativeState) -> dict[str, Any]:
        logger.warning("retrying the narratives once after %s", state["error"])
        return {
            "messages": [HumanMessage(NARRATIVE_REPAIR_INSTRUCTION)],
            "attempts": state["attempts"] + 1,
        }

    def _after_investigate(self, state: NarrativeState) -> str:
        reply = state["messages"][-1]
        if getattr(reply, "tool_calls", None) and self._toolbox.call_count < MAX_TOOL_CALLS:
            return "tools"
        return "answer"

    @staticmethod
    def _after_answer(state: NarrativeState) -> str:
        if state["drafts"] is not None:
            return END
        return "repair" if state["attempts"] == 0 else END


def _grounded_verdict(subject_code: str, verdict: str, refs: Sequence[str]) -> ThesisVerdict:
    """근거 없는 판정을 `unresolved`로 내린다.

    프롬프트에 규칙을 적어 두지만 그것만으로는 역산을 못 막는다. 이 검사는 막는다 —
    문서를 인용하지 못한 `supported`·`contradicted`는 가격을 보고 지어낸 것이다.
    오염을 없애는 장치가 아니라 **되짚을 수 있게 만드는 장치**다.
    """
    chosen = ThesisVerdict(verdict)
    if chosen is not ThesisVerdict.UNRESOLVED and not refs:
        logger.warning("%s answered %s with no evidence; downgrading to unresolved", subject_code, chosen.value)
        return ThesisVerdict.UNRESOLVED
    return chosen
