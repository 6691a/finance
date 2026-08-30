"""LLM에게 한 주를 되짚게 하고 답을 검증한다.

**LangChain을 import한다.** 그래서 `domain.py`가 이 모듈을 모르고, DAG 테스트와 순수 함수
테스트가 이 무게 없이 돈다.

계약은 `docs/analysis/market-causal-graph.md` 4·5절이다. 프롬프트 문장은 코드가 아니라
`modules/prompts/causal_graph.yaml`에 있다.
"""

import logging
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel, Field

from modules import llm
from modules.causal.domain import (
    EVENT_LOOKBACK_WEEKS,
    MAX_CHAIN,
    MAX_LINK_PATHS,
    MAX_PATHS,
    MAX_REASONING_CHARS,
    MAX_TOOL_ROUNDS,
    PROMPT_VERSION,
    CandidateSet,
    CausalTarget,
    CausalWindow,
    ChannelOption,
    DailyClose,
    EventOption,
    LinkedPath,
    NodeChoice,
    TargetReturns,
    VerifiedPath,
    close_direction,
    crosses_session,
)
from modules.causal.toolbox import CausalToolbox, ToolLimitExceeded
from modules.prompt import read_prompt
from modules.schema import json_object, response_format

logger = logging.getLogger(__name__)

# import 시점에 읽고 검증한다. 칸이 빠지면 이 모듈을 쓰는 DAG이 DagBag 단계에서 죽고,
# 그것이 실행 중에 프롬프트가 비는 것보다 낫다.
PROMPTS = read_prompt("causal_graph")

# 후보 줄에 싣는 공시 본문 길이. **저장은 안 자른다** — `disclosure_event.body`가 원문 전체를
# 갖고 여기서만 자른다. 자르는 자리를 읽는 쪽에 두어야 프롬프트 예산을 바꿀 때 재수집이
# 필요 없다.
#
# 문서 요약(220자)보다 길게 주는 이유는 셋이다 — 공시는 주당 몇 건뿐이라 예산을 덜 먹고,
# 문장이 아니라 표를 편 텍스트라 앞부분에 머리말이 붙고, 값어치가 숫자에 있다.
#
# **1,200이다.** 1,000에서는 자기주식처분결정의 `처분금액`이 @1065로 65자 차이에 잘렸다
# (2026-08-29 실측). 나머지는 1,000 안에 들어온다 — 대량보유보고서의 `보유비율`이 @260,
# 자기주식취득결정의 `취득예정금액`이 @305·@317, 파생상품거래손실발생은 921자라 통째로다.
#
# 올리는 값이 싼 이유는 공시 후보가 주당 서너 건이기 때문이다. 200자를 더 줘도 600자다.
# 문서 후보 60건이 220자씩 쓰는 것과 자릿수가 다르다.
MAX_DISCLOSURE_BODY_CHARS = 1200


