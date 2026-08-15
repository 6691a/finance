import re
from datetime import UTC, datetime
from typing import Self

import pytest
from sqlalchemy import Table

from apps.models.content import Document as DocumentModel
from apps.models.content import DocumentIndicator, DocumentInstrument
from modules.assessment import (
    DEFAULT_PERSPECTIVE,
    DOCUMENT_INDICATOR_UPSERT,
    DOCUMENT_INSTRUMENT_UPSERT,
    PENDING_DOCUMENTS,
    PERSPECTIVES,
    PROMPT_VERSION,
    UPDATE_ASSESSMENT,
    Assessment,
    AssessmentError,
    Candidates,
    IndicatorTag,
    LlmSettings,
    PendingDocument,
    assess,
    build_messages,
    filter_tags,
    parse_assessment,
    store_assessment,
)
from modules.llm import AssistantMessage

ASSESSED_AT = datetime(2026, 8, 15, 3, 25, tzinfo=UTC)

CANDIDATES = Candidates(
    instruments=(("005930", "삼성전자"), ("000660", "SK하이닉스")),
    indicators=(("yahoo", "USDKRW", "원/달러"), ("fred", "DGS10", "미국 10년물")),
)

DOCUMENT = PendingDocument(
    id=11,
    source_slug="yonhap",
    title="원/달러 환율 급등",
    summary="장중 1,400원을 넘었다.",
    body=None,
    language="ko",
    published_at=datetime(2026, 8, 14, 22, 30, tzinfo=UTC),
    content_hash="abc",
)

VALID = """{"instruments": ["005930"],
 "indicators": [{"provider": "yahoo", "series_id": "USDKRW"}],
 "topics": ["fx"], "direction": "negative",
 "scores": {"relevance": 2, "novelty": 1, "specificity": 2, "impact": 1},
 "new_facts": ["환율이 1,400원을 넘었다"], "reason": "수출주 원가에 직접 영향"}"""


class FakeClient:
    """`modules.llm.ChatClient`를 흉내 낸다. 실제 호출은 하지 않는다."""

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.calls: list[list[dict[str, str]]] = []
        self.schemas: list[dict | None] = []

    def complete(self, *, model: str, messages, tools=None, response_format=None) -> AssistantMessage:
        self.calls.append(list(messages))
        self.schemas.append(response_format)
        return AssistantMessage(content=self.replies.pop(0))


class FakeCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, statement: str, parameters: tuple = ()) -> None:
        self.calls.append((statement, parameters))

    def executemany(self, statement: str, parameters) -> None:
        self.calls.extend((statement, tuple(row)) for row in parameters)

    def fetchall(self) -> list:
        return []


class FakeConnection:
    def __init__(self) -> None:
        self.recorded_cursor = FakeCursor()

    def cursor(self) -> FakeCursor:
        return self.recorded_cursor


@pytest.fixture(autouse=True)
def without_the_psycopg2_fast_path(monkeypatch):
    monkeypatch.setattr("modules.upsert._execute_batch", None)


def settings(perspective: str = DEFAULT_PERSPECTIVE) -> LlmSettings:
    return LlmSettings(
        base_url="https://example.invalid/v1",
        api_key="secret",
        chat_model="test-model",
        perspective=perspective,
    )


def inserted_columns(statement: str) -> tuple[str, ...]:
    columns = re.search(r"INSERT INTO \w+ \(([^)]+)\)", statement, re.DOTALL)
    assert columns is not None
    return tuple(name.strip() for name in re.sub(r"--[^\n]*", "", columns.group(1)).split(",") if name.strip())


def natural_key(table: Table, name: str) -> tuple[str, ...]:
    return next(
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if constraint.name == name
    )


def test_api_key_does_not_leak_when_the_settings_are_printed():
    # URL에 키가 실리지 않더라도 예외 메시지와 로그에 객체가 찍힐 수 있다.
    assert "secret" not in repr(settings())
    assert "secret" not in str(settings())


def test_missing_environment_is_reported_without_the_values(monkeypatch):
    for name in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_CHAT_MODEL"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(AssessmentError, match="Missing LLM settings"):
        LlmSettings.from_environment()


def test_prompt_lists_the_allowed_values():
    # 자유 문자열을 받으면 document_instrument가 instrument와 조인되지 않는다.
    messages = build_messages(DOCUMENT, CANDIDATES)
    prompt = messages[-1]["content"]

    assert "005930: 삼성전자" in prompt
    assert "yahoo:USDKRW" in prompt
    assert DOCUMENT.title in prompt


