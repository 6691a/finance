"""수집한 문서를 LLM으로 태깅하고 점수를 매긴다.

`docs/economic-document-archive-design.md` 2단계의 LLM 절반이다. 수집(`collectors/documents.py`)과
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

## 관점은 값이지 프롬프트가 아니다

초판은 "한국 투자자를 위한 분석기"로 고정돼 있었다. 그런데 **켜져 있는 피드 아홉 중 여섯이
비한국이고, 그것들을 모으는 이유가 전이 효과다.** 미국 금리와 달러, 반도체 업황은 한국을
한 번도 언급하지 않으면서 한국 자산 가격을 움직인다. "한국에 직접 관련되는가"로 물으면
그 문서들이 전부 0점을 받는다.

그래서 관점을 `PERSPECTIVES`의 문자열 하나로 빼고 기본을 `global`로 뒀다. `relevance`도
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

`XAI_API_KEY` 하나가 필수다. **우리가 읽지 않는다.** 어떤 모델을 부를지는 `modules/llm.py`가
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
from typing import Annotated, Any, Literal, Protocol, Self, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from modules import llm
from modules.llm import UnsupportedResponseFormat
from modules.schema import SchemaError, json_object, response_format
from modules.sql import read_sql
from modules.upsert import execute_upserts

logger = logging.getLogger(__name__)

# 프롬프트를 고치면 올린다. 이 값이 오른 문서는 재평가 대상이 된다.
PROMPT_VERSION = "3"

# 어느 시장의 눈으로 볼 것인가. **관점을 바꾸는 것이 프롬프트를 새로 쓰는 일이면 안 된다.**
#
# 초판은 "한국 투자자를 위한 분석기"로 고정돼 있었다. 그런데 켜져 있는 피드 아홉 중 여섯이
# 비한국(Fed, BEA, BLS, BBC, CNBC, NPR)이고, 그것들을 모으는 이유가 바로 전이 효과다.
# 한국을 언급하지 않는 연준 성명이 낮은 점수를 받으면 아카이브의 대부분이 잘못 채점된다.
#
# 그래서 관점을 문자열 하나로 빼고 기본을 `global`로 둔다. 나중에 미국 시장만 보는 리포트가
# 필요해지면 프롬프트를 복사하는 대신 이 값을 바꾼다.
PERSPECTIVES: dict[str, str] = {
    "global": (
        "세계 경제 문서를 읽고 **한국 시장에 어떤 경로로 전달되는지**를 함께 판단한다. "
        "미국·유럽·중국·일본에서 일어난 일이 환율, 금리, 수요, 공급망, 투자심리를 타고 "
        "한국 자산 가격에 닿는다. 한국을 언급하지 않는 문서라도 그 경로가 뚜렷하면 관련성이 높다."
    ),
    "korea": (
        "한국 시장에서 직접 일어난 일만 본다. 국내 정책, 국내 기업, 국내 지표가 대상이며 "
        "해외 소식은 한국을 명시적으로 다룰 때만 관련성이 있다."
    ),
    "us": (
        "미국 시장의 눈으로 본다. 연준 정책, 미국 지표, 미국 상장 기업이 대상이며 "
        "다른 지역 소식은 미국 자산 가격에 닿는 경로가 있을 때 관련성이 있다."
    ),
}

DEFAULT_PERSPECTIVE = "global"

# 한 번 실행에서 평가할 문서 수. 시간당 수집량보다 넉넉하되 예산이 한 번에 새지 않게 둔다.
DEFAULT_BATCH_SIZE = 50

# 한 배치에서 동시에 부를 문서 수. 제공처 rate limit과 비용 흐름을 바꾸는 값이라 작게 둔다.
DEFAULT_MAX_CONCURRENCY = 4

# 점수 항목. 각 0~2점이고 합이 `value_score`다.
SCORE_FIELDS = ("relevance", "novelty", "specificity", "impact")


class Cursor(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(self, *args: object) -> bool | None: ...

    def execute(self, statement: str, parameters: Sequence[Any] = ()) -> object: ...

    def executemany(self, statement: str, parameters: Sequence[Sequence[Any]]) -> object: ...

    def fetchall(self) -> Any: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...


class AssessmentError(RuntimeError):
    """모델이 우리가 아는 모양으로 답하지 않았다. 문서는 태그 없이 남는다."""


class LlmSettings(BaseModel):
    """평가 설정.

    **접속 정보는 여기 없다.** 어떤 모델을 부를지는 `modules/llm.py`가 코드로 정하고 키는
    LangChain이 자기 환경변수(`XAI_API_KEY`)에서 읽는다. 여기 남은 것은 제공처와 무관하게
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