class CausalPathAnswer(BaseModel):
    """모델이 낸 경로 하나. 저장 전에 `verify_paths`가 거른다."""

    # **중첩 모델에 `Field(description=...)`을 붙이지 않는다.** 스키마가 `$ref` 옆에
    # `description`을 두는데 OpenAI가 그것을 거절한다. 설명은 프롬프트가 한다.
    event: NodeChoice
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
        f"[{item.ref}] ({', '.join(item.tags) or '태그 없음'}, "
        f"{item.published_at:%Y-%m-%d %H:%M}, score {item.value_score}, "
        f"평가방향 {item.assessed_direction}) {item.title}\n"
        f"    {item.summary[:220]}"
        for item in found.documents
    ]
    lines += [
        f"[{item.ref}] ({item.target_code}, {item.receipt_date}) "
        f"{item.company_name} — {item.report_name}\n"
        f"    {item.body[:MAX_DISCLOSURE_BODY_CHARS]}"
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


def reply_text(reply: Any) -> str:
    """응답 본문. **Responses API는 `content`가 블록 리스트다.**

    `use_responses_api=True`로 옮긴 뒤(툴을 쓰려면 그래야 한다 — `llm.causal_model` 주석)
    `content`가 `[{"type": "reasoning", …}, {"type": "text", "text": "…"}]` 모양으로 온다.
    `str()`을 씌우면 파이썬 repr가 되어 JSON 파싱이 `key must be a string`으로 죽는다.

    `briefing/disclosure_picks._text`가 같은 일을 한다 — 제공처에 따라 조각 리스트로 오는
    것을 이미 겪은 자리다.
    """
    content = reply.content
    if isinstance(content, str):
        return content
    return "".join(
        part if isinstance(part, str) else part.get("text", "") for part in content
    )


def target_block(targets: Iterable[CausalTarget]) -> str:
    """대상 목록. 모델이 `target_code`로 쓸 수 있는 것이 이것뿐이다."""
    return "\n".join(f"  - {target.code} ({target.kind})" for target in targets)


class LinkPathAnswer(BaseModel):
    """링커가 낸 경로 하나. **출발점이 사건이 아니라 앞 답이 낸 경로의 대상이다**(설계 §11.4).

    `source_date`·`target_date`를 **칸으로 받는다.** 프로토타입은 날짜를 `reasoning` 산문에만
    적게 했는데, 그러면 `endpoint_observed`가 맞는지를 사람이 읽어야만 안다. 칸으로 받으면
    코드가 종가와 대조해 스스로 판정한다.
    """

    source_target_code: str = Field(description="원인이 된 대상 코드. 방금 낸 경로의 대상만")
    source_target_kind: Literal["instrument", "index", "quote", "indicator"]
    source_sign: Literal["up", "down"]
    source_date: str = Field(description="원인 대상이 움직인 날 YYYY-MM-DD. 대상 주 안이어야 한다")
    channels: list[str] = Field(
        description=(
            f"원인에서 결과로 가는 전달 경로를 순서대로. 1~{MAX_CHAIN}단. "
            "방금 낸 경로에 쓴 이름이 맞으면 글자 그대로 다시 쓴다"
        )
    )
    target_kind: Literal["instrument", "index", "quote", "indicator"]
    target_code: str = Field(description="결과 대상 코드. source_target_code와 달라야 한다")
    target_date: str = Field(description="결과 대상이 움직인 날 YYYY-MM-DD. 대상 주 안이어야 한다")
    sign: Literal["up", "down"]
    confidence: Literal["endpoint_observed", "plausible"]
    reasoning: str = Field(description=f"이 연결 한 문장. 최대 {MAX_REASONING_CHARS}자")
    evidence_refs: list[str] = Field(
        default_factory=list,
        description="근거 ref. 반드시 주어진 후보 목록 안의 값. 없으면 빈 목록",
    )


class LinkAnswer(BaseModel):
    """링커가 낸 연결 전부. **빈 목록이 정상 답이다.**"""

    paths: list[LinkPathAnswer] = Field(description=f"최대 {MAX_LINK_PATHS}개. 없으면 빈 목록")


def answered_block(paths: Sequence[VerifiedPath], names: Mapping[str, str]) -> str:
    """첫 답이 낸 경로. **채널을 id가 아니라 이름으로 보여 준다.**

    `market_channel`의 자연키가 이름이라 링커는 이름만 내면 되고 저장 쪽이 같은 upsert로
    한 노드에 붙인다. 그런데 첫 답이 기존 채널을 고르면 `c:<id>`만 들고 있고, 새로 만든
    채널은 저장 전이라 id 자체가 없다. **양쪽을 이름으로 통일해 보여 준다** — 한 글자만
    달라도 `store._upsert_channel`이 새 행을 만든다.
    """
    lines = []
    for path in paths:
        chain = " > ".join(
            channel.new_name or names.get(channel.existing_id, channel.existing_id)
            for channel in path.channels
        )
        lines.append(f"  {path.target_code} {path.sign}  <-- {chain}")
    return "\n".join(lines) or "  (없음)"


def price_block(prices: Mapping[str, tuple[DailyClose, ...]]) -> str:
    """대상별 그 주 일별 종가. **모델이 물어야 보이는 값이 아니라 언제나 있는 값이다.**"""
    lines = [
        f"  {code}: " + "  ".join(f"{row.business_date:%m/%d} {row.close:g}" for row in rows)
        for code, rows in prices.items()
        if rows
    ]
    return "\n".join(lines) or "  (없음)"


def verify_links(
    paths: Iterable[LinkPathAnswer],
    *,
    window: CausalWindow,
    answered: set[str],
    target_codes: set[str],
    prices: Mapping[str, tuple[DailyClose, ...]],
    found: CandidateSet,
) -> tuple[tuple[LinkedPath, ...], tuple[str, ...]]:
    """저장할 수 있는 연결만 남기고 버린 사유를 함께 돌려준다.

    **프롬프트에만 두지 않는다**(설계 §11.4). §4.1이 "`verify_paths`가 사슬과 `reasoning`의
    일치를 못 잡는다"를 결함으로 적어 뒀는데, 같은 형태를 하나 더 만들지 않는다.

    **`endpoint_observed`는 모델이 주장하고 코드가 확인한다.** 종가가 그 날짜에 그 방향으로
    움직이지 않았으면 경로를 버리지 않고 `plausible`로 내린다 — 연결 자체는 틀리지 않았고
    "값이 그렇게 보였다"만 못 미더운 것이라, 버리면 관측을 잃는다.
    """
    registry = set(found.refs)
    kept: list[LinkedPath] = []
    dropped: list[str] = []

    def drop(path: LinkPathAnswer, why: str) -> None:
        dropped.append(f"{path.source_target_code}->{path.target_code}({why})")

    for path in paths:
        if path.source_target_code not in answered:
            drop(path, "앞 답의 대상이 아니다")
            continue
        if path.target_code not in target_codes:
            drop(path, "대상 목록 밖")
            continue
        if path.target_code == path.source_target_code:
            drop(path, "자기 자신으로 돌아온다")
            continue
        if not 1 <= len(path.channels) <= MAX_CHAIN:
            drop(path, f"사슬 {len(path.channels)}단")
            continue
        if any(not channel.strip() for channel in path.channels):
            drop(path, "빈 채널 이름")
            continue
        source_on = _parse_day(path.source_date, window)
        target_on = _parse_day(path.target_date, window)
        if source_on is None or target_on is None:
            drop(path, "날짜가 대상 주 밖")
            continue
        if source_on > target_on:
            drop(path, "원인이 결과보다 늦다")
            continue
        # **해외 종가는 KRX보다 늦게 정해진다.** 같은 날짜면 순서가 거꾸로다(설계 §11.5).
        if crosses_session(path.source_target_code, path.target_code) and source_on == target_on:
            drop(path, "해외→국내인데 같은 날")
            continue
        confidence = path.confidence
        if confidence == "endpoint_observed" and not _moved_as_claimed(path, source_on, target_on, prices):
            confidence = "plausible"
        kept.append(
            LinkedPath(
                source_target_kind=path.source_target_kind,
                source_target_code=path.source_target_code,
                source_sign=path.source_sign,
                channels=tuple(channel.strip() for channel in path.channels),
                target_kind=path.target_kind,
                target_code=path.target_code,
                sign=path.sign,
                confidence=confidence,
                reasoning=path.reasoning[:MAX_REASONING_CHARS],
                evidence_refs=tuple(ref for ref in path.evidence_refs if ref in registry),
            )
        )
    return tuple(kept), tuple(dropped)


def _parse_day(value: str, window: CausalWindow) -> date | None:
    try:
        parsed = date.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed if window.week_start <= parsed <= window.week_end else None


def _moved_as_claimed(
    path: LinkPathAnswer,
    source_on: date,
    target_on: date,
    prices: Mapping[str, tuple[DailyClose, ...]],
) -> bool:
    """그 날짜에 두 값이 실제로 그 방향으로 움직였나. **여기가 `endpoint_observed`의 근거다.**"""
    source = close_direction(prices.get(path.source_target_code, ()), source_on)
    target = close_direction(prices.get(path.target_code, ()), target_on)
    return source == path.source_sign and target == path.sign


class CausalState(TypedDict):
    """되짚기 한 번의 상태. **설정 객체를 넣지 않는다** — 상태는 트레이스 입력으로 나간다.

    `found`와 `target_codes`는 노드가 답을 거를 때 쓰므로 상태에 있어야 한다.

    **`messages`에 `add_messages` 리듀서를 단다.** 노드는 새로 생긴 메시지만 돌려주고 병합은
    리듀서가 한다 — `ToolNode`가 그 형태로 반환하므로 맞출 쪽은 우리다.
    """

    messages: Annotated[list[BaseMessage], add_messages]
    found: CandidateSet
    target_codes: frozenset[str]
    paths: tuple[VerifiedPath, ...] | None
    attempts: int
    tool_rounds: int
    window: CausalWindow
    prices: dict[str, tuple[DailyClose, ...]]
    channel_names: dict[str, str]
    links: tuple[LinkedPath, ...]


class CausalBuilder:
    """한 주를 되짚는 대화 하나를 소유한다.

    **툴을 바인딩하지 않는다.** 후보를 코드가 이미 좁혀 실었고, 8주 프로토타입이 그 형태로
    돌아 어휘 수렴과 사슬 깊이를 확인했다. 툴을 늘릴지는 첫 실행들의 원장을 보고 정한다
    (설계 §5.2).

    **재시도는 Airflow가 한다.** 여기서 도는 것은 교정 한 번뿐이고, 그것도 답이 비었을 때다.
    """

    def __init__(self, model: BaseChatModel, toolbox: CausalToolbox | None = None) -> None:
        self._model = model
        self._schema = response_format(CausalAnswer, "market_causal_paths")
        self._link_schema = response_format(LinkAnswer, "market_causal_links")
        self._toolbox = toolbox
        tools = toolbox.tools if toolbox else []
        # **타입을 준다.** 기본값(`True`)은 DB 연결 끊김을 "결과 없음"으로 위장한다.
        self._tool_node = ToolNode(tools, handle_tool_errors=(ToolLimitExceeded,))
        self._graph = self._build_graph()

    def build(
        self,
        *,
        window: CausalWindow,
        returns: Mapping[str, TargetReturns],
        found: CandidateSet,
        events: Sequence[EventOption],
        channels: Sequence[ChannelOption],
        targets: Sequence[CausalTarget],
        prices: Mapping[str, tuple[DailyClose, ...]] | None = None,
    ) -> tuple[tuple[VerifiedPath, ...], tuple[LinkedPath, ...]]:
        """그 주의 경로와 대상→대상 연결. 검증을 마친 것만 돌려준다.

        **검증에 쓰는 대상 집합은 `targets`가 아니라 `returns`의 키다.** 실현 등락이 없는
        대상은 저장할 수 없으므로(설계 §6) 프롬프트에도 보여 주지 않는다.

        **이름은 여기 한 자리에만 붙인다.** 호출마다 `with_config`로 손으로 붙이고 있으면
        그건 흐름이 그래프가 아니라는 신호다 — 노드 이름이 그 일을 한다.
        """
        target_codes = frozenset(returns)
        state: CausalState = {
            "messages": self.build_messages(
                window=window,
                returns=returns,
                found=found,
                events=events,
                channels=channels,
                targets=[target for target in targets if target.code in target_codes],
            ),
            "found": found,
            "target_codes": target_codes,
            "paths": None,
            "attempts": 0,
            "tool_rounds": 0,
            "window": window,
            "prices": dict(prices or {}),
            "channel_names": {option.node_id: option.name for option in channels},
            "links": (),
        }
        final = self._graph.invoke(
            state,
            config={
                "run_name": f"causal {window.week_start.isoformat()}",
                "tags": ["causal", f"prompt_v{PROMPT_VERSION}"],
                "metadata": {
                    "week_start": window.week_start.isoformat(),
                    "prompt_version": PROMPT_VERSION,
                },
            },
        )
        return final.get("paths") or (), final.get("links") or ()

    @staticmethod
    def build_messages(
        *,
        window: CausalWindow,
        returns: Mapping[str, TargetReturns],
        found: CandidateSet,
        events: Sequence[EventOption],
        channels: Sequence[ChannelOption],
        targets: Sequence[CausalTarget],
    ) -> list[BaseMessage]:
        """첫 대화. 상태가 없으므로 정적 메서드다."""
        return [
            SystemMessage(
                PROMPTS.render(
                    "system",
                    max_chain=MAX_CHAIN,
                    max_paths=MAX_PATHS,
                    max_reasoning_chars=MAX_REASONING_CHARS,
                    max_tool_rounds=MAX_TOOL_ROUNDS,
                    targets=target_block(targets),
                )
            ),
            HumanMessage(
                PROMPTS.render(
                    "instruction",
                    week_start=window.week_start.isoformat(),
                    week_end=window.week_end.isoformat(),
                    vocabulary=vocabulary_block(events=events, channels=channels),
                    returns=returns_block(returns),
                    candidates=candidate_block(found),
                )
            ),
        ]

    def _build_graph(self):
        """`investigate` → 조건부 `tools` → `answer` → 조건부 `repair`.

        저장소의 툴 붙은 흐름 둘(`thesis/generation`·`thesis/outcomes`)과 같은 모양이다.
        뒤쪽 둘은 툴 없는 다섯과 글자 그대로 같다.
        """
        graph = StateGraph(CausalState)
        graph.add_node("investigate", self._investigate)
        graph.add_node("tools", self._tools)
        graph.add_node("answer", self._answer)
        graph.add_node("repair", self._repair)
        graph.add_node("link", self._link)
        graph.add_edge(START, "investigate")
        graph.add_conditional_edges(
            "investigate", self._after_investigate, {"tools": "tools", "answer": "answer"}
        )
        graph.add_edge("tools", "investigate")
        # **`repair`는 `answer`의 것이고 `link`는 타지 않는다**(설계 §11.3). 링커가 0건을
        # 내는 것은 "이을 것이 없다"는 정상 답이라, 다시 물으면 없는 것을 만든다.
        graph.add_conditional_edges(
            "answer", self._next, {"repair": "repair", "link": "link", END: END}
        )
        graph.add_edge("repair", "answer")
        graph.add_edge("link", END)
        return graph.compile()

    def _investigate(self, state: CausalState) -> dict[str, Any]:
        """툴만 바인딩해 부른다. **스키마는 넣지 않는다**(`llm.invoke`가 막는다).

        툴이 없으면(`toolbox=None`) 부르지 않고 바로 답변으로 넘어간다 — 테스트와 툴을 안 쓰는
        경로가 그 형태다.
        """
        if not self._toolbox:
            return {}
        reply = llm.invoke(self._model, state["messages"], tools=self._toolbox.tools)
        return {"messages": [reply]}

    def _tools(self, state: CausalState) -> dict[str, Any]:
        """`ToolNode`가 tool_call을 돌리고 `tool_call_id`마다 `ToolMessage` 하나를 보장한다."""
        update = self._tool_node.invoke(state)
        return {"messages": update["messages"], "tool_rounds": state["tool_rounds"] + 1}

    @staticmethod
    def _after_investigate(state: CausalState) -> str:
        """툴을 부르자고 했고 왕복 상한이 남았으면 조사를 잇는다."""
        messages = state["messages"]
        reply = messages[-1] if messages else None
        if getattr(reply, "tool_calls", None) and state["tool_rounds"] < MAX_TOOL_ROUNDS:
            return "tools"
        if getattr(reply, "tool_calls", None):
            logger.warning(
                "causal investigation truncated: the model asked for more tools after %s rounds",
                state["tool_rounds"],
            )
        return "answer"

    def _answer(self, state: CausalState) -> dict[str, Any]:
        """스키마를 강제해 답을 받고 검증한다. 남은 것이 없으면 `paths`가 빈 튜플이다."""
        messages = state["messages"]
        reply = llm.invoke(self._model, messages, schema=self._schema)
        answer = CausalAnswer.model_validate_json(json_object(reply_text(reply)))
        verified = verify_paths(
            tuple(answer.paths[:MAX_PATHS]), state["found"], state["target_codes"]
        )
        return {"messages": [reply], "paths": verified}

    def _repair(self, state: CausalState) -> dict[str, Any]:
        """무엇이 잘못됐는지를 실어 한 번만 다시 묻는다(thesis 판 11의 교훈)."""
        logger.warning("causal answer had no usable 경로; asking once more")
        return {
            "messages": [
                HumanMessage(
                    PROMPTS.render(
                        "repair",
                        reason=(
                            "쓸 수 있는 경로가 하나도 없었다. 대상은 위 목록 안의 값이어야 "
                            f"하고, 경로(channels)는 1~{MAX_CHAIN}단이어야 한다."
                        ),
                    )
                ),
            ],
            "attempts": state["attempts"] + 1,
        }

    def _link(self, state: CausalState) -> dict[str, Any]:
        """대상이 다시 원인이 된 자리만 한 번 더 묻는다(설계 §11.3).

        **같은 대화다.** 조사 단계가 본 것을 그대로 쥔 채 묻는 것이 도메인별로 쪼갠 뒤
        병합하는 것과 갈리는 지점이고, 그 하나가 순차를 고른 이유 전부다.
        """
        message = HumanMessage(
            PROMPTS.render_variant(
                "link",
                paths=answered_block(state["paths"] or (), state["channel_names"]),
                prices=price_block(state["prices"]),
                max_chain=MAX_CHAIN,
                max_link_paths=MAX_LINK_PATHS,
                max_reasoning_chars=MAX_REASONING_CHARS,
            )
        )
        reply = llm.invoke(self._model, [*state["messages"], message], schema=self._link_schema)
        answer = LinkAnswer.model_validate_json(json_object(reply_text(reply)))
        links, dropped = verify_links(
            answer.paths[:MAX_LINK_PATHS],
            window=state["window"],
            answered={path.target_code for path in state["paths"] or ()},
            target_codes=set(state["target_codes"]),
            prices=state["prices"],
            found=state["found"],
        )
        if dropped:
            logger.warning("dropped %d linked paths: %s", len(dropped), "; ".join(dropped))
        return {"messages": [message, reply], "links": links}

    @staticmethod
    def _next(state: CausalState) -> str:
        if state["paths"]:
            return "link"
        return "repair" if state["attempts"] == 0 else END
