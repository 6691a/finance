"""문서에서 기대·실제 주장을 뽑는다 — `ExpectationExtractor`.

**문장은 여기 없다.** 프롬프트는 `modules/prompts/expectation_extraction.yaml`이 갖는다.
문장을 고치는 일과 흐름을 고치는 일은 주기가 다르다. 읽는 방법은 `modules/prompt.py`에 있다.
**여기 문장에는 판이 붙는다** — 고치면 `expectation_domain.PROMPT_VERSION`을 올리고
`tests/modules/test_prompt_versions.py`의 해시를 같은 커밋에서 바꾼다.

**검증이 추출의 절반이다.** 모델 응답을 그대로 믿지 않는다. 이벤트·지표는 Literal로 좁히고
조합·기간 표기·단위 정규화·종목 태그 대조는 `filter_claims`가 한다. 목록 밖 값은 그 주장만
버리고 건수를 로그로 남긴다.

**실적(earnings)의 실제값은 추출하지 않는다.** `earnings_fact`가 원본이다. DART 파서가
원문 표에서 정확히 읽는 값을 기사 산문에서 다시 뽑으면 어긋난 쪽을 고를 수 없다.

**배치를 흩지 않는다.** 문서 태깅(`assessment.AssessmentBatch`)은 `Send` 팬아웃을 쓰지만
여기는 순차 루프다. 대상이 종목 태그 문서뿐이라 시간당 수 건이고 동시성이 벌어 줄 시간이 없다.
"""

import json
import logging
from typing import Any, Literal, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, ValidationError

from modules import llm
from modules.expectation_domain import (
    EVENT_METRICS,
    PERIOD_KEY_PATTERN,
    ExtractionError,
    NormalizedClaim,
    PendingExtractionDocument,
    normalize_amount,
)
from modules.llm import UnsupportedResponseFormat
from modules.prompt import read_prompt
from modules.schema import SchemaError, json_object, response_format

logger = logging.getLogger(__name__)


class ExtractedClaim(BaseModel):
    """모델이 낸 주장 하나. 값 집합은 Literal(스키마 enum)이 먼저 막고 조합은 filter_claims가 본다."""

    model_config = ConfigDict(frozen=True)

    stock_code: str
    event_type: Literal["shareholder_return", "earnings", "guidance"]
    period_key: str
    metric: Literal[
        "total_return_amount",
        "buyback_amount",
        "dividend_total",
        "dividend_per_share",
        "revenue",
        "operating_profit",
        "net_income",
    ]
    kind: Literal["expectation", "actual"]
    value: str
    unit: str
    value_low: str | None = None
    value_high: str | None = None
    broker: str | None = None


class ExtractionResponse(BaseModel):
    """모델 응답 전체. 주장이 없는 문서가 대부분이라 빈 배열이 기본이다."""

    model_config = ConfigDict(frozen=True)

    claims: tuple[ExtractedClaim, ...] = ()


PROMPTS = read_prompt("expectation_extraction")

SYSTEM_PROMPT = PROMPTS.system

# 사람이 읽는 지시. 종목 후보는 문서의 태그로 실행 시점에 뒤에 이어 붙인다.
INSTRUCTION = PROMPTS.instruction

REPAIR_INSTRUCTION = PROMPTS.repair


class ExtractState(TypedDict):
    """문서 하나를 추출하는 동안의 상태."""

    messages: list[BaseMessage]
    extraction: ExtractionResponse | None
    error: str | None
    attempts: int


