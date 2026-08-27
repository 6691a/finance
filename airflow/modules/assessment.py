"""수집한 문서를 LLM으로 태깅하고 점수를 매긴다.

`docs/analysis/economic-document-archive-design.md` 2단계의 LLM 절반이다. 수집(`collectors/documents.py`)과
나뉘어 있어 **모델이나 키가 없어도 원문 수집은 계속 돈다.** 여기가 못 돌면 문서는 태그 없이
쌓이고, 다음 실행이 밀린 것부터 집는다.

## 하는 일과 하지 않는 일

- **한다**: 문서를 종목·지표에 연결하고, 방향(호재·악재)과 0~8점을 매긴다.
- **하지 않는다**: 문서를 버리거나 상태를 바꾸는 일. 점수는 저장만 하고, 무엇을 쓸지는 4단계
  리포트 프롬프트가 정한다. 지금 버리면 나중에 기준을 바꿀 때 되돌릴 수 없다.

## 태그는 조인되는 값이어야 한다

`document_instrument`와 `document_indicator`가 이 모듈의 산출물이다. 리포트는 "지난 7일
005930 관련 기사"로 시작하는데 자유 문자열로는 그 조인이 안 된다. 그래서 **허용 값을
프롬프트에 후보 목록으로 제시한다.** 수집기들이 `MarketRateSeries`나 `DomesticStock` Enum으로
식별자를 좁히는 것과 같은 이유다.

목록 밖의 값이 오면 **그 태그만 버리고 문서는 저장한다.** 태그 하나 때문에 문서를 잃지
않는다. 버린 값은 로그에 남겨 마스터를 늘릴 근거로 쓴다.

## 문장은 여기 없다

프롬프트는 `modules/prompts/assessment.yaml`이 갖는다. 관점 셋도 그 파일의 `variants`다.
문장을 고치는 일과 흐름을 고치는 일은 주기가 다르다. 읽는 방법은 `modules/prompt.py`에 있다.
**여기 문장에는 판이 붙는다** — 고치면 `PROMPT_VERSION`을 올리고
`tests/modules/test_prompt_versions.py`의 해시를 같은 커밋에서 바꾼다.

## 관점은 값이지 프롬프트가 아니다

초판은 "한국 투자자를 위한 분석기"로 고정돼 있었다. 그런데 **켜져 있는 피드 아홉 중 여섯이
비한국이고, 그것들을 모으는 이유가 전이 효과다.** 미국 금리와 달러, 반도체 업황은 한국을
한 번도 언급하지 않으면서 한국 자산 가격을 움직인다. "한국에 직접 관련되는가"로 물으면
그 문서들이 전부 0점을 받는다.

그래서 관점을 조각 하나로 빼고 기본을 `global`로 뒀다. `relevance`도
"직접 관련"이 아니라 **경로의 존재와 길이**를 묻는다. 미국 시장만 보는 리포트가 필요해지면
프롬프트를 복사하는 대신 `LLM_PERSPECTIVE`를 바꾼다.

## 다시 평가하는 조건

`assessed_content_hash`가 현재 `content_hash`와 다르거나 `prompt_version`이 달라지면 대상이
된다. 이 컬럼이 없으면 같은 문서를 매번 다시 평가하거나 영영 안 하거나 둘 중 하나가 된다.

`prompt_version`에는 관점이 함께 들어간다(`2/global`). 관점이 바뀌면 같은 문서라도 점수가
달라지므로 컬럼을 늘리는 대신 기존 재평가 조건에 얹는다.

## 흐름

LangGraph 그래프 둘이다. 흐름 제어를 손으로 짜지 않는 대신 노드 이름이 그대로 트레이스에
남아 어디서 몇 번 불렀는지 보인다.

- `DocumentAssessor`: 문서 하나. `call` → (형식이 깨지면) `repair` → `call`. 교정은 한 번뿐이다.
- `AssessmentBatch`: 문서 목록을 `Send`로 흩어 `assess_one`을 문서마다 돌린다. 동시 실행 수는
  `LLM_MAX_CONCURRENCY`가 정한다. **저장은 하지 않는다.** 결과만 모아 DAG에 돌려주고, 문서
  하나가 트랜잭션 하나라는 규칙은 DAG가 지킨다.

체크포인터는 붙이지 않는다. 상태를 프로세스 밖에 남길 이유가 없고 재실행 단위는 Airflow
태스크다.

## 필요한 환경

`OPENAI_API_KEY` 하나가 필수다. **우리가 읽지 않는다.** 어떤 모델을 부를지는 `modules/llm.py`가
코드로 정하고, 키는 그 LangChain 클래스가 자기 이름으로 읽는다. 제공처를 바꾸면 환경변수
이름도 그 제공처 것으로 바뀐다.

`LLM_PERSPECTIVE`(기본 `global`)와 `LLM_MAX_CONCURRENCY`(기본 4)는 선택이며 이 모듈이 읽는다.
둘 다 제공처와 무관하게 판단을 바꾸는 값이다.

`LANGSMITH_TRACING`을 켜면 프롬프트와 문서 본문이 LangSmith로 나간다. `modules/llm.py` 참고.
"""

