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

## 필요한 환경

`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_CHAT_MODEL`. `config.yaml`은 Airflow가 읽지 못하므로
환경변수로 준다. 키는 `SecretStr`로 받고 로그와 예외 메시지에 넣지 않는다.
`LLM_PERSPECTIVE`는 선택이며 기본은 `global`이다.
"""

import json
import logging
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator

from modules.llm import ChatClient, answer
from modules.schema import response_format
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

REQUEST_TIMEOUT_SECONDS = 60

# 한 번 실행에서 평가할 문서 수. 시간당 수집량보다 넉넉하되 예산이 한 번에 새지 않게 둔다.
DEFAULT_BATCH_SIZE = 50

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
    """모델 접속 설정. 키는 로그에 남지 않게 `SecretStr`로 받는다."""

    model_config = ConfigDict(frozen=True)

    base_url: str
    api_key: SecretStr
    chat_model: str
    perspective: str = DEFAULT_PERSPECTIVE

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
        missing = [name for name in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_CHAT_MODEL") if not os.environ.get(name)]
        if missing:
            raise AssessmentError(f"Missing LLM settings: {', '.join(missing)}")
        perspective = os.environ.get("LLM_PERSPECTIVE") or DEFAULT_PERSPECTIVE
        if perspective not in PERSPECTIVES:
            raise AssessmentError(f"LLM_PERSPECTIVE must be one of {sorted(PERSPECTIVES)}, got {perspective!r}")
        return cls(
            base_url=os.environ["LLM_BASE_URL"],
            api_key=SecretStr(os.environ["LLM_API_KEY"]),
            chat_model=os.environ["LLM_CHAT_MODEL"],
            perspective=perspective,
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


def build_messages(
    document: PendingDocument,
    candidates: Candidates,
    perspective: str = DEFAULT_PERSPECTIVE,
) -> list[dict[str, str]]:
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
        {"role": "system", "content": SYSTEM_PROMPT_TEMPLATE.format(perspective=PERSPECTIVES[perspective])},
        {"role": "user", "content": "\n".join(parts)},
    ]


def _json_object(raw: str) -> str:
    """코드 펜스나 앞뒤 설명이 붙어 와도 JSON 객체만 뽑는다.

    제3자 OpenAI 호환 제공자는 JSON만 내라는 지시를 지키지 않는 경우가 있다. 첫 `{`부터
    마지막 `}`까지를 잘라 낸다.
    """
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end <= start:
        raise AssessmentError("Model did not return a JSON object")
    return raw[start : end + 1]


def parse_assessment(raw: str) -> Assessment:
    try:
        return Assessment.model_validate_json(_json_object(raw))
    except ValidationError as error:
        raise AssessmentError(f"Model returned an invalid assessment: {error}") from None
    except json.JSONDecodeError as error:
        raise AssessmentError(f"Model returned malformed JSON: {error}") from None


def assess(
    client: ChatClient,
    settings: LlmSettings,
    document: PendingDocument,
    candidates: Candidates,
) -> Assessment:
    """문서 하나를 평가한다.

    응답 형식은 `response_format`으로 **강제한다**. 제공처가 그걸 받으면 깨진 응답이 아예
    오지 않는다. 받지 않는 제공처에서는 프롬프트와 검증이 형식을 지키고, 그때만 교정을
    한 번 요청한다.

    두 번째도 실패하면 `AssessmentError`를 올린다. 호출자는 그 문서를 태그 없이 두고 다음
    실행에 다시 집는다. 실패를 상태로 바꾸지 않는다.
    """
    messages = build_messages(document, candidates, settings.perspective)
    schema = response_format(Assessment, "assessment")
    try:
        return parse_assessment(answer(client, settings.chat_model, messages, schema))
    except AssessmentError as first:
        # 스키마를 강제했는데도 깨졌다면 제공처가 그걸 못 받아 검증 경로로 떨어진 것이다.
        logger.warning("document %s: retrying once after %s", document.id, first)

    return parse_assessment(
        answer(
            client,
            settings.chat_model,
            messages,
            schema,
            "이전 응답이 형식에 맞지 않았다. JSON 객체 하나만 다시 출력하라.",
        )
    )


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
