"""LLM에게 한 주를 되짚게 하고 답을 검증한다.

**LangChain을 import한다.** 그래서 `domain.py`가 이 모듈을 모르고, DAG 테스트와 순수 함수
테스트가 이 무게 없이 돈다.

계약은 `docs/analysis/market-causal-graph.md` 4·5절이다. 프롬프트 문장은 코드가 아니라
`modules/prompts/causal_graph.yaml`에 있다.
"""

import logging
from collections.abc import Iterable, Mapping, Sequence
from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field

from modules import llm
from modules.causal.domain import (
    EVENT_LOOKBACK_WEEKS,
    MAX_CHAIN,
    MAX_PATHS,
    MAX_REASONING_CHARS,
    CandidateSet,
    CausalTarget,
    CausalWindow,
    ChannelOption,
    EventOption,
    TargetReturns,
)
from modules.prompt import read_prompt
from modules.schema import json_object, response_format

logger = logging.getLogger(__name__)

# import 시점에 읽고 검증한다. 칸이 빠지면 이 모듈을 쓰는 DAG이 DagBag 단계에서 죽고,
# 그것이 실행 중에 프롬프트가 비는 것보다 낫다.
PROMPTS = read_prompt("causal_graph")


class NodeChoice(BaseModel):
    """사건이나 경로 한 칸. 기존 것을 고르거나 새로 만든다 — 이 한 칸이 §4 전체를 담는다."""

    existing_id: str = Field(
        default="",
        description="후보 목록에 있는 id(e:12 또는 c:3). 새로 만들면 빈 문자열",
    )
    new_name: str = Field(
        default="",
        description="새로 만들 이름. 기존을 고르면 빈 문자열",
    )

    @property
    def is_new(self) -> bool:
        return not self.existing_id and bool(self.new_name)


class CausalPathAnswer(BaseModel):
    """모델이 낸 경로 하나. 저장 전에 `verify_paths`가 거른다."""

    event: NodeChoice = Field(description="사건. 그 주에 실제로 일어난 일")
    event_date: str = Field(
        default="",
        description="사건 날짜 YYYY-MM-DD. 기존 사건을 고르면 빈 문자열",
    )
    channels: list[NodeChoice] = Field(
        description=(
            f"전달 경로를 사건에서 대상 쪽으로 순서대로. 1~{MAX_CHAIN}단. "
            "한 단으로 충분하면 하나만 쓰고 억지로 늘리지 않는다"
        )
    )
    target_kind: Literal["instrument", "index", "quote", "indicator"]
    target_code: str = Field(description="대상 식별자. 주어진 대상 목록 안의 값")
    sign: Literal["up", "down"]
    confidence: Literal["observed", "plausible"]
    reasoning: str = Field(description=f"이 경로 한 문장. 최대 {MAX_REASONING_CHARS}자")
    evidence_refs: list[str] = Field(
        default_factory=list,
        description="근거 ref. 반드시 주어진 후보 목록 안의 값. 없으면 빈 목록",
    )


class CausalAnswer(BaseModel):
    """한 대화가 낸 경로 전부."""

    paths: list[CausalPathAnswer] = Field(description=f"최대 {MAX_PATHS}개")


class VerifiedPath(BaseModel):
    """검증을 마친 경로 하나. 저장 코드는 이것만 본다."""

    model_config = ConfigDict(frozen=True)

    event: NodeChoice
    event_date: str
    channels: tuple[NodeChoice, ...]
    target_kind: str
    target_code: str
    sign: str
    confidence: str
    reasoning: str
    evidence_refs: tuple[str, ...]


def vocabulary_block(
    *,
    events: Sequence[EventOption],
    channels: Sequence[ChannelOption],
) -> str:
    """어휘 후보 블록. **이 블록이 서로 다른 주의 그래프를 잇는 유일한 장치다**(설계 §4).

    첫 주는 목록이 비어 전부 새로 만드는 것이 정상이다. 빈 목록을 그냥 보여 주면 모델이
    "고를 것이 없다"와 "목록이 잘렸다"를 가리지 못하므로 그 사실을 문장으로 밝힌다.
    """
    if not events and not channels:
        return PROMPTS.render_variant("no_vocabulary")
    return PROMPTS.render_variant(
        "vocabulary",
        recent_weeks=EVENT_LOOKBACK_WEEKS,
        events="\n".join(
            f"  {option.node_id} — {option.title} ({option.occurred_on.isoformat()})"
            for option in events
        )
        or "  없음",
        channels="\n".join(f"  {option.node_id} — {option.name}" for option in channels)
        or "  없음",
    )


def candidate_block(found: CandidateSet) -> str:
    """근거 후보 블록. **여기 있는 ref만 인용할 수 있다.**

    후보가 0건인 주도 정상이다(8주 프로토타입의 7/06 주가 그랬다). 그때 빈 목록을 그냥 두면
    모델이 ref를 지어내므로 없다는 것을 명시하고 빈 목록을 쓰라고 말한다.
    """
    lines = [
        f"[{item.ref}] ({item.target_code}, {item.published_at:%Y-%m-%d %H:%M}, "
        f"score {item.value_score}, 평가방향 {item.assessed_direction}) {item.title}\n"
        f"    {item.summary[:220]}"
        for item in found.documents
    ]
    lines += [
        f"[{item.ref}] ({item.target_code}, {item.receipt_date}) "
        f"{item.company_name} — {item.report_name}"
        for item in found.disclosures
    ]
    lines += [
        f"[{item.ref}] ({item.target_code}, {item.signal_date}) {item.kind} {item.direction}"
        for item in found.signals
    ]
    if not lines:
        return PROMPTS.render_variant("no_candidates")
    return PROMPTS.render_variant("candidates", items="\n".join(lines))