import json
import logging
import operator
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Self, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from modules import llm
from modules.db import Connection
from modules.llm import UnsupportedResponseFormat
from modules.prompt import read_prompt
from modules.schema import SchemaError, json_object, response_format
from modules.sql import read_sql
from modules.upsert import execute_upserts

logger = logging.getLogger(__name__)

# 프롬프트를 고치면 올린다. 이 값이 오른 문서는 재평가 대상이 된다.
PROMPT_VERSION = "3"

PROMPTS = read_prompt("assessment")

# 어느 시장의 눈으로 볼 것인가. 문장은 `modules/prompts/assessment.yaml`의 `variants`에 있고
# 여기 남은 것은 **허용 값 판정**뿐이다 — `LlmSettings`가 이 키로 `LLM_PERSPECTIVE`를 막는데
# 그 판정은 YAML이 할 수 없다.
PERSPECTIVES: dict[str, str] = PROMPTS.variants

DEFAULT_PERSPECTIVE = "global"

# 한 번 실행에서 평가할 문서 수. 시간당 수집량보다 넉넉하되 예산이 한 번에 새지 않게 둔다.
DEFAULT_BATCH_SIZE = 50

# 한 배치에서 동시에 부를 문서 수. 제공처 rate limit과 비용 흐름을 바꾸는 값이라 작게 둔다.
DEFAULT_MAX_CONCURRENCY = 4

# 점수 항목. 각 0~2점이고 합이 `value_score`다.
SCORE_FIELDS = ("relevance", "novelty", "specificity", "impact")
class AssessmentError(RuntimeError):
    """모델이 우리가 아는 모양으로 답하지 않았다. 문서는 태그 없이 남는다."""


class LlmSettings(BaseModel):
    """평가 설정.

    **접속 정보는 여기 없다.** 어떤 모델을 부를지는 `modules/llm.py`가 코드로 정하고 키는
    LangChain이 자기 환경변수(`OPENAI_API_KEY`)에서 읽는다. 여기 남은 것은 제공처와 무관하게
    이 DAG의 판단을 바꾸는 값뿐이다.
    """

    model_config = ConfigDict(frozen=True)

    perspective: str = DEFAULT_PERSPECTIVE
    # 한 배치에서 동시에 부를 문서 수. 제공처 rate limit에 걸리면 내린다. 1이면 순차다.
    max_concurrency: int = Field(default=DEFAULT_MAX_CONCURRENCY, ge=1)

    @field_validator("perspective")
    @classmethod
    def require_known_perspective(cls, value: str) -> str:
        if value not in PERSPECTIVES:
            raise ValueError(f"perspective must be one of {sorted(PERSPECTIVES)}")
        return value

    @property
    def prompt_revision(self) -> str:
        """`document.prompt_version`에 저장할 값.

        관점을 버전에 함께 담는다. 관점이 바뀌면 같은 문서라도 점수가 달라지므로 재평가
        대상이 돼야 하는데, 컬럼을 하나 더 두는 대신 기존 재평가 조건을 그대로 쓴다.
        """
        return f"{PROMPT_VERSION}/{self.perspective}"

    @classmethod
    def from_environment(cls) -> Self:
        # 모델 API 키는 검사하지 않는다. LangChain 클래스가 자기 이름으로 읽고, 없으면
        # 모델을 만들 때 그쪽이 실패한다. 우리가 이름을 한 번 더 알 이유가 없다.
        perspective = os.environ.get("LLM_PERSPECTIVE") or DEFAULT_PERSPECTIVE
        if perspective not in PERSPECTIVES:
            raise AssessmentError(f"LLM_PERSPECTIVE must be one of {sorted(PERSPECTIVES)}, got {perspective!r}")
        raw_concurrency = os.environ.get("LLM_MAX_CONCURRENCY") or DEFAULT_MAX_CONCURRENCY
        try:
            max_concurrency = int(raw_concurrency)
        except ValueError:
            raise AssessmentError(f"LLM_MAX_CONCURRENCY must be an integer, got {raw_concurrency!r}") from None
        if max_concurrency < 1:
            raise AssessmentError(f"LLM_MAX_CONCURRENCY must be at least 1, got {max_concurrency}")
        return cls(
            perspective=perspective,
            max_concurrency=max_concurrency,
        )