class ExpectationExtractor:
    """문서 하나에서 이벤트 주장을 뽑는다. `DocumentAssessor` 계보의 흐름 클래스다.

    - `call`: 스키마를 강제해 부르고 응답을 검증한다. 제공처가 스키마를 받지 않으면
      스키마 없이 한 번 더 부른다.
    - `repair`: 형식이 깨졌을 때 교정 지시를 붙인다. **한 번만** 붙는다.
    """

    def __init__(self, model: BaseChatModel) -> None:
        self._model = model
        self._schema = response_format(ExtractionResponse, "event_claims")
        self._graph = self._build_graph()

    @staticmethod
    def build_messages(document: PendingExtractionDocument) -> list[BaseMessage]:
        """모델에 보낼 메시지. 종목 후보는 그 문서의 태그다 — 전체 마스터가 아니다."""
        ticker_lines = "\n".join(f"- {ticker}" for ticker in document.tickers)
        parts = [
            INSTRUCTION,
            f"\n## 종목 후보\n{ticker_lines or '(없음)'}",
            f"\n## 문서\n출처: {document.source_slug}",
            f"발행: {document.published_at.isoformat() if document.published_at else '알 수 없음'}",
            f"제목: {document.title}",
        ]
        if document.summary:
            parts.append(f"요약: {document.summary}")
        if document.body:
            parts.append(f"본문: {document.body}")
        return [SystemMessage(SYSTEM_PROMPT), HumanMessage("\n".join(parts))]

    @staticmethod
    def parse(raw: str) -> ExtractionResponse:
        """모델 응답을 검증한다. 스키마 강제가 안 되는 제공처에서는 이것이 유일한 방어다."""
        try:
            return ExtractionResponse.model_validate_json(json_object(raw))
        except SchemaError as error:
            raise ExtractionError(str(error)) from None
        except ValidationError as error:
            raise ExtractionError(f"Model returned invalid claims: {error}") from None
        except json.JSONDecodeError as error:
            raise ExtractionError(f"Model returned malformed JSON: {error}") from None

    def extract(self, document: PendingExtractionDocument) -> ExtractionResponse:
        """문서 하나에서 주장을 뽑는다. 두 번째도 형식이 깨지면 `ExtractionError`를 올린다."""
        state: ExtractState = {
            "messages": self.build_messages(document),
            "extraction": None,
            "error": None,
            "attempts": 0,
        }
        final = self._graph.invoke(
            state,
            config={"run_name": "extract_event_claims", "metadata": {"document_id": document.id}},
        )
        extraction = final.get("extraction")
        if extraction is None:
            raise ExtractionError(final.get("error") or "Model did not return claims")
        return extraction

    def _build_graph(self):
        graph = StateGraph(ExtractState)
        graph.add_node("call", self._call)
        graph.add_node("repair", self._repair)
        graph.add_edge(START, "call")
        graph.add_conditional_edges("call", self._next, {"repair": "repair", END: END})
        graph.add_edge("repair", "call")
        return graph.compile()

    def _call(self, state: ExtractState) -> dict[str, Any]:
        messages = state["messages"]
        try:
            reply = llm.invoke(self._model, messages, schema=self._schema)
        except UnsupportedResponseFormat as error:
            logger.warning("provider does not accept a response schema; falling back to validation: %s", error)
            reply = llm.invoke(self._model, messages)

        try:
            return {"messages": [*messages, reply], "extraction": self.parse(_text(reply)), "error": None}
        except ExtractionError as error:
            return {"messages": [*messages, reply], "extraction": None, "error": str(error)}

    def _repair(self, state: ExtractState) -> dict[str, Any]:
        logger.warning("retrying once after %s", state["error"])
        return {
            "messages": [*state["messages"], HumanMessage(REPAIR_INSTRUCTION)],
            "attempts": state["attempts"] + 1,
        }

    @staticmethod
    def _next(state: ExtractState) -> str:
        if state["extraction"] is not None:
            return END
        return "repair" if state["attempts"] == 0 else END


def _text(message: AIMessage) -> str:
    """응답 본문을 문자열로. 제공처가 블록 배열로 답해도 같은 자리에서 흡수한다."""
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") for part in content if isinstance(part, dict))


def filter_claims(
    response: ExtractionResponse,
    document: PendingExtractionDocument,
) -> tuple[NormalizedClaim, ...]:
    """모델 주장을 검증·정규화한다. 통과 못 한 주장만 버리고 건수를 로그로 남긴다.

    버리는 것: 문서 태그 밖 종목, 이벤트·지표 조합 위반, 기간 표기 위반, 단위 정규화 실패,
    earnings의 actual(원본은 earnings_fact다), 같은 키의 중복 주장(첫 것만 남긴다).
    """
    kept: list[NormalizedClaim] = []
    seen: set[tuple[str, str, str, str]] = set()
    dropped: list[str] = []
    for claim in response.claims:
        key = (claim.stock_code, claim.event_type, claim.period_key, claim.metric, claim.kind)
        reason = _rejection_reason(claim, document)
        if reason is not None:
            dropped.append(f"{key}: {reason}")
            continue
        dedup_key = (claim.stock_code, claim.event_type, claim.period_key, claim.metric + "/" + claim.kind)
        if dedup_key in seen:
            dropped.append(f"{key}: duplicate")
            continue
        value = normalize_amount(claim.value, claim.unit)
        if value is None:
            dropped.append(f"{key}: unparseable amount {claim.value!r} {claim.unit!r}")
            continue
        value_low = normalize_amount(claim.value_low, claim.unit) if claim.value_low is not None else None
        value_high = normalize_amount(claim.value_high, claim.unit) if claim.value_high is not None else None
        if (claim.value_low is not None or claim.value_high is not None) and (
            value_low is None or value_high is None or value_low > value_high
        ):
            dropped.append(f"{key}: unparseable range {claim.value_low!r}~{claim.value_high!r}")
            continue
        seen.add(dedup_key)
        kept.append(
            NormalizedClaim(
                stock_code=claim.stock_code,
                event_type=claim.event_type,
                period_key=claim.period_key,
                metric=claim.metric,
                claim_kind=claim.kind,
                value=value,
                value_low=value_low,
                value_high=value_high,
                broker=claim.broker or None,
            )
        )
    if dropped:
        # 프롬프트나 이벤트 목록을 늘릴 근거다. 조용히 버리면 무엇을 놓치는지 알 수 없다.
        logger.warning("document %s: dropped %s claims: %s", document.id, len(dropped), dropped)
    return tuple(kept)


def _rejection_reason(claim: ExtractedClaim, document: PendingExtractionDocument) -> str | None:
    if claim.stock_code not in document.tickers:
        return "stock_code outside document tags"
    if claim.metric not in EVENT_METRICS[claim.event_type]:
        return "metric not allowed for event_type"
    if not PERIOD_KEY_PATTERN.match(claim.period_key):
        return "invalid period_key"
    if claim.event_type == "earnings" and claim.kind == "actual":
        return "earnings actuals come from earnings_fact"
    return None
