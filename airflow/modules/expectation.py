"""종목 이벤트의 기대치·실제값을 문서에서 추출하고 서프라이즈를 판정한다.

`docs/market-thesis/8-expectation.md`의 2·4절이다. 두 층의 역할이 갈린다.

- **추출(LLM)**: 평가가 끝나고 종목 태그가 붙은 문서에서 "누가 언제 어떤 이벤트에 어떤 값을
  기대했다 / 발표했다"를 구조화한다. 한 문서에서 기대(expectation)와 실제(actual)를 함께
  뽑는다 — 발표 기사가 곧 실제값의 원천이다. 빈 결과가 대부분이고 정상이다.
- **판정(LLM 없음)**: 실제값이 생기면 발표 전 기대들과 대조해 beat/meet/miss 한 행을
  남긴다. 대표 기대치 집계와 분류는 전부 순수 함수다 — thesis 채점 수식이 SQL이 아니라
  Python에 있는 것과 같은 이유로, DB 없이 경계값을 테스트한다.

## 검증이 추출의 절반이다

모델 응답을 그대로 믿지 않는다. 이벤트·지표는 Literal(스키마 enum)로 좁히고, 조합·기간
표기·단위 정규화·종목 태그 대조는 `filter_claims`가 한다. 목록 밖 값은 그 주장만 버리고
건수를 로그로 남긴다 — `document_instrument` 태깅과 같은 패턴이다.

**실적(earnings)의 실제값은 추출하지 않는다.** `earnings_fact`가 원본이다. DART 파서가
원문 표에서 정확히 읽는 값을 기사 산문에서 다시 뽑으면 어긋난 쪽을 고를 수 없다.

## 도메인 상수는 백엔드와 중복이다

이 모듈은 `apps/models`를 import하지 못한다(Airflow 트리 규칙). 이벤트·지표·기간 표기
상수는 `apps/models/analysis.py`와 **중복을 허용하되 테스트로 대조한다**
(`tests/modules/test_expectation.py`, realtime 수집기의 `*_match_the_airflow_collector`와
같은 방식).

## 배치를 흩지 않는다

문서 태깅(`assessment.AssessmentBatch`)은 `Send` 팬아웃을 쓰지만 여기는 순차 루프다.
대상이 종목 태그 문서뿐이라 시간당 수 건이고, 동시성이 벌어 줄 시간이 없다.
물량이 늘면 그때 같은 팬아웃을 붙인다.

이 모듈은 Airflow를 import하지 않는다. import하면 테스트가 배포 환경 없이 돌지 않는다.
"""

import json
import logging
import re
import statistics
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Protocol, Self, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, ValidationError

from modules import llm
from modules.llm import UnsupportedResponseFormat
from modules.schema import SchemaError, json_object, response_format
from modules.sql import read_sql

logger = logging.getLogger(__name__)

# 프롬프트를 고치면 올린다. 이 값이 오른 문서는 재추출 대상이 된다.
PROMPT_VERSION = "1"

# 한 번 실행에서 추출할 문서 수. 대상이 종목 태그 문서뿐이라 보통 이보다 훨씬 적다.
DEFAULT_BATCH_SIZE = 50

# meet로 볼 서프라이즈 허용 밴드(퍼센트). **실측이 아니라 시작값이다** — thesis의
# FLAT_THRESHOLD_PCT처럼 판정 분포가 쌓이면 다시 정한다(docs/market-thesis/TUNING.md).
MEET_BAND_PCT = Decimal("5.0")

# 아래 도메인 상수는 apps/models/analysis.py와 중복이다(모듈 docstring). 테스트가 대조한다.
EVENT_TYPES: tuple[str, ...] = ("shareholder_return", "earnings", "guidance")
CLAIM_KINDS: tuple[str, ...] = ("expectation", "actual")
EVENT_METRICS: dict[str, tuple[str, ...]] = {
    "shareholder_return": ("total_return_amount", "buyback_amount", "dividend_total", "dividend_per_share"),
    "earnings": ("revenue", "operating_profit", "net_income"),
    "guidance": ("revenue", "operating_profit"),
}
PERIOD_KEY_PATTERN = re.compile(r"^[0-9]{4}(Q[1-4]|H[12])?$")