class PendingDocument(BaseModel):
    """평가를 기다리는 문서."""

    model_config = ConfigDict(frozen=True)

    id: int
    source_slug: str
    title: str
    summary: str | None
    body: str | None
    language: str
    published_at: datetime | None
    content_hash: str


class Candidates(BaseModel):
    """프롬프트에 넣을 허용 값. 마스터에서 읽는다."""

    model_config = ConfigDict(frozen=True)

    instruments: tuple[tuple[str, str], ...]
    indicators: tuple[tuple[str, str, str], ...]


class IndicatorTag(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    series_id: str


class Scores(BaseModel):
    model_config = ConfigDict(frozen=True)

    relevance: int = Field(ge=0, le=2)
    novelty: int = Field(ge=0, le=2)
    specificity: int = Field(ge=0, le=2)
    impact: int = Field(ge=0, le=2)

    @property
    def total(self) -> int:
        return sum(getattr(self, name) for name in SCORE_FIELDS)


class Assessment(BaseModel):
    """모델 응답. 서버 측 스키마 강제에 기대지 않고 여기서 검증한다."""

    model_config = ConfigDict(frozen=True)

    instruments: tuple[str, ...] = ()
    indicators: tuple[IndicatorTag, ...] = ()
    topics: tuple[str, ...] = ()
    # 검증기가 아니라 타입으로 막는다. Literal은 스키마에 enum으로 실려 모델이 애초에
    # 다른 값을 내지 못한다.
    direction: Literal["positive", "negative", "neutral"] = "neutral"
    scores: Scores
    new_facts: tuple[str, ...] = ()
    reason: str = ""


# 사람이 읽는 지시. 후보 목록은 실행 시점에 마스터에서 채워 뒤에 이어 붙인다.
INSTRUCTION = PROMPTS.instruction

# 형식이 깨졌을 때 붙이는 교정 지시. 한 번만 붙인다.
REPAIR_INSTRUCTION = PROMPTS.repair


def system_prompt(perspective: str) -> str:
    """그 관점으로 채운 시스템 프롬프트. 모르는 관점이면 죽는다."""
    if perspective not in PERSPECTIVES:
        raise AssessmentError(f"Unknown perspective: {perspective!r}")
    return PROMPTS.render("system", perspective=PERSPECTIVES[perspective], number_style=llm.NUMBER_STYLE)


class AssessState(TypedDict):
    """문서 하나를 평가하는 동안의 상태.

    `settings`는 여기 넣지 않는다. API 키가 들어 있고 상태는 트레이스 입력으로 나간다.
    """

    messages: list[BaseMessage]
    assessment: Assessment | None
    error: str | None
    attempts: int


class AssessmentResult(BaseModel):
    """문서 하나의 평가 결과. 실패해도 결과 하나로 돌아온다."""

    model_config = ConfigDict(frozen=True)

    document_id: int
    assessment: Assessment | None = None
    error: str | None = None
    # None은 응답 형식 오류, False는 재시도해도 해결되지 않는 제공처 오류, True는 일시 오류다.
    retryable: bool | None = None


class BatchState(TypedDict):
    documents: list[PendingDocument]
    candidates: Candidates
    # 문서마다 갈라진 노드가 각자 한 건씩 넣는다. 순서는 보장되지 않는다.
    results: Annotated[list[AssessmentResult], operator.add]


class DocumentAssessor:
    """문서 하나를 평가한다. 프롬프트, 파싱, 교정 재요청 흐름을 갖는다.

    흐름은 LangGraph 그래프다. 노드는 둘이다.

    - `call`: 스키마를 강제해 부르고 응답을 검증한다. 제공처가 스키마를 받지 않으면
      스키마 없이 한 번 더 부른다.
    - `repair`: 형식이 깨졌을 때 교정 지시를 붙인다. **한 번만** 붙는다.

    조건부 엣지가 `call` 다음을 정한다. 결과가 있으면 끝, 없고 아직 교정을 안 했으면
    `repair`, 그 밖이면 끝이다.
    """

    def __init__(self, model: BaseChatModel, settings: LlmSettings) -> None:
        self._model = model
        self._settings = settings
        self._schema = response_format(Assessment, "assessment")
        self._graph = self._build_graph()

    @staticmethod
    def build_messages(
        document: PendingDocument,
        candidates: Candidates,
        perspective: str = DEFAULT_PERSPECTIVE,
    ) -> list[BaseMessage]:
        """모델에 보낼 메시지. 후보 목록을 프롬프트에 실어 자유 문자열을 막는다."""
        system = system_prompt(perspective)
        instrument_lines = "\n".join(f"- {ticker}: {name}" for ticker, name in candidates.instruments)
        indicator_lines = "\n".join(
            f"- {provider}:{series_id} ({label})" for provider, series_id, label in candidates.indicators
        )
        parts = [
            INSTRUCTION,
            f"\n## 종목 후보\n{instrument_lines or '(없음)'}",
            f"\n## 지표 후보\n{indicator_lines or '(없음)'}",
            f"\n## 문서\n출처: {document.source_slug}",
            f"발행: {document.published_at.isoformat() if document.published_at else '알 수 없음'}",
            f"제목: {document.title}",
        ]
        if document.summary:
            parts.append(f"요약: {document.summary}")
        if document.body:
            parts.append(f"본문: {document.body}")
        return [SystemMessage(system), HumanMessage("\n".join(parts))]

    @staticmethod
    def parse(raw: str) -> Assessment:
        """모델 응답을 검증한다. 스키마 강제가 안 되는 제공처에서는 이것이 유일한 방어다."""
        try:
            return Assessment.model_validate_json(json_object(raw))
        except SchemaError as error:
            raise AssessmentError(str(error)) from None
        except ValidationError as error:
            raise AssessmentError(f"Model returned an invalid assessment: {error}") from None
        except json.JSONDecodeError as error:
            raise AssessmentError(f"Model returned malformed JSON: {error}") from None

    def assess(self, document: PendingDocument, candidates: Candidates) -> Assessment:
        """문서 하나를 평가한다.

        두 번째도 실패하면 `AssessmentError`를 올린다. 호출자는 그 문서를 태그 없이 두고
        다음 실행에 다시 집는다. 실패를 상태로 바꾸지 않는다.
        """
        state: AssessState = {
            "messages": self.build_messages(document, candidates, self._settings.perspective),
            "assessment": None,
            "error": None,
            "attempts": 0,
        }
        final = self._graph.invoke(
            state,
            config={"run_name": "assess_document", "metadata": {"document_id": document.id}},
        )
        assessment = final.get("assessment")
        if assessment is None:
            raise AssessmentError(final.get("error") or "Model did not return an assessment")
        return assessment

    def _build_graph(self):
        graph = StateGraph(AssessState)
        graph.add_node("call", self._call)
        graph.add_node("repair", self._repair)
        graph.add_edge(START, "call")
        graph.add_conditional_edges("call", self._next, {"repair": "repair", END: END})
        graph.add_edge("repair", "call")
        return graph.compile()

    def _call(self, state: AssessState) -> dict[str, Any]:
        """스키마를 강제해 한 번 부른다. 제공처가 스키마를 안 받으면 그때만 한 번 더."""
        messages = state["messages"]
        try:
            reply = llm.invoke(self._model, messages, schema=self._schema)
        except UnsupportedResponseFormat as error:
            logger.warning("provider does not accept a response schema; falling back to validation: %s", error)
            reply = llm.invoke(self._model, messages)

        try:
            return {"messages": [*messages, reply], "assessment": self.parse(_text(reply)), "error": None}
        except AssessmentError as error:
            return {"messages": [*messages, reply], "assessment": None, "error": str(error)}

    def _repair(self, state: AssessState) -> dict[str, Any]:
        """형식이 깨졌다. 교정 지시를 붙이고 한 번만 다시 묻는다."""
        logger.warning("retrying once after %s", state["error"])
        return {
            "messages": [*state["messages"], HumanMessage(REPAIR_INSTRUCTION)],
            "attempts": state["attempts"] + 1,
        }

    @staticmethod
    def _next(state: AssessState) -> str:
        if state["assessment"] is not None:
            return END
        return "repair" if state["attempts"] == 0 else END


class AssessmentBatch:
    """문서 여러 개를 평가한다. **저장은 하지 않는다.**

    문서마다 갈라진 노드가 각자 `DocumentAssessor`를 부르고 결과 한 건씩을 돌려준다.
    한 문서의 실패가 나머지로 번지지 않는다. 저장은 호출자가 문서마다 트랜잭션으로 한다.
    """

    def __init__(self, assessor: DocumentAssessor, max_concurrency: int = DEFAULT_MAX_CONCURRENCY) -> None:
        self._assessor = assessor
        self._max_concurrency = max_concurrency
        self._graph = self._build_graph()

    def run(
        self,
        documents: Sequence[PendingDocument],
        candidates: Candidates,
    ) -> tuple[AssessmentResult, ...]:
        if not documents:
            return ()
        final = self._graph.invoke(
            {"documents": list(documents), "candidates": candidates, "results": []},
            config={"run_name": "assess_batch", "max_concurrency": self._max_concurrency},
        )
        return tuple(final["results"])

    def _build_graph(self):
        graph = StateGraph(BatchState)
        graph.add_node("assess_one", self._assess_one)
        graph.add_conditional_edges(START, self._fan_out, ["assess_one"])
        graph.add_edge("assess_one", END)
        return graph.compile()

    @staticmethod
    def _fan_out(state: BatchState) -> list[Send]:
        return [
            Send("assess_one", {"document": document, "candidates": state["candidates"]})
            for document in state["documents"]
        ]

    def _assess_one(self, task: dict[str, Any]) -> dict[str, Any]:
        """문서 하나를 평가한다.

        문서별 모델 오류를 결과로 바꾼다. 성공 결과를 먼저 저장한 뒤 DAG가 재시도 여부를
        판단해야 하므로, 한 문서의 제공처 오류가 배치 전체 결과를 버리면 안 된다.

        `retryable=True`는 네트워크·429·5xx처럼 Airflow 재시도가 필요한 오류다. `False`는
        인증·잘못된 요청처럼 즉시 실패시킬 오류고, None은 이 문서의 응답 형식 오류다.
        """
        document: PendingDocument = task["document"]
        try:
            assessment = self._assessor.assess(document, task["candidates"])
        except AssessmentError as error:
            # 문서는 태그 없이 남는다. 다음 실행이 다시 집는다.
            logger.warning("document %s could not be assessed: %s", document.id, error)
            return {"results": [AssessmentResult(document_id=document.id, error=str(error))]}
        except (llm.RetryableLlmError, ConnectionError) as error:
            logger.warning("document %s hit a retryable LLM error: %s", document.id, error)
            return {"results": [AssessmentResult(document_id=document.id, error=str(error), retryable=True)]}
        except llm.LlmError as error:
            logger.error("document %s hit a non-retryable LLM error: %s", document.id, error)
            return {"results": [AssessmentResult(document_id=document.id, error=str(error), retryable=False)]}
        return {"results": [AssessmentResult(document_id=document.id, assessment=assessment)]}


def _text(message: AIMessage) -> str:
    """응답 본문을 문자열로. 제공처가 블록 배열로 답해도 같은 자리에서 흡수한다."""
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") for part in content if isinstance(part, dict))


def filter_tags(
    assessment: Assessment,
    candidates: Candidates,
    document_id: int,
) -> tuple[tuple[str, ...], tuple[IndicatorTag, ...]]:
    """마스터에 없는 태그를 버린다. 문서는 그대로 저장한다.

    버리기 전에 한 번 복원을 시도한다. 모델이 후보 목록을 대충 읽고 답하는 일이 있어서다.
    gpt-5.6-luna 실측(2026-08-20) 네 가지: `000660: SK하이닉스`를 instruments에,
    `kis:000660`을 series_id에, 지수 `KOSPI`를 instruments 칸에, provider를 `yahoo`로
    지어내서. **후보 목록에서 원래 값이 유일하게 복원되는 경우만** 복원한다 — 콜론 앞
    티커, series_id 앞 provider 접두사, 후보에서 provider가 하나뿐인 series_id의
    provider 교정, instruments 칸에 온 값이 지표 후보의 유일한 series_id일 때의 칸 이동.
    그래도 목록 밖이면 버린다. 같은 값으로 복원된 중복은 하나만 남긴다 — 저장 upsert가
    한 배치에서 같은 키를 두 번 만나면 죽는다.
    """
    allowed_instruments = {ticker for ticker, _ in candidates.instruments}
    allowed_indicators = {(provider, series_id) for provider, series_id, _ in candidates.indicators}
    # series_id마다 후보에 있는 provider들. 하나뿐이면 provider가 틀려도 유일하게 복원된다.
    series_providers: dict[str, set[str]] = {}
    for provider, series_id, _ in candidates.indicators:
        series_providers.setdefault(series_id, set()).add(provider)

    def _unique_indicator(series_id: str) -> IndicatorTag | None:
        providers = series_providers.get(series_id)
        if providers is not None and len(providers) == 1:
            return IndicatorTag(provider=next(iter(providers)), series_id=series_id)
        return None

    instruments: list[str] = []
    indicators: list[IndicatorTag] = []
    dropped_instruments: list[str] = []
    dropped_indicators: list[tuple[str, str]] = []

    def _keep_indicator(tag: IndicatorTag) -> None:
        if tag not in indicators:
            indicators.append(tag)

    for value in assessment.instruments:
        ticker = value if value in allowed_instruments else value.split(":", 1)[0].strip()
        if ticker in allowed_instruments:
            if ticker not in instruments:
                instruments.append(ticker)
            continue
        # 지수·환율을 종목 칸에 넣는 실수. 지표 후보에서 유일하면 그쪽으로 옮긴다.
        moved = _unique_indicator(ticker)
        if moved is not None:
            _keep_indicator(moved)
        else:
            dropped_instruments.append(value)

    for tag in assessment.indicators:
        series_id = tag.series_id
        if (tag.provider, series_id) not in allowed_indicators:
            series_id = series_id.removeprefix(f"{tag.provider}:")
        if (tag.provider, series_id) in allowed_indicators:
            _keep_indicator(tag.model_copy(update={"series_id": series_id}))
            continue
        # provider를 지어내는 실수. series_id의 후보 provider가 하나뿐이면 교정한다.
        corrected = _unique_indicator(series_id)
        if corrected is not None:
            _keep_indicator(corrected)
        else:
            dropped_indicators.append((tag.provider, tag.series_id))

    if dropped_instruments or dropped_indicators:
        # 마스터를 늘릴 근거다. 조용히 버리면 무엇을 놓치고 있는지 알 수 없다.
        logger.warning(
            "document %s: dropped unknown tags instruments=%s indicators=%s",
            document_id,
            sorted(set(dropped_instruments)),
            sorted(set(dropped_indicators)),
        )
    return tuple(instruments), tuple(indicators)


PENDING_DOCUMENTS = read_sql("postgres", "document", "select_pending_assessment.sql")
UPDATE_ASSESSMENT = read_sql("postgres", "document", "update_assessment.sql")
INSTRUMENT_CANDIDATES = read_sql("postgres", "instrument", "select_watched.sql")
INDICATOR_CANDIDATES = read_sql("postgres", "indicator_series", "select_candidates.sql")
DOCUMENT_INSTRUMENT_UPSERT = read_sql("postgres", "document_instrument", "upsert.sql")
DOCUMENT_INDICATOR_UPSERT = read_sql("postgres", "document_indicator", "upsert.sql")


class AssessmentStore:
    """평가 원장을 읽고 쓴다. **연결과 프롬프트 리비전이 상태다.**

    전에는 조회 둘과 저장 하나가 각각 `connection`을 받고, 그중 둘이 `prompt_revision`까지
    다시 받았다. 관점을 하나 더 얹으면 그 인자가 세 자리에서 같이 늘어난다.

    **연결은 생성자가 받는다.** 수집기와 달리 여기서는 트랜잭션 하나가 객체 하나다 —
    DAG이 문서마다 새 연결을 열어 저장하므로(앞의 성공을 뒤의 실패가 되돌리지 않게)
    그 연결의 수명이 곧 이 객체의 수명이다.
    """

    def __init__(
        self,
        connection: Connection,
        prompt_revision: str = f"{PROMPT_VERSION}/{DEFAULT_PERSPECTIVE}",
    ) -> None:
        self._connection = connection
        self._prompt_revision = prompt_revision

    def candidates(self) -> Candidates:
        """프롬프트에 넣을 허용 값을 마스터에서 읽는다."""
        with self._connection.cursor() as cursor:
            cursor.execute(INSTRUMENT_CANDIDATES)
            instruments = tuple((row[0], row[1]) for row in cursor.fetchall())
            cursor.execute(INDICATOR_CANDIDATES)
            indicators = tuple((row[0], row[1], row[2]) for row in cursor.fetchall())
        return Candidates(instruments=instruments, indicators=indicators)

    def pending(self, limit: int = DEFAULT_BATCH_SIZE) -> tuple[PendingDocument, ...]:
        """아직 평가하지 않았거나 본문·프롬프트가 바뀐 문서.

        `prompt_revision`에는 관점이 함께 들어 있다(`LlmSettings.prompt_revision`). 관점을 바꾸면
        같은 문서라도 점수가 달라지므로 전부 재평가 대상이 된다.
        """
        with self._connection.cursor() as cursor:
            cursor.execute(PENDING_DOCUMENTS, (self._prompt_revision, limit))
            rows = cursor.fetchall()
        return tuple(
            PendingDocument(
                id=row[0],
                source_slug=row[1],
                title=row[2],
                summary=row[3],
                body=row[4],
                language=row[5],
                published_at=row[6],
                content_hash=row[7],
            )
            for row in rows
        )

    def store(
        self,
        document: PendingDocument,
        assessment: Assessment,
        instruments: Sequence[str],
        indicators: Sequence[IndicatorTag],
        model: str,
        assessed_at: datetime | None = None,
    ) -> None:
        """평가 결과와 태그를 저장한다. 문서 하나가 트랜잭션 하나다(커밋은 호출자가 한다)."""
        payload = assessment.model_dump()
        with self._connection.cursor() as cursor:
            cursor.execute(
                UPDATE_ASSESSMENT,
                (
                    assessment.direction,
                    assessment.scores.total,
                    json.dumps(payload, ensure_ascii=False),
                    model,
                    self._prompt_revision,
                    document.content_hash,
                    assessed_at or datetime.now(UTC),
                    document.id,
                ),
            )
            if instruments:
                execute_upserts(
                    cursor,
                    DOCUMENT_INSTRUMENT_UPSERT,
                    [(document.id, ticker) for ticker in instruments],
                )
            if indicators:
                execute_upserts(
                    cursor,
                    DOCUMENT_INDICATOR_UPSERT,
                    [(document.id, tag.provider, tag.series_id) for tag in indicators],
                )
