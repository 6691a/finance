"""문서에서 기대·실제 주장을 뽑는다 — 프롬프트와 `ExpectationExtractor`.

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
from modules.expectation.domain import (
    EVENT_METRICS,
    PERIOD_KEY_PATTERN,
    ExtractionError,
    NormalizedClaim,
    PendingExtractionDocument,
    normalize_amount,
)
from modules.llm import UnsupportedResponseFormat
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


SYSTEM_PROMPT = (
    "당신은 한국 주식 이벤트 분석기다. 경제 문서에서 종목 이벤트에 대한 **수치 주장**만 뽑는다. "
    "기대(전망·추정·컨센서스)와 실제(발표·확정)를 가른다. "
    "반드시 JSON 객체 하나만 출력한다. 설명이나 코드 펜스를 붙이지 않는다."
)

# 사람이 읽는 지시. 종목 후보는 문서의 태그로 실행 시점에 채운다.
INSTRUCTION = """\
아래 문서에서 종목 이벤트에 대한 수치 주장을 뽑아 JSON으로 답하라. 없으면 빈 배열이 정답이다.

규칙:
- `stock_code`는 **종목 후보에 있는 값만** 쓴다. 후보 밖 종목의 주장은 뽑지 않는다.
- `event_type`은 shareholder_return(주주환원 정책·배당·자사주), earnings(실적),
  guidance(회사가 직접 낸 전망) 중 하나다.
- `metric`은 이벤트마다 정해져 있다.
  - shareholder_return: total_return_amount(총 환원액), buyback_amount(자사주 매입),
    dividend_total(배당 총액), dividend_per_share(주당 배당금)
  - earnings: revenue(매출액), operating_profit(영업이익), net_income(당기순이익)
  - guidance: revenue, operating_profit
- `kind`는 expectation(전망·추정·컨센서스) 또는 actual(회사가 발표·확정한 값)이다.
  - "~할 전망", "~로 추정", "목표", "컨센서스" → expectation
  - "발표했다", "확정했다", "공시했다" → actual
  - **earnings의 actual은 뽑지 않는다.** 실적 확정치는 공시 파서가 따로 받는다.
- `period_key`는 대상 기간이다. 연간은 `2026`, 분기는 `2026Q2`, 반기는 `2026H1` 형식만 쓴다.
  기간을 특정할 수 없는 주장은 뽑지 않는다.
- `value`는 문서의 숫자 그대로, `unit`은 그 단위(원, 만원, 억, 억원, 조, 조원)다.
  "9.5조원"이면 value "9.5", unit "조원"이다. 문서에 없는 숫자를 만들지 마라.
- 범위 주장("9~10조")은 value에 중앙값, value_low/value_high에 하한·상한을 쓴다.
  단일 값이면 value_low/value_high는 null이다.
- `broker`는 주장 주체(증권사·기관 이름)다. 문서 제목 끝이나 본문에서 찾고, 모르면 null이다.
  회사 자신의 발표(actual)는 null이다.

출력 형식:
{"claims": [{"stock_code": "", "event_type": "", "period_key": "", "metric": "",
 "kind": "", "value": "", "unit": "", "value_low": null, "value_high": null, "broker": null}]}
"""

REPAIR_INSTRUCTION = "이전 응답이 형식에 맞지 않았다. JSON 객체 하나만 다시 출력하라."


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