def verify_paths(
    paths: Iterable[CausalPathAnswer],
    found: CandidateSet,
    target_codes: set[str],
) -> tuple[VerifiedPath, ...]:
    """저장할 수 있는 경로만 남긴다.

    - **목록 밖 ref는 버린다.** 그것이 모델이 근거를 지어내지 못하게 막는 유일한 장치다.
      근거가 하나도 안 남아도 경로는 살린다 — 실현 등락만으로 설명되는 주가 있다.
    - **마스터 밖 대상은 경로째 버린다.** 저장할 수 없는 값이다.
    - **체인 길이 상한은 여기서 막는다.** 프롬프트에만 적으면 모델이 넘길 때 막을 것이 없다.
    """
    registry = set(found.refs)
    kept: list[VerifiedPath] = []
    for path in paths:
        if path.target_code not in target_codes:
            continue
        if not 1 <= len(path.channels) <= MAX_CHAIN:
            continue
        if any(not (channel.existing_id or channel.new_name) for channel in path.channels):
            continue
        kept.append(
            VerifiedPath(
                event=path.event,
                event_date=path.event_date,
                channels=tuple(path.channels),
                target_kind=path.target_kind,
                target_code=path.target_code,
                sign=path.sign,
                confidence=path.confidence,
                reasoning=path.reasoning[:MAX_REASONING_CHARS],
                evidence_refs=tuple(ref for ref in path.evidence_refs if ref in registry),
            )
        )
    return tuple(kept)


def returns_block(returns: Mapping[str, TargetReturns]) -> str:
    """대상별 실현 등락. **단위를 값 옆에 붙인다** — 가격 퍼센트와 금리 bp가 섞여 있어서
    표기가 없으면 모델이 7bp를 1.75퍼센트로 읽는다."""
    lines = []
    for code, row in returns.items():
        mark = "%" if row.unit == "percent" else "bp"
        lines.append(
            f"  {code}: 주간 {row.week:+g}{mark}"
            f"  T+1 {row.t1:+g}{mark}  T+5 {row.t5:+g}{mark}"
        )
    return "\n".join(lines) or "  (없음)"


def target_block(targets: Iterable[CausalTarget]) -> str:
    """대상 목록. 모델이 `target_code`로 쓸 수 있는 것이 이것뿐이다."""
    return "\n".join(f"  - {target.code} ({target.kind})" for target in targets)


class CausalBuilder:
    """한 주를 되짚는 대화 하나를 소유한다.

    **툴을 바인딩하지 않는다.** 후보를 코드가 이미 좁혀 실었고, 8주 프로토타입이 그 형태로
    돌아 어휘 수렴과 사슬 깊이를 확인했다. 툴을 늘릴지는 첫 실행들의 원장을 보고 정한다
    (설계 §5.2).

    **재시도는 Airflow가 한다.** 여기서 도는 것은 교정 한 번뿐이고, 그것도 답이 비었을 때다.
    """

    def __init__(self, model: BaseChatModel) -> None:
        self._model = model
        self._schema = response_format(CausalAnswer, "market_causal_paths")

    def build(
        self,
        *,
        window: CausalWindow,
        returns: Mapping[str, TargetReturns],
        found: CandidateSet,
        events: Sequence[EventOption],
        channels: Sequence[ChannelOption],
        targets: Sequence[CausalTarget],
    ) -> tuple[VerifiedPath, ...]:
        """그 주의 경로 전부. 검증을 마친 것만 돌려준다.

        **검증에 쓰는 대상 집합은 `targets`가 아니라 `returns`의 키다.** 실현 등락이 없는
        대상은 저장할 수 없으므로(설계 §6) 프롬프트에도 보여 주지 않는다.
        """
        target_codes = set(returns)
        targets = [target for target in targets if target.code in target_codes]
        system = SystemMessage(
            PROMPTS.render(
                "system",
                max_chain=MAX_CHAIN,
                max_paths=MAX_PATHS,
                max_reasoning_chars=MAX_REASONING_CHARS,
                targets=target_block(targets),
            )
        )
        human = HumanMessage(
            PROMPTS.render(
                "instruction",
                week_start=window.week_start.isoformat(),
                week_end=window.week_end.isoformat(),
                vocabulary=vocabulary_block(events=events, channels=channels),
                returns=returns_block(returns),
                candidates=candidate_block(found),
            )
        )

        messages = [system, human]
        paths = self._ask(messages)
        verified = verify_paths(paths, found, target_codes)
        if verified:
            return verified

        # 후보가 있는데 경로가 하나도 안 남았다. 한 번만 다시 묻는다 — 무엇이 잘못됐는지를
        # 실어야 모델이 같은 답을 다시 내지 않는다(thesis 판 11의 교훈).
        logger.warning("causal answer had no usable 경로; asking once more")
        messages = [
            *messages,
            HumanMessage(
                PROMPTS.render(
                    "repair",
                    reason=(
                        "쓸 수 있는 경로가 하나도 없었다. 대상은 위 목록 안의 값이어야 하고, "
                        f"경로(channels)는 1~{MAX_CHAIN}단이어야 한다."
                    ),
                )
            ),
        ]
        return verify_paths(self._ask(messages), found, target_codes)

    def _ask(self, messages: Sequence[object]) -> tuple[CausalPathAnswer, ...]:
        reply = llm.invoke(self._model, messages, schema=self._schema)
        answer = CausalAnswer.model_validate_json(json_object(str(reply.content)))
        return tuple(answer.paths[:MAX_PATHS])