def test_relevance_may_be_zero_and_the_prompt_says_so():
    """실측(grok-4)에서 태그를 하나도 안 달고도 relevance를 1로 줬다.

    0을 안 쓰면 점수 바닥이 1로 올라가 0~8이 아니라 4~8이 된다. 상위 몇 건을 고르는 것이 이
    점수의 유일한 쓸모라 눌리면 못 쓴다.
    """
    prompt = build_messages(DOCUMENT, CANDIDATES)[-1]["content"]

    assert "0을 쓰는 것을 주저하지 마라" in prompt
    assert "둘 다 비었다면 relevance는 0" in prompt


def test_relevance_is_asked_as_a_path_not_as_a_direct_mention():
    """켜져 있는 피드 아홉 중 여섯이 비한국이다.

    "한국에 직접 관련되는가"로 물으면 한국을 언급하지 않는 연준 성명이 0점을 받는다. 그
    문서들을 모으는 이유가 전이 효과이므로 경로를 묻는다.
    """
    prompt = build_messages(DOCUMENT, CANDIDATES)[-1]["content"]

    assert "직접 언급을 요구하지 않는다" in prompt
    assert "경로" in prompt


def test_the_default_perspective_is_global():
    # 기본이 한국 전용이면 아카이브의 대부분이 잘못 채점된다.
    assert DEFAULT_PERSPECTIVE == "global"
    system = build_messages(DOCUMENT, CANDIDATES)[0]["content"]
    assert PERSPECTIVES["global"] in system


def test_the_perspective_changes_the_system_prompt():
    korea = build_messages(DOCUMENT, CANDIDATES, "korea")[0]["content"]
    united_states = build_messages(DOCUMENT, CANDIDATES, "us")[0]["content"]

    assert PERSPECTIVES["korea"] in korea
    assert PERSPECTIVES["us"] in united_states
    # 문서 부분은 관점과 무관하게 같아야 한다.
    assert build_messages(DOCUMENT, CANDIDATES, "korea")[-1] == build_messages(DOCUMENT, CANDIDATES, "us")[-1]


def test_an_unknown_perspective_is_rejected():
    with pytest.raises(AssessmentError, match="Unknown perspective"):
        build_messages(DOCUMENT, CANDIDATES, "mars")


def test_the_prompt_revision_carries_the_perspective():
    """관점이 바뀌면 같은 문서라도 점수가 달라진다. 재평가 대상이 돼야 한다."""
    assert settings("global").prompt_revision == f"{PROMPT_VERSION}/global"
    assert settings("korea").prompt_revision != settings("global").prompt_revision


def test_the_perspective_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("LLM_API_KEY", "secret")
    monkeypatch.setenv("LLM_CHAT_MODEL", "test-model")

    monkeypatch.delenv("LLM_PERSPECTIVE", raising=False)
    assert LlmSettings.from_environment().perspective == DEFAULT_PERSPECTIVE

    monkeypatch.setenv("LLM_PERSPECTIVE", "us")
    assert LlmSettings.from_environment().perspective == "us"

    monkeypatch.setenv("LLM_PERSPECTIVE", "mars")
    with pytest.raises(AssessmentError, match="LLM_PERSPECTIVE must be one of"):
        LlmSettings.from_environment()


def test_parse_accepts_a_reply_wrapped_in_a_code_fence():
    # 제3자 OpenAI 호환 제공자가 JSON만 내라는 지시를 안 지키는 경우가 있다.
    assessment = parse_assessment(f"```json\n{VALID}\n```")

    assert assessment.direction == "negative"
    assert assessment.scores.total == 6


def test_parse_rejects_a_score_outside_the_range():
    with pytest.raises(AssessmentError, match="invalid assessment"):
        parse_assessment(VALID.replace('"relevance": 2', '"relevance": 5'))


def test_parse_rejects_an_unknown_direction():
    with pytest.raises(AssessmentError, match="invalid assessment"):
        parse_assessment(VALID.replace('"negative"', '"bullish"'))


def test_parse_rejects_a_reply_without_a_json_object():
    with pytest.raises(AssessmentError, match="did not return a JSON object"):
        parse_assessment("판단할 수 없습니다.")


def test_assess_forces_the_output_schema():
    """검증만 하지 않고 강제한다. 제공처가 받으면 깨진 응답이 아예 오지 않는다."""
    client = FakeClient(VALID)

    assess(client, settings(), DOCUMENT, CANDIDATES)

    schema = client.schemas[0]
    assert schema["type"] == "json_schema"
    assert schema["json_schema"]["strict"] is True
    properties = schema["json_schema"]["schema"]["properties"]
    # direction은 검증기가 아니라 스키마의 enum이 막는다.
    assert properties["direction"]["enum"] == ["positive", "negative", "neutral"]


def test_assess_retries_once_when_the_format_is_broken():
    client = FakeClient("형식이 틀린 답", VALID)

    assessment = assess(client, settings(), DOCUMENT, CANDIDATES)

    assert assessment.scores.total == 6
    assert len(client.calls) == 2
    # 교정 요청은 원래 메시지 뒤에 한 줄만 붙인다.
    assert len(client.calls[1]) == len(client.calls[0]) + 1