# 원문 단위 표기를 원(KRW)으로 바꾸는 배수. 모르는 표기는 그 주장을 버린다 — 조용히
# 엉뚱한 자릿수로 저장하는 것보다 낫다(재무성 和暦 규칙과 같은 태도).
UNIT_MULTIPLIERS: dict[str, Decimal] = {
    "원": Decimal(1),
    "만원": Decimal(10) ** 4,
    "억": Decimal(10) ** 8,
    "억원": Decimal(10) ** 8,
    "조": Decimal(10) ** 12,
    "조원": Decimal(10) ** 12,
}

# 지표 한글 표기. Slack과 로그가 쓴다.
METRIC_LABELS: dict[str, str] = {
    "total_return_amount": "총 환원액",
    "buyback_amount": "자사주 매입",
    "dividend_total": "배당 총액",
    "dividend_per_share": "주당 배당금",
    "revenue": "매출액",
    "operating_profit": "영업이익",
    "net_income": "당기순이익",
}
EVENT_LABELS: dict[str, str] = {
    "shareholder_return": "주주환원",
    "earnings": "실적",
    "guidance": "가이던스",
}
VERDICT_LABELS: dict[str, str] = {
    "beat": "▲ 상회",
    "meet": "– 부합",
    "miss": "▼ 미달",
}