SYSTEM_PROMPT_TEMPLATE = (
    "당신은 경제 문서 분석기다. {perspective} "
    "주어진 문서를 읽고 어떤 종목과 지표에 관련되는지, 얼마나 값있는 정보인지 판단한다. "
    "반드시 JSON 객체 하나만 출력한다. 설명이나 코드 펜스를 붙이지 않는다."
)

# 사람이 읽는 지시. 후보 목록은 실행 시점에 마스터에서 채운다.
#
# **`relevance`를 "직접 관련"으로 묻지 않는다.** 그렇게 물으면 한국을 언급하지 않는 연준
# 성명이 0점을 받는다. 우리가 Fed·BEA·BLS·BIS를 모으는 이유가 그 문서들이 경로를 타고
# 도착하기 때문이므로, 경로의 존재와 길이를 묻는다.
#
# 반대 방향으로도 못을 박는다. 실측(grok-4, 2026-08-15)에서 용산 주택공급 기사에 태그를 하나도
# 달지 않고 `reason`에 "직접 관련이 없다"고 쓰면서 relevance를 1로 줬다. **0을 안 쓰면 점수
# 바닥이 1로 올라가 0~8이 아니라 4~8이 된다.** 상위 몇 건을 고르는 것이 이 점수의 유일한
# 쓸모라 눌리면 못 쓴다. 그래서 "태그가 비면 0"이라는 검사 가능한 규칙을 함께 준다.
INSTRUCTION = """\
아래 문서를 읽고 JSON으로 답하라.

규칙:
- `instruments`와 `indicators`는 **후보 목록에 있는 값만** 쓴다. 없으면 빈 배열로 둔다.
  문서가 다른 나라 이야기여도, 그 일이 후보의 가격에 닿는 경로가 뚜렷하면 태그한다.
- `direction`은 태그한 종목·지표의 가격 관점에서 positive, negative, neutral 중 하나다.
- `scores`의 네 항목은 각각 0~2 정수다.
  - relevance: 관심 시장에 닿는 경로가 있는가. **직접 언급을 요구하지 않는다.**
    경로가 뚜렷하고 짧으면 2, 있으나 멀면 1, 없으면 0이다.
    **0을 쓰는 것을 주저하지 마라.** 관련이 없다고 판단했으면 예의로 1을 주지 않는다.
    `instruments`와 `indicators`가 둘 다 비었다면 relevance는 0이다.
  - novelty: 이미 알려진 사실의 반복이 아니라 새 정보인가
  - specificity: 수치·일정·주체가 구체적인가
  - impact: 가격에 미칠 영향이 큰가
- `new_facts`는 이 문서가 새로 알려 준 사실을 짧은 문장으로 담는다.
- `reason`은 두 문장 이내로 쓰되 **어떤 경로로 닿는지를 밝힌다**(환율, 금리, 수요,
  공급망, 투자심리 등). 경로를 말할 수 없으면 relevance를 0으로 둔다.

출력 형식:
{"instruments": [], "indicators": [{"provider": "", "series_id": ""}], "topics": [],
 "direction": "neutral", "scores": {"relevance": 0, "novelty": 0, "specificity": 0, "impact": 0},
 "new_facts": [], "reason": ""}
"""