def test_assess_gives_up_after_the_second_failure():
    client = FakeClient("틀림", "또 틀림")

    with pytest.raises(AssessmentError):
        assess(client, settings(), DOCUMENT, CANDIDATES)
    assert len(client.calls) == 2


def test_unknown_tags_are_dropped_and_the_document_survives():
    assessment = Assessment.model_validate_json(VALID).model_copy(
        update={
            "instruments": ("005930", "999999"),
            "indicators": (
                IndicatorTag(provider="yahoo", series_id="USDKRW"),
                IndicatorTag(provider="x", series_id="y"),
            ),
        }
    )

    instruments, indicators = filter_tags(assessment, CANDIDATES, DOCUMENT.id)

    # 태그 하나 때문에 문서를 잃지 않는다.
    assert instruments == ("005930",)
    assert indicators == (IndicatorTag(provider="yahoo", series_id="USDKRW"),)


def test_pending_query_covers_the_three_reasons_to_assess():
    # 한 번도 평가하지 않은 문서, 본문이 바뀐 문서, 프롬프트 버전이 오른 문서.
    assert "assessed_at IS NULL" in PENDING_DOCUMENTS
    assert "assessed_content_hash IS DISTINCT FROM content_hash" in PENDING_DOCUMENTS
    assert "prompt_version IS DISTINCT FROM %s" in PENDING_DOCUMENTS


def test_update_statement_only_touches_assessment_columns():
    # 평가가 문서의 내용이나 계보를 바꾸면 안 된다.
    assert "title" not in UPDATE_ASSESSMENT
    # `assessed_content_hash`는 쓰지만 문서 자신의 `content_hash`는 건드리지 않는다.
    assert re.search(r"(?<!assessed_)content_hash\s*=", UPDATE_ASSESSMENT) is None
    for column in ("direction", "value_score", "assessment", "llm_model", "prompt_version", "assessed_at"):
        assert column in UPDATE_ASSESSMENT
    assert {
        "direction",
        "value_score",
        "assessment",
        "llm_model",
        "prompt_version",
        "assessed_content_hash",
        "assessed_at",
    } <= {column.name for column in DocumentModel.__table__.columns}


def test_tag_upserts_match_the_models_and_their_natural_keys():
    for statement, model, name in (
        (DOCUMENT_INSTRUMENT_UPSERT, DocumentInstrument, "uq_document_instrument_natural_key"),
        (DOCUMENT_INDICATOR_UPSERT, DocumentIndicator, "uq_document_indicator_natural_key"),
    ):
        columns = inserted_columns(statement)
        assert set(columns) <= {column.name for column in model.__table__.columns}
        key = natural_key(model.__table__, name)
        assert set(key) <= set(columns)
        assert f"ON CONFLICT ({', '.join(key)}) DO NOTHING" in statement


def test_store_writes_the_assessment_and_both_tag_kinds():
    connection = FakeConnection()
    assessment = parse_assessment(VALID)
    instruments, indicators = filter_tags(assessment, CANDIDATES, DOCUMENT.id)

    store_assessment(
        connection,
        DOCUMENT,
        assessment,
        instruments,
        indicators,
        "test-model",
        ASSESSED_AT,
        settings().prompt_revision,
    )

    calls = connection.recorded_cursor.calls
    update = next(parameters for statement, parameters in calls if "UPDATE document" in statement)
    direction, score, payload, model, prompt_version, hashed, assessed_at, document_id = update
    assert (direction, score) == ("negative", 6)
    assert (model, prompt_version) == ("test-model", f"{PROMPT_VERSION}/{DEFAULT_PERSPECTIVE}")
    # 어떤 본문으로 평가했는지를 남겨야 재평가 판단이 선다.
    assert hashed == DOCUMENT.content_hash
    assert (assessed_at, document_id) == (ASSESSED_AT, DOCUMENT.id)
    assert '"relevance": 2' in payload

    assert [p for s, p in calls if "INSERT INTO document_instrument" in s] == [(DOCUMENT.id, "005930")]
    assert [p for s, p in calls if "INSERT INTO document_indicator" in s] == [(DOCUMENT.id, "yahoo", "USDKRW")]


def test_store_skips_the_tag_statements_when_there_is_nothing_to_tag():
    connection = FakeConnection()
    assessment = parse_assessment(VALID)

    store_assessment(connection, DOCUMENT, assessment, (), (), "test-model", ASSESSED_AT, settings().prompt_revision)

    statements = [statement for statement, _ in connection.recorded_cursor.calls]
    assert not any("INSERT INTO document_instrument" in statement for statement in statements)
    assert any("UPDATE document" in statement for statement in statements)