class Cursor(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(self, *args: object) -> bool | None: ...

    def execute(self, statement: str, parameters: Sequence[Any] = ()) -> object: ...

    def executemany(self, statement: str, parameters: Sequence[Sequence[Any]]) -> object: ...

    def fetchall(self) -> Any: ...

    def fetchone(self) -> Any: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...


class ExtractionError(RuntimeError):
    """모델이 우리가 아는 모양으로 답하지 않았다. 문서는 원장에 오르지 않고 다음 실행이 다시 집는다."""


# ---------------------------------------------------------------------------
# 순수 함수 — 기간·단위·집계·분류. LLM도 DB도 모른다.
# ---------------------------------------------------------------------------


def period_end_for(period_key: str) -> date:
    """기간 표기를 회계 기간 종료일로 바꾼다. `earnings_fact.period_end`와 조인하는 키다."""
    if not PERIOD_KEY_PATTERN.match(period_key):
        raise ValueError(f"period_key must match YYYY, YYYYQn or YYYYHn: {period_key!r}")
    year = int(period_key[:4])
    suffix = period_key[4:]
    if not suffix:
        return date(year, 12, 31)
    quarter_ends = {"Q1": (3, 31), "Q2": (6, 30), "Q3": (9, 30), "Q4": (12, 31), "H1": (6, 30), "H2": (12, 31)}
    month, day = quarter_ends[suffix]
    return date(year, month, day)


def amount_basis_for(period_key: str) -> str:
    """기간 표기가 요구하는 `earnings_fact.amount_basis`.

    분기·반기는 해당 기간 금액(period), 연간은 사업연도 누계(cumulative)다.
    """
    if not PERIOD_KEY_PATTERN.match(period_key):
        raise ValueError(f"period_key must match YYYY, YYYYQn or YYYYHn: {period_key!r}")
    return "cumulative" if len(period_key) == 4 else "period"


def normalize_amount(value: str, unit: str) -> Decimal | None:
    """원문 표기 값을 원(KRW)으로 정규화한다. 모르는 표기는 None — 부르는 쪽이 그 주장을 버린다."""
    multiplier = UNIT_MULTIPLIERS.get(unit.replace(" ", ""))
    if multiplier is None:
        return None
    try:
        amount = Decimal(value.replace(",", "").strip())
    except InvalidOperation:
        return None
    return (amount * multiplier).quantize(Decimal("0.01"))


class ClaimRow(BaseModel):
    """판정 조회가 돌려준 주장 한 행."""

    model_config = ConfigDict(frozen=True)

    stock_code: str
    event_type: str
    period_key: str
    metric: str
    claim_kind: str
    value: Decimal
    stated_at: datetime
    broker: str | None
    document_id: int | None
    source_record_id: int | None


def resolve_actual(rows: Sequence[ClaimRow]) -> tuple[Decimal, datetime, str] | None:
    """actual 주장들에서 실제값 하나를 정한다. 값이 갈리면 판정하지 않는다.

    "총 환원 8조"와 "배당+자사주 8.5조"처럼 집계 범위가 다른 숫자가 실제로 온다. 조용히
    한쪽을 고르는 대신 보류한다 — 공시 기반 기사가 수렴하면 다음 실행이 푼다. 반환은
    (실제값, 발표 시각 = 가장 이른 주장 시각, actual_ref)다.
    """
    actuals = [row for row in rows if row.claim_kind == "actual"]
    if not actuals:
        return None
    values = {row.value for row in actuals}
    if len(values) > 1:
        first = actuals[0]
        logger.warning(
            "conflicting actual values for %s %s %s %s: %s — holding judgment",
            first.stock_code,
            first.event_type,
            first.period_key,
            first.metric,
            sorted(values),
        )
        return None
    earliest = min(actuals, key=lambda row: row.stated_at)
    return earliest.value, earliest.stated_at, f"document:{earliest.document_id}"


def aggregate_expectations(rows: Sequence[ClaimRow], announced_at: datetime) -> tuple[Decimal, int] | None:
    """발표 전 기대들에서 대표 기대치를 뽑는다. 기대가 없으면 None — 판정하지 않는다.

    - `stated_at < announced_at`인 기대만 쓴다. 발표 뒤 "기대치는 X였다"라고 회고한 기사가
      기대로 섞이면 판정이 오염된다.
    - 컨센서스 행(`source_record_id` 출처)이 있으면 **최신 컨센서스 하나**다. 정형 집계가
      개별 리포트 발췌보다 정확하다.
    - 없으면 주체(broker)별 최신 행의 **중앙값**이다. 같은 증권사가 기대를 올려 잡았으면
      최신만 센다. 주체를 모르는 기사 인용(broker NULL)도 한 표다.

    반환은 (대표 기대치, 대조한 기대 행 수)다.
    """
    expectations = [row for row in rows if row.claim_kind == "expectation" and row.stated_at < announced_at]
    if not expectations:
        return None
    consensus = [row for row in expectations if row.source_record_id is not None]
    if consensus:
        latest = max(consensus, key=lambda row: row.stated_at)
        return latest.value, len(expectations)
    latest_by_broker: dict[str | None, ClaimRow] = {}
    for row in expectations:
        current = latest_by_broker.get(row.broker)
        if current is None or row.stated_at > current.stated_at:
            latest_by_broker[row.broker] = row
    values = [row.value for row in latest_by_broker.values()]
    return Decimal(statistics.median(values)), len(expectations)


def classify_surprise(
    expected: Decimal,
    actual: Decimal,
    band_pct: Decimal = MEET_BAND_PCT,
) -> tuple[Decimal, str] | None:
    """서프라이즈를 분류한다. |surprise| ≤ 밴드면 meet, 아니면 부호로 beat/miss.

    기대가 0이면 비율을 만들 수 없다. 실제도 0이면 정확히 부합(meet, 0%)이고, 아니면
    판정하지 않는다(None) — 억지 비율이 더 나쁘다.
    """
    if expected == 0:
        if actual == 0:
            return Decimal("0.0000"), "meet"
        return None
    surprise = ((actual - expected) / abs(expected) * 100).quantize(Decimal("0.0001"))
    if abs(surprise) <= band_pct:
        return surprise, "meet"
    return surprise, "beat" if surprise > 0 else "miss"


class EarningsFactRow(BaseModel):
    """`earnings_fact/select_actual_for_judgment.sql`이 돌려준 실적 행."""

    model_config = ConfigDict(frozen=True)

    id: int
    statement_scope: str
    amount_basis: str
    release_type: str
    rcept_no: str
    current_amount: Decimal
    created_at: datetime


def resolve_earnings_actual(rows: Sequence[EarningsFactRow], period_key: str) -> tuple[Decimal, datetime, str] | None:
    """실적 실제값을 earnings_fact 후보에서 고른다.

    기간 기준(`amount_basis_for`)이 맞는 행 중 연결(CFS)을 별도(OFS)보다 우선하고, 같은
    범위 안에서는 최신 접수번호(정정 공시)를 쓴다 — `EarningsFact` docstring의 조회 규칙
    그대로다. `created_at`은 우리가 그 공시를 파싱한 시각이라 발표 감지 시각의 대용이다.
    """
    basis = amount_basis_for(period_key)
    candidates = [row for row in rows if row.amount_basis == basis]
    if not candidates:
        return None
    for scope in ("CFS", "OFS"):
        scoped = [row for row in candidates if row.statement_scope == scope]
        if scoped:
            # 조회 SQL이 rcept_no 내림차순으로 준다 — 첫 행이 최신 정정이다.
            chosen = scoped[0]
            return chosen.current_amount, chosen.created_at, f"earnings_fact:{chosen.id}"
    return None


def format_krw(amount: Decimal) -> str:
    """원 단위 금액을 사람이 읽는 표기로. 조·억 단위로 줄이고 작은 값은 원 그대로 둔다."""
    magnitude = abs(amount)
    if magnitude >= Decimal(10) ** 12:
        return f"{amount / Decimal(10) ** 12:,.2f}조"
    if magnitude >= Decimal(10) ** 8:
        return f"{amount / Decimal(10) ** 8:,.0f}억"
    return f"{amount:,.0f}원"


# ---------------------------------------------------------------------------
# 추출 — LLM은 여기에만 있다.
# ---------------------------------------------------------------------------


class PendingExtractionDocument(BaseModel):
    """추출을 기다리는 문서. `tickers`는 평가가 붙인 종목 태그다."""

    model_config = ConfigDict(frozen=True)

    id: int
    source_slug: str
    title: str
    summary: str | None
    body: str | None
    published_at: datetime | None
    detected_at: datetime
    content_hash: str
    tickers: tuple[str, ...]

    @property
    def stated_at(self) -> datetime:
        """주장 시점. 발행 시각이 없으면 감지 시각이다. 모델이 아니라 코드가 정한다."""
        return self.published_at or self.detected_at


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


class NormalizedClaim(BaseModel):
    """검증·정규화를 통과해 저장할 주장 하나. 값은 원(KRW)이다."""

    model_config = ConfigDict(frozen=True)

    stock_code: str
    event_type: str
    period_key: str
    metric: str
    claim_kind: str
    value: Decimal
    value_low: Decimal | None
    value_high: Decimal | None
    broker: str | None


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


# ---------------------------------------------------------------------------
# 저장과 판정 — SQL은 행을 나르고 판단은 위의 순수 함수가 한다.
# ---------------------------------------------------------------------------

PENDING_EXTRACTION = read_sql("postgres", "document", "select_pending_extraction.sql")
CLAIM_INSERT = read_sql("postgres", "stock_event_claim", "insert.sql")
EXTRACTION_UPSERT = read_sql("postgres", "stock_event_extraction", "upsert.sql")
PENDING_JUDGMENT = read_sql("postgres", "stock_event_claim", "select_pending_judgment.sql")
PENDING_EARNINGS_EXPECTATIONS = read_sql("postgres", "stock_event_claim", "select_pending_earnings_expectations.sql")
EARNINGS_ACTUAL = read_sql("postgres", "earnings_fact", "select_actual_for_judgment.sql")
OUTCOME_INSERT = read_sql("postgres", "stock_event_outcome", "insert.sql")


def pending_documents(
    connection: Connection,
    limit: int = DEFAULT_BATCH_SIZE,
    prompt_version: str = PROMPT_VERSION,
) -> tuple[PendingExtractionDocument, ...]:
    """추출을 기다리는 문서. 평가 완료 + 종목 태그 + (미추출이거나 본문·프롬프트가 바뀜)."""
    with connection.cursor() as cursor:
        cursor.execute(PENDING_EXTRACTION, (prompt_version, limit))
        rows = cursor.fetchall()
    return tuple(
        PendingExtractionDocument(
            id=row[0],
            source_slug=row[1],
            title=row[2],
            summary=row[3],
            body=row[4],
            published_at=row[5],
            detected_at=row[6],
            content_hash=row[7],
            tickers=tuple(row[8] or ()),
        )
        for row in rows
    )


def store_extraction(
    connection: Connection,
    document: PendingExtractionDocument,
    claims: Sequence[NormalizedClaim],
    model: str,
    extracted_at: datetime | None = None,
    prompt_version: str = PROMPT_VERSION,
) -> None:
    """주장과 원장을 저장한다. 문서 하나가 트랜잭션 하나다(커밋은 호출자가 한다).

    주장 0건도 원장에 남는다 — "뽑았는데 없었다"와 "아직 안 뽑았다"가 구분돼야
    매시간 같은 문서를 다시 뽑지 않는다.
    """
    stated_at = document.stated_at
    with connection.cursor() as cursor:
        for claim in claims:
            cursor.execute(
                CLAIM_INSERT,
                (
                    claim.stock_code,
                    claim.event_type,
                    claim.period_key,
                    claim.metric,
                    claim.claim_kind,
                    claim.value,
                    claim.value_low,
                    claim.value_high,
                    stated_at,
                    claim.broker,
                    document.id,
                    None,
                ),
            )
        cursor.execute(
            EXTRACTION_UPSERT,
            (
                document.id,
                document.content_hash,
                extracted_at or datetime.now(UTC),
                model,
                prompt_version,
                len(claims),
            ),
        )


class JudgedOutcome(BaseModel):
    """이번 실행이 새로 쓴 판정 하나. Slack 렌더링의 입력이다."""

    model_config = ConfigDict(frozen=True)

    stock_code: str
    event_type: str
    period_key: str
    metric: str
    expected_value: Decimal
    expectation_count: int
    actual_value: Decimal
    surprise_pct: Decimal
    verdict: str
    announced_at: datetime


def _claim_rows(rows: Sequence[Sequence[Any]]) -> tuple[ClaimRow, ...]:
    return tuple(
        ClaimRow(
            stock_code=row[0],
            event_type=row[1],
            period_key=row[2],
            metric=row[3],
            claim_kind=row[4],
            value=row[5],
            stated_at=row[6],
            broker=row[7],
            document_id=row[8],
            source_record_id=row[9],
        )
        for row in rows
    )


def _group_by_event(rows: Sequence[ClaimRow]) -> dict[tuple[str, str, str, str], list[ClaimRow]]:
    grouped: dict[tuple[str, str, str, str], list[ClaimRow]] = {}
    for row in rows:
        grouped.setdefault((row.stock_code, row.event_type, row.period_key, row.metric), []).append(row)
    return grouped


def judge_pending(connection: Connection, dag_run_id: str) -> tuple[JudgedOutcome, ...]:
    """판정 없는 이벤트를 대조해 새 판정 행을 쓴다. 이번 실행이 **새로 쓴** 것만 돌려준다.

    LLM이 없다. 실제값 확보(주장 일치 또는 earnings_fact) → 발표 전 기대 집계 → 분류 →
    INSERT(첫 성공본 불변, RETURNING 0행이면 동시 실행이 먼저 쓴 것이라 발송 대상이 아니다).
    조건을 못 채운 키(실제 불일치, 기대 0건, 기대 0 나누기)는 행이 안 생기고 다음 실행이
    다시 본다.
    """
    with connection.cursor() as cursor:
        cursor.execute(PENDING_JUDGMENT)
        claim_groups = _group_by_event(_claim_rows(cursor.fetchall()))
        cursor.execute(PENDING_EARNINGS_EXPECTATIONS)
        earnings_groups = _group_by_event(_claim_rows(cursor.fetchall()))

    judged: list[JudgedOutcome] = []
    with connection.cursor() as cursor:
        for key, rows in claim_groups.items():
            actual = resolve_actual(rows)
            if actual is None:
                continue
            outcome = _judge_one(cursor, key, rows, actual, dag_run_id)
            if outcome is not None:
                judged.append(outcome)

        for key, rows in earnings_groups.items():
            stock_code, _, period_key, metric = key
            cursor.execute(EARNINGS_ACTUAL, (stock_code, period_end_for(period_key), metric))
            fact_rows = tuple(
                EarningsFactRow(
                    id=row[0],
                    statement_scope=row[1],
                    amount_basis=row[2],
                    release_type=row[3],
                    rcept_no=row[4],
                    current_amount=row[5],
                    created_at=row[6],
                )
                for row in cursor.fetchall()
            )
            actual = resolve_earnings_actual(fact_rows, period_key)
            if actual is None:
                continue
            outcome = _judge_one(cursor, key, rows, actual, dag_run_id)
            if outcome is not None:
                judged.append(outcome)
    return tuple(judged)


def _judge_one(
    cursor: Cursor,
    key: tuple[str, str, str, str],
    rows: Sequence[ClaimRow],
    actual: tuple[Decimal, datetime, str],
    dag_run_id: str,
) -> JudgedOutcome | None:
    stock_code, event_type, period_key, metric = key
    actual_value, announced_at, actual_ref = actual
    aggregated = aggregate_expectations(rows, announced_at)
    if aggregated is None:
        # 기대가 없던 발표는 그것대로 사실이다. 억지 판정이 더 나쁘다.
        logger.info("no pre-announcement expectations for %s %s %s %s", *key)
        return None
    expected_value, expectation_count = aggregated
    classified = classify_surprise(expected_value, actual_value)
    if classified is None:
        logger.warning("cannot classify %s %s %s %s: expected 0, actual %s", *key, actual_value)
        return None
    surprise_pct, verdict = classified
    cursor.execute(
        OUTCOME_INSERT,
        (
            stock_code,
            event_type,
            period_key,
            metric,
            expected_value,
            expectation_count,
            actual_value,
            surprise_pct,
            verdict,
            announced_at,
            actual_ref,
            dag_run_id,
        ),
    )
    if cursor.fetchone() is None:
        # 동시 실행이 먼저 썼다. 첫 성공본 불변 — 이번 실행의 발송 대상이 아니다.
        return None
    return JudgedOutcome(
        stock_code=stock_code,
        event_type=event_type,
        period_key=period_key,
        metric=metric,
        expected_value=expected_value,
        expectation_count=expectation_count,
        actual_value=actual_value,
        surprise_pct=surprise_pct,
        verdict=verdict,
        announced_at=announced_at,
    )


# ---------------------------------------------------------------------------
# Slack 렌더링 — 순수 조회+포맷. 발송은 DAG가 한다.
# ---------------------------------------------------------------------------

HEADER = "📐 기대 대비 발표"


def render_text(outcomes: Sequence[JudgedOutcome]) -> str:
    """블록을 못 그리는 자리(알림, 검색)에 뜨는 대체 문구."""
    lines = [HEADER]
    lines.extend(_outcome_line(outcome) for outcome in outcomes)
    return "\n".join(lines)


def render_blocks(outcomes: Sequence[JudgedOutcome]) -> list[dict[str, Any]]:
    """판정 하나가 section 하나다. 새 판정이 있을 때만 발송하므로 0건 형태는 없다."""
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": HEADER}},
    ]
    blocks.extend(
        {"type": "section", "text": {"type": "mrkdwn", "text": _outcome_line(outcome)}} for outcome in outcomes
    )
    return blocks


def _outcome_line(outcome: JudgedOutcome) -> str:
    return (
        f"*{outcome.stock_code}* · {EVENT_LABELS[outcome.event_type]} {outcome.period_key}"
        f" · {METRIC_LABELS[outcome.metric]}\n"
        f"발표 {format_krw(outcome.actual_value)} vs 기대 {format_krw(outcome.expected_value)}"
        f" (기대 {outcome.expectation_count}건)"
        f" → {VERDICT_LABELS[outcome.verdict]} {outcome.surprise_pct:+.1f}%"
    )