# 형식이 깨졌을 때 붙이는 교정 지시. 한 번만 붙인다.
REPAIR_INSTRUCTION = "이전 응답이 형식에 맞지 않았다. JSON 객체 하나만 다시 출력하라."


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
        if perspective not in PERSPECTIVES:
            raise AssessmentError(f"Unknown perspective: {perspective!r}")
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
        return [
            SystemMessage(SYSTEM_PROMPT_TEMPLATE.format(perspective=PERSPECTIVES[perspective])),
            HumanMessage("\n".join(parts)),
        ]

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
        """문서 하나를 평가한다. **모델에 닿지 못한 실패는 잡지 않는다.**

        `AssessmentError`만 결과로 바꾼다. 그건 이 문서의 응답 형식이 두 번 깨졌다는 뜻이고,
        문서 하나의 문제라 나머지를 저장하는 것이 설계다.

        제공처 예외(`ConnectionError`, `LlmError`)는 그대로 위로 올린다. 키가 틀렸거나
        네트워크가 끊긴 것은 이 문서의 문제가 아니라 남은 문서 전부가 똑같이 실패할 문제다.
        잡아서 결과로 담으면 원인이 문자열로 뭉개져 DAG가 재시도 여부를 가를 수 없고,
        태스크는 "0건 처리" 성공으로 끝난다. 재시도 여부 판단은 DAG가 한다.
        """
        document: PendingDocument = task["document"]
        try:
            assessment = self._assessor.assess(document, task["candidates"])
        except AssessmentError as error:
            # 문서는 태그 없이 남는다. 다음 실행이 다시 집는다.
            logger.warning("document %s could not be assessed: %s", document.id, error)
            return {"results": [AssessmentResult(document_id=document.id, error=str(error))]}
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
    """마스터에 없는 태그를 버린다. 문서는 그대로 저장한다."""
    allowed_instruments = {ticker for ticker, _ in candidates.instruments}
    allowed_indicators = {(provider, series_id) for provider, series_id, _ in candidates.indicators}

    instruments = tuple(ticker for ticker in assessment.instruments if ticker in allowed_instruments)
    indicators = tuple(tag for tag in assessment.indicators if (tag.provider, tag.series_id) in allowed_indicators)

    dropped_instruments = set(assessment.instruments) - allowed_instruments
    dropped_indicators = {(tag.provider, tag.series_id) for tag in assessment.indicators} - allowed_indicators
    if dropped_instruments or dropped_indicators:
        # 마스터를 늘릴 근거다. 조용히 버리면 무엇을 놓치고 있는지 알 수 없다.
        logger.warning(
            "document %s: dropped unknown tags instruments=%s indicators=%s",
            document_id,
            sorted(dropped_instruments),
            sorted(dropped_indicators),
        )
    return instruments, indicators


PENDING_DOCUMENTS = read_sql("postgres", "document", "select_pending_assessment.sql")
UPDATE_ASSESSMENT = read_sql("postgres", "document", "update_assessment.sql")
INSTRUMENT_CANDIDATES = read_sql("postgres", "instrument", "select_watched.sql")
INDICATOR_CANDIDATES = read_sql("postgres", "indicator_series", "select_candidates.sql")
DOCUMENT_INSTRUMENT_UPSERT = read_sql("postgres", "document_instrument", "upsert.sql")
DOCUMENT_INDICATOR_UPSERT = read_sql("postgres", "document_indicator", "upsert.sql")


def load_candidates(connection: Connection) -> Candidates:
    """프롬프트에 넣을 허용 값을 마스터에서 읽는다."""
    with connection.cursor() as cursor:
        cursor.execute(INSTRUMENT_CANDIDATES)
        instruments = tuple((row[0], row[1]) for row in cursor.fetchall())
        cursor.execute(INDICATOR_CANDIDATES)
        indicators = tuple((row[0], row[1], row[2]) for row in cursor.fetchall())
    return Candidates(instruments=instruments, indicators=indicators)


def pending_documents(
    connection: Connection,
    limit: int = DEFAULT_BATCH_SIZE,
    prompt_revision: str = f"{PROMPT_VERSION}/{DEFAULT_PERSPECTIVE}",
) -> tuple[PendingDocument, ...]:
    """아직 평가하지 않았거나 본문·프롬프트가 바뀐 문서.

    `prompt_revision`에는 관점이 함께 들어 있다(`LlmSettings.prompt_revision`). 관점을 바꾸면
    같은 문서라도 점수가 달라지므로 전부 재평가 대상이 된다.
    """
    with connection.cursor() as cursor:
        cursor.execute(PENDING_DOCUMENTS, (prompt_revision, limit))
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


def store_assessment(
    connection: Connection,
    document: PendingDocument,
    assessment: Assessment,
    instruments: Sequence[str],
    indicators: Sequence[IndicatorTag],
    model: str,
    assessed_at: datetime | None = None,
    prompt_revision: str = f"{PROMPT_VERSION}/{DEFAULT_PERSPECTIVE}",
) -> None:
    """평가 결과와 태그를 저장한다. 문서 하나가 트랜잭션 하나다(커밋은 호출자가 한다)."""
    payload = assessment.model_dump()
    with connection.cursor() as cursor:
        cursor.execute(
            UPDATE_ASSESSMENT,
            (
                assessment.direction,
                assessment.scores.total,
                json.dumps(payload, ensure_ascii=False),
                model,
                prompt_revision,
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
