"""추론 생성 — 답변 모델과 `ThesisBuilder`.

관측 상태와 툴 결과를 놓고 오늘의 방향을 확률로 적는다. 흐름은 LangGraph `StateGraph`이고
그래프는 생성자에서 한 번 compile한다.

저장은 여기 없다. 만든 초안을 쓰는 것은 `thesis.store.ThesisStore`다 — 조회와 저장의
트랜잭션 경계를 DAG이 쥐어야 하기 때문이다.

**문장도 여기 없다.** 프롬프트는 `modules/prompts/thesis_generation.yaml`이 갖고 슬롯별
문장도 그 파일의 `variants`다. 읽는 방법은 `modules/prompt.py`에 있다. **여기 문장에는
판이 붙는다** — 고치면 `thesis.domain.PROMPT_VERSION`을 올리고
`tests/modules/test_prompt_versions.py`의 해시를 같은 커밋에서 바꾼다.
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
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from types import MappingProxyType
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from modules import llm
from modules.llm import UnsupportedResponseFormat
from modules.prompt import json_dump, read_prompt
from modules.schema import SchemaError, json_object, response_format
from modules.technical.base_rate import FLAT_BASE_RATE_BARS
from modules.thesis.domain import (
    FLAT_THRESHOLD_PCT,
    MAX_EXPECTED_RETURN_PCT,
    MAX_MECHANISM_CHARS,
    MAX_REASONING_CHARS,
    MAX_TOOL_ROUNDS,
    MIN_REASONING_CHARS,
    PROB_QUANTUM,
    PROB_SUM_TOLERANCE,
    RETURN_QUANTUM,
    RSI_OVERBOUGHT,
    RSI_OVERSOLD,
    VOLUME_HEAVY_RATIO,
    VOLUME_LIGHT_RATIO,
    Subject,
    ThesisDirection,
    ThesisError,
    _shorten,
    _shorten_to,
    kst_label,
)
from modules.thesis.state import (
    INTRADAY_SLOTS,
    NxtObservedState,
    ObservedState,
    PastThesis,
    RunSlot,
    SameDayThesis,
)
from modules.thesis.toolbox import (
    ThesisToolbox,
    tool_node,
)

logger = logging.getLogger(__name__)


class ClaimAnswer(BaseModel):
    """모델이 근거 하나를 어떻게 썼는지. 검증 전 원본이다.

    이유 문장은 산문이라 그래프 엣지에 실을 수 없다. 근거마다 **방향과 경로**를 따로 받아야
    `(:Thesis)-[:CITES {direction, mechanism}]->(:Evidence)`가 된다.
    """

    model_config = ConfigDict(frozen=True)

    ref: str
    direction: Literal["up", "down", "flat"]
    mechanism: str = ""


class ThesisAnswer(BaseModel):
    """모델이 subject 하나에 대해 낸 답. 검증 전 원본이다."""

    model_config = ConfigDict(frozen=True)

    subject_code: str
    prob_up: float = Field(ge=0, le=1)
    prob_down: float = Field(ge=0, le=1)
    prob_flat: float = Field(ge=0, le=1)
    # 방향별 **조건부** 크기다. 확률을 곱한 기대값이 아니다. 상한은 폭주만 막고 정합성
    # (임계보다 커야 한다)은 저장 전 검증이 본다 — 스키마로 막으면 답 전체가 사라진다.
    up_return_pct: float | None = Field(default=None, ge=0)
    down_return_pct: float | None = Field(default=None, ge=0)
    up_reasoning: str = ""
    down_reasoning: str = ""
    flat_reasoning: str = ""
    claims: tuple[ClaimAnswer, ...] = ()


class Answers(BaseModel):
    """모델 응답 전체. 스키마를 강제하되 강제가 안 되는 제공처를 위해 검증도 남긴다."""

    model_config = ConfigDict(frozen=True)

    theses: tuple[ThesisAnswer, ...] = ()


class Claim(BaseModel):
    """레지스트리로 검증을 마친 인용 하나. `thesis_evidence` 행의 direction·mechanism이 된다."""

    model_config = ConfigDict(frozen=True)

    ref: str
    direction: ThesisDirection
    mechanism: str


class Investigation(BaseModel):
    """조사 한 번의 결과. 추론들과 그것을 만든 조사의 모양이다.

    **`truncated`가 이 모델을 만든 이유다.** 전에는 `(drafts, rounds)` 튜플이었는데, 왕복
    상한에서 끊긴 실행과 스스로 끝낸 실행이 `rounds` 하나로는 구분되지 않는다. 원장이
    그것을 세야 다음에 상한을 올릴지 근거로 판단한다.
    """

    model_config = ConfigDict(frozen=True)

    drafts: tuple["ThesisDraft", ...]
    tool_rounds: int
    truncated: bool
    # 요청한 대상 수. `len(drafts)`와 다르면 모델이 일부만 답한 것이다 — 원장이 그 둘을
    # 함께 세야 "넷 중 하나만 저장됐다"가 SQL로 보인다.
    subjects_requested: int


class ThesisDraft(BaseModel):
    """검증·정규화를 마친 추론 하나. 그대로 `thesis` 행이 된다."""

    model_config = ConfigDict(frozen=True)

    subject: Subject
    prob_up: Decimal
    prob_down: Decimal
    prob_flat: Decimal
    # 검증을 마친 조건부 크기. 모델이 안 주거나 규칙을 어기면 `None`이고, 그때는
    # 확률만 있던 판(6 이하)과 같은 모양으로 저장된다.
    up_return_pct: Decimal | None = None
    down_return_pct: Decimal | None = None
    up_reasoning: str
    down_reasoning: str
    flat_reasoning: str
    # 레지스트리로 검증하고 ref 첫 등장 순서로 중복을 없앤 인용. rank는 이 순서다.
    claims: tuple[Claim, ...] = ()

    @property
    def evidence_refs(self) -> tuple[str, ...]:
        return tuple(claim.ref for claim in self.claims)


def normalize_probabilities(
    prob_up: float,
    prob_down: float,
    prob_flat: float,
) -> tuple[Decimal, Decimal, Decimal] | None:
    """세 확률의 합을 정확히 1로 맞춘다. 허용 오차를 넘으면 `None`이다.

    모델에게 직접 1로 맞춰 달라고 프롬프트에 적어 두고, 여기서는 반올림·형식 오차만 흡수한다.
    합이 `PROB_SUM_TOLERANCE`를 넘게 어긋났다는 것은 모델이 규칙을 안 지켰다는 뜻이라
    그 subject를 버린다 — 억지로 정규화하면 모델이 부르지 않은 확률을 우리가 지어내게 된다.
    """
    values = [Decimal(str(prob_up)), Decimal(str(prob_down)), Decimal(str(prob_flat))]
    total = sum(values)
    if total <= 0 or abs(total - 1) > PROB_SUM_TOLERANCE:
        return None

    scaled = [(value / total).quantize(PROB_QUANTUM, rounding=ROUND_HALF_UP) for value in values]
    # 자리수를 맞추면서 생긴 잔차를 가장 큰 칸에 몰아 준다. DB CHECK가 합 오차 0.001 미만을
    # 요구하므로 여기서 정확히 1이 되어야 한다.
    residual = Decimal(1) - sum(scaled)
    largest = max(range(3), key=lambda index: scaled[index])
    scaled[largest] += residual
    return scaled[0], scaled[1], scaled[2]


def normalize_return_pct(value: float | None) -> Decimal | None:
    """조건부 크기 하나를 검증한다. 규칙을 어기면 `None`이고 **그 칸만** 버린다.

    **추론 전체를 버리지 않는다.** 확률과 이유는 멀쩡한데 크기 하나 때문에 판단이 통째로
    사라지면 손해가 더 크다. 버린 사실은 부르는 쪽이 로그로 남긴다.

    거르는 것 둘.

    - `FLAT_THRESHOLD_PCT[0]` 이하 — 그 크기는 정의상 `flat`이다. 방향의 크기로 두면
      "상승하는데 임계 안"이라는 모순이 저장된다.
    - `MAX_EXPECTED_RETURN_PCT` 초과 — 폭주다. **자르지 않는다** — 상한으로 clamp하면
      모델이 부르지 않은 숫자를 우리가 지어내는 것이 된다.
    """
    if value is None:
        return None
    amount = Decimal(str(value))
    if amount <= FLAT_THRESHOLD_PCT[0] or amount > MAX_EXPECTED_RETURN_PCT:
        return None
    return amount.quantize(RETURN_QUANTUM, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# 프롬프트
# ---------------------------------------------------------------------------

PROMPTS = read_prompt("thesis_generation")

SYSTEM_PROMPT = PROMPTS.render(
    "system",
    rsi_overbought=int(RSI_OVERBOUGHT),
    rsi_oversold=int(RSI_OVERSOLD),
    volume_heavy_ratio=VOLUME_HEAVY_RATIO,
    volume_light_ratio=VOLUME_LIGHT_RATIO,
    flat_threshold_pct=FLAT_THRESHOLD_PCT[0],
    flat_base_rate_bars=FLAT_BASE_RATE_BARS,
    max_expected_return_pct=MAX_EXPECTED_RETURN_PCT,
    max_mechanism_chars=MAX_MECHANISM_CHARS,
    max_reasoning_chars=MAX_REASONING_CHARS,
    number_style=llm.NUMBER_STYLE,
)

# 슬롯이 무엇을 물어보는지. **문장은 슬롯 종류마다 한 벌**이고 장중 넷은 여기서 펼친다 —
# 슬롯이 늘어도 YAML은 안 고친다. 슬롯 목록은 코드가 정한다.
SLOT_INSTRUCTION = {
    RunSlot.PRE_OPEN: PROMPTS.variants["pre_open"],
    **{slot: PROMPTS.variants["intraday"] for slot in INTRADAY_SLOTS},
    RunSlot.POST_CLOSE: PROMPTS.variants["post_close"],
    RunSlot.POST_NXT_CLOSE: PROMPTS.variants["post_nxt_close"],
}

REPAIR_INSTRUCTION = PROMPTS.repair


# 과거 추론이 없을 때 그 절에 넣는 말. 절 자체를 빼면 프롬프트 모양이 날마다 달라진다.
# 오늘 앞 슬롯도 같다 — 장전·장후는 언제나 "(없음)"이고 장중 첫 슬롯도 그렇다.
NO_PAST_THESES = "(없음)"

def _json_section(rows: Mapping[str, Sequence[BaseModel]]) -> str:
    """subject 코드별 모델 목록을 프롬프트 블록으로. 비면 `NO_PAST_THESES`다.

    **절 자체를 빼지 않는다.** 빼면 프롬프트 모양이 날마다 달라져 캐시도 비교도 어긋난다.
    """
    shown = {code: [row.model_dump(mode="json") for row in items] for code, items in rows.items() if items}
    if not shown:
        return NO_PAST_THESES
    return f"```json\n{json_dump(shown)}\n```"


# ---------------------------------------------------------------------------
# Builder — LangGraph
# ---------------------------------------------------------------------------


def missing_subjects(subjects: Sequence[Subject], drafts: Sequence[ThesisDraft]) -> tuple[str, ...]:
    """요청했는데 답이 안 온 대상 코드. 요청 순서를 지킨다.

    **`parse`가 버린 것과 다른 수다.** 그쪽은 온 것 중 못 쓸 것을 세고, 이 함수는 아예
    안 온 것을 센다. 둘을 합쳐야 "요청 넷 중 하나만 저장됐다"가 설명된다.
    """
    answered = {draft.subject.code for draft in drafts}
    return tuple(subject.code for subject in subjects if subject.code not in answered)


class ThesisState(TypedDict):
    """추론 한 번의 상태.

    연결·설정 객체는 넣지 않는다. 상태는 트레이스 입력으로 나간다. 레지스트리도 넣지 않는다 —
    조사 중에 자라는 값이라 Toolbox가 들고 있고 노드가 그것을 읽는다.
    """

    # `add_messages` 리듀서를 단다. 노드는 **새로 생긴 메시지만** 돌려주고 병합은
    # 리듀서가 한다 — `ToolNode`가 그 형태로 반환하므로 이게 맞춰야 할 쪽이다.
    messages: Annotated[list[BaseMessage], add_messages]
    # 요청한 대상. 답변을 거를 때 노드가 읽으므로 상태에 있어야 한다.
    subjects: tuple[Subject, ...]
    tool_rounds: int
    # 첫 답에서 빠진 대상. 비어 있지 않으면 교정이 "형식"이 아니라 "개수"를 요구한다.
    missing_subjects: tuple[str, ...]
    # 첫 답에 담겨 온 것. 교정본이 더 나쁘거나 못 읽힐 때 이쪽으로 되돌아간다.
    partial_drafts: tuple[ThesisDraft, ...]
    # 모델이 툴을 더 부르겠다고 했는데 왕복 상한에서 끊긴 실행이다. **조용히 답변으로
    # 넘어가는 자리라 이 칸이 없으면 DB에서 "3왕복에 스스로 끝낸 것"과 구분되지 않는다.**
    investigation_truncated: bool
    drafts: tuple[ThesisDraft, ...] | None
    error: str | None
    attempts: int


class ThesisBuilder:
    """관측 상태를 받아 subject마다 추론 하나를 만든다.

    흐름은 `investigate → (tool_calls 있으면) tools → investigate → … → answer →
    (형식 실패) repair → answer`다. 교정은 한 번뿐이다.

    **실행당 대화 하나에 모든 subject를 한 번에** 준다. subject마다 부르면 모델이 대상들을
    비교하지 못하고 비용도 배로 든다.
    """

    def __init__(self, model: BaseChatModel, toolbox: ThesisToolbox) -> None:
        self._model = model
        self._toolbox = toolbox
        self._schema = response_format(Answers, "market_theses")
        self._tool_node = tool_node(toolbox)
        self._usage = UsageMetadataCallbackHandler()
        self._graph = self._build_graph()

    @property
    def usage(self) -> llm.TokenUsage:
        """그래프가 지금까지 청구된 토큰. **예외가 나도 읽을 수 있다.**

        `toolbox.round_count`와 같은 자리다 — 실패한 대화는 최종 상태를 못 받으므로
        원장에 실을 값은 그래프 밖에 살아야 한다(`modules/llm.py`).
        """
        return llm.token_usage(self._usage)

    @staticmethod
    def build_messages(
        *,
        run_slot: RunSlot,
        as_of_at: datetime,
        subjects: Sequence[Subject],
        observed_state: ObservedState | NxtObservedState,
        past_theses: Mapping[str, Sequence[PastThesis]],
        same_day: Mapping[str, Sequence[SameDayThesis]] = MappingProxyType({}),
    ) -> list[BaseMessage]:
        """`past_theses`는 subject 코드별 과거 추론 목록(`thesis.past_theses`의 행)이다.

        빈 매핑이면 그 절에 `NO_PAST_THESES`가 들어간다. 장후 리뷰가 그 경우다.

        `same_day`는 **오늘 앞 슬롯**의 추론과 그 뒤 실현 등락이다. 장중 슬롯만 채우고
        나머지는 비운다. 저장된 채점이 아니라 봉에서 계산한 중간 경과라 `past_theses`와
        절을 나눈다 — 섞으면 모델이 채점된 값으로 읽는다.

        **모양은 모델이 정한다**(`thesis.state`). 여기서 하는 것은 JSON으로 바꾸는 것뿐이다.
        """
        subject_lines = "\n".join(f"- {subject.code} ({subject.label}, {subject.kind.value})" for subject in subjects)
        return [
            SystemMessage(SYSTEM_PROMPT),
            HumanMessage(
                PROMPTS.render(
                    "instruction",
                    slot_instruction=SLOT_INSTRUCTION[run_slot],
                    as_of_at=kst_label(as_of_at),
                    subjects=subject_lines or "(없음)",
                    observed_state=json_dump(observed_state.model_dump(mode="json")),
                    same_day=_json_section(same_day),
                    past_theses=_json_section(past_theses),
                )
            ),
        ]

    def run(
        self,
        *,
        run_slot: RunSlot,
        as_of_at: datetime,
        subjects: Sequence[Subject],
        observed_state: ObservedState | NxtObservedState,
        past_theses: Mapping[str, Sequence[PastThesis]],
        same_day: Mapping[str, Sequence[SameDayThesis]] = MappingProxyType({}),
    ) -> "Investigation":
        """추론들과 조사 결과. 두 번째도 실패하면 `ThesisError`를 올린다."""
        if not subjects:
            return Investigation(drafts=(), tool_rounds=0, truncated=False, subjects_requested=0)
        state: ThesisState = {
            "messages": self.build_messages(
                run_slot=run_slot,
                as_of_at=as_of_at,
                subjects=subjects,
                observed_state=observed_state,
                past_theses=past_theses,
                same_day=same_day,
            ),
            "subjects": tuple(subjects),
            "tool_rounds": 0,
            "missing_subjects": (),
            "partial_drafts": (),
            "investigation_truncated": False,
            "drafts": None,
            "error": None,
            "attempts": 0,
        }
        final = self._graph.invoke(
            state,
            config={
                "run_name": "build_theses",
                "metadata": {"run_slot": run_slot.value, "subjects": len(subjects)},
                "callbacks": [self._usage],
            },
        )
        drafts = final.get("drafts")
        if drafts is None:
            raise ThesisError(final.get("error") or "Model did not return any thesis")
        return Investigation(
            drafts=drafts,
            tool_rounds=final["tool_rounds"],
            truncated=bool(final.get("investigation_truncated")),
            subjects_requested=len(subjects),
        )

    def parse(self, raw: str, subjects: Sequence[Subject]) -> tuple[ThesisDraft, ...]:
        """응답을 검증하고 쓸 수 없는 항목을 버린다.

        전부 버려지면 `ThesisError`다. 그건 모델이 요청을 안 보고 답했다는 뜻이라 교정을
        요청할 값어치가 있다. 반대로 **일부만 남는 것은 정상이다** — 요청 목록에 있는데 답에
        없는 subject는 그 슬롯에 없던 것으로 남기고 재요청하지 않는다.
        """
        try:
            parsed = Answers.model_validate_json(json_object(raw))
        except SchemaError as error:
            raise ThesisError(str(error)) from error
        except ValidationError as error:
            raise ThesisError(f"Model returned an unusable object: {error}") from error

        by_code = {subject.code: subject for subject in subjects}
        seen: set[str] = set()
        duplicated: set[str] = set()
        drafts: list[ThesisDraft] = []
        dropped: list[str] = []

        for answer in parsed.theses:
            subject = by_code.get(answer.subject_code)
            if subject is None:
                dropped.append(f"{answer.subject_code}(목록 밖)")
                continue
            if answer.subject_code in seen:
                # 어느 쪽이 진짜인지 알 수 없다. 먼저 넣은 것도 함께 뺀다.
                duplicated.add(answer.subject_code)
                dropped.append(f"{answer.subject_code}(중복)")
                continue
            seen.add(answer.subject_code)
            probabilities = normalize_probabilities(answer.prob_up, answer.prob_down, answer.prob_flat)
            if probabilities is None:
                dropped.append(f"{answer.subject_code}(확률 합 {answer.prob_up + answer.prob_down + answer.prob_flat})")
                continue
            reasonings = (
                _shorten(answer.up_reasoning),
                _shorten(answer.down_reasoning),
                _shorten(answer.flat_reasoning),
            )
            # 이유가 셋 다 자리표시자면 확률만 남는다. 그 상태로 저장하면 Slack에 근거 없는
            # 결론 한 줄이 나가고 채점은 그것을 정상 추론으로 센다.
            if all(len(text) < MIN_REASONING_CHARS for text in reasonings):
                dropped.append(f"{answer.subject_code}(이유 없음)")
                continue
            up_return = normalize_return_pct(answer.up_return_pct)
            down_return = normalize_return_pct(answer.down_return_pct)
            # 크기 하나가 규칙을 어겨도 추론은 살린다(`normalize_return_pct`). 다만 조용히
            # 버리면 프롬프트가 안 먹히는 것을 못 본다.
            if (answer.up_return_pct is not None and up_return is None) or (
                answer.down_return_pct is not None and down_return is None
            ):
                logger.warning(
                    "%s dropped out-of-range return sizes: up=%s down=%s",
                    answer.subject_code,
                    answer.up_return_pct,
                    answer.down_return_pct,
                )
            drafts.append(
                ThesisDraft(
                    subject=subject,
                    prob_up=probabilities[0],
                    prob_down=probabilities[1],
                    prob_flat=probabilities[2],
                    up_return_pct=up_return,
                    down_return_pct=down_return,
                    up_reasoning=reasonings[0],
                    down_reasoning=reasonings[1],
                    flat_reasoning=reasonings[2],
                    claims=self._known_claims(answer),
                )
            )

        kept = tuple(draft for draft in drafts if draft.subject.code not in duplicated)
        if dropped:
            logger.warning("dropped %s theses: %s", len(dropped), dropped)
        if parsed.theses and not kept:
            raise ThesisError(f"Model returned {len(parsed.theses)} theses, none of them usable")
        return kept

    def _known_claims(self, answer: ThesisAnswer) -> tuple[Claim, ...]:
        """레지스트리에 있는 ref의 인용만, ref 첫 등장 순서로 중복 없이.

        순서가 곧 `thesis_evidence.rank`다. 같은 ref를 두 번 인용하면 **첫 것이 남는다** — 행이
        ref당 하나라 방향 둘을 담을 수 없다. 목록 밖 ref는 버리고 건수를 로그로 남긴다 —
        조용히 버리면 모델이 무엇을 지어내는지 알 수 없다.
        """
        registry = self._toolbox.registry
        kept: dict[str, Claim] = {}
        unknown: list[str] = []
        for claim in answer.claims:
            if claim.ref not in registry:
                unknown.append(claim.ref)
            elif claim.ref not in kept:
                kept[claim.ref] = Claim(
                    ref=claim.ref,
                    direction=ThesisDirection(claim.direction),
                    mechanism=_shorten_to(claim.mechanism, MAX_MECHANISM_CHARS),
                )
        if unknown:
            logger.warning("%s cited %s refs that no tool returned: %s", answer.subject_code, len(unknown), unknown)
        return tuple(kept.values())

    def _build_graph(self):
        graph = StateGraph(ThesisState)
        graph.add_node("investigate", self._investigate)
        graph.add_node("tools", self._tools)
        graph.add_node("answer", self._answer)
        graph.add_node("repair", self._repair)
        # 상한에서 끊긴 사실을 남기는 노드. 조건부 엣지는 상태를 못 바꾸므로 답변 앞에 둔다.
        graph.add_node("close_investigation", self._mark_truncation)
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

    def _investigate(self, state: ThesisState) -> dict[str, Any]:
        """툴만 바인딩해 부른다. 스키마는 넣지 않는다(`llm.invoke`가 막는다)."""
        reply = llm.invoke(self._model, state["messages"], tools=self._toolbox.tools)
        return {"messages": [reply]}

    def _tools(self, state: ThesisState) -> dict[str, Any]:
        """`ToolNode`가 tool_call을 돌리고 `ToolMessage`를 만든다. 우리는 왕복만 센다.

        **`tool_call_id`마다 `ToolMessage`가 정확히 하나**여야 하는 것도 `ToolNode`가
        보장한다. 손으로 짜던 때는 그것이 우리 책임이었다.

        `handle_tool_errors`에 타입을 준 것이 이 노드의 핵심이다 — `ToolLimitExceeded`만
        오류 `ToolMessage`가 되어 모델이 고쳐 부를 기회를 얻고, **DB 오류는 그대로 올라가
        태스크를 죽인다.** 기본값(`True`)은 둘을 가르지 않아 연결 끊김이 "결과 없음"으로
        위장된다.
        """
        reply = state["messages"][-1]
        # 원장(13단계)이 여기서 열리고 닫힌다. **래퍼만으로는 부족하다** — 모르는 툴과
        # 인자 검증 실패는 함수에 도달하기 전에 `ToolNode`가 오류 `ToolMessage`로 바꾼다.
        self._toolbox.begin_round(getattr(reply, "tool_calls", None) or [])
        update = self._tool_node.invoke(state)
        self._toolbox.finish_round(update["messages"])
        return {"messages": update["messages"], "tool_rounds": state["tool_rounds"] + 1}

    def _answer(self, state: ThesisState) -> dict[str, Any]:
        """툴을 빼고 스키마를 강제한다. 제공처가 스키마를 안 받으면 그때만 한 번 더."""
        messages = state["messages"]
        try:
            reply = llm.invoke(self._model, messages, schema=self._schema)
        except UnsupportedResponseFormat as error:
            logger.warning("provider does not accept a response schema; falling back to validation: %s", error)
            reply = llm.invoke(self._model, messages)

        previous = state["partial_drafts"]
        try:
            drafts = self.parse(_text(reply), state["subjects"])
        except ThesisError as error:
            # 교정본을 못 읽었는데 첫 답이 있으면 그것을 쓴다. 하나라도 남기는 편이
            # 태스크를 죽이는 것보다 낫다 — 첫 답은 이미 검증을 통과한 것이다.
            if previous:
                logger.warning("교정 답을 읽지 못해 첫 답 %s건을 그대로 쓴다: %s", len(previous), error)
                return {"messages": [reply], "drafts": previous, "error": None}
            return {"messages": [reply], "drafts": None, "error": str(error)}

        missing = missing_subjects(state["subjects"], drafts)
        if missing:
            # **요청한 대상이 안 온 것을 여기서 처음 센다.** `parse`는 온 것 중 버린 것만
            # 세므로, 모델이 넷 중 하나만 답해도 지금까지는 아무 데도 남지 않았다
            # (2026-08-27 `intraday_midday` 실측: 넷을 조사하고 하나만 답했다).
            logger.warning(
                "모델이 대상 %s개 중 %s개만 답했다. 빠진 것: %s",
                len(state["subjects"]),
                len(drafts),
                list(missing),
            )

        # 모자란 첫 답은 형식 실패와 같게 다룬다 — 대상 넷을 요청했으면 넷이 와야 한다.
        # 교정은 한 번뿐이고, 그 답이 더 적으면 첫 답으로 되돌아간다.
        if missing and state["attempts"] == 0:
            return {
                "messages": [reply],
                "drafts": None,
                "error": f"대상 {len(missing)}개가 빠졌다: {', '.join(missing)}",
                "missing_subjects": missing,
                "partial_drafts": drafts,
            }
        if len(drafts) < len(previous):
            logger.warning("교정 답이 %s건으로 더 적어 첫 답 %s건을 쓴다", len(drafts), len(previous))
            return {"messages": [reply], "drafts": previous, "error": None}
        return {"messages": [reply], "drafts": drafts, "error": None}

    def _repair(self, state: ThesisState) -> dict[str, Any]:
        """한 번만 다시 묻는다. **무엇이 잘못됐는지에 따라 문구가 다르다.**

        형식이 깨진 것과 대상이 모자란 것은 모델이 고쳐야 할 것이 다르다. 같은 문구를
        주면 "JSON 하나만 내라"는 말을 듣고 다시 하나만 낸다.
        """
        missing = state["missing_subjects"]
        instruction = (
            PROMPTS.render_variant("repair_short_answer", missing=", ".join(missing))
            if missing
            else REPAIR_INSTRUCTION
        )
        logger.warning("retrying the theses once after %s", state["error"])
        return {
            "messages": [HumanMessage(instruction)],
            "attempts": state["attempts"] + 1,
        }

    @staticmethod
    def _after_investigate(state: ThesisState) -> str:
        """툴을 부르자고 했고 왕복 상한이 남았으면 조사를 잇는다."""
        reply = state["messages"][-1]
        if getattr(reply, "tool_calls", None) and state["tool_rounds"] < MAX_TOOL_ROUNDS:
            return "tools"
        return "answer"

    @staticmethod
    def _mark_truncation(state: ThesisState) -> dict[str, Any]:
        """상한에서 끊겼으면 그 사실을 상태에 남긴다.

        **경로 판정(`_after_investigate`)에서 쓰기를 하지 않는다** — 조건부 엣지의 반환값은
        다음 노드 이름이라 상태를 못 바꾼다. 그래서 답변 노드 앞에 이 노드를 둔다.
        """
        reply = state["messages"][-1]
        if getattr(reply, "tool_calls", None) and state["tool_rounds"] >= MAX_TOOL_ROUNDS:
            logger.warning(
                "investigation truncated: the model asked for more tools after %s rounds", state["tool_rounds"]
            )
            return {"investigation_truncated": True}
        return {}

    @staticmethod
    def _after_answer(state: ThesisState) -> str:
        if state["drafts"] is not None:
            return END
        return "repair" if state["attempts"] == 0 else END


def _text(reply: AIMessage) -> str:
    """응답 본문. 제공처에 따라 문자열이 아니라 조각 리스트로 온다."""
    content = reply.content
    if isinstance(content, str):
        return content
    return "".join(part if isinstance(part, str) else part.get("text", "") for part in content)
