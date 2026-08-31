import re
from datetime import UTC, datetime
from typing import Self

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import Table

from apps.models.content import Document as DocumentModel
from apps.models.content import DocumentIndicator, DocumentInstrument
from modules.assessment import (
    DEFAULT_MAX_CONCURRENCY,
    DEFAULT_PERSPECTIVE,
    DOCUMENT_INDICATOR_UPSERT,
    DOCUMENT_INSTRUMENT_UPSERT,
    INSTRUMENT_CANDIDATES,
    PENDING_DOCUMENTS,
    PERSPECTIVES,
    PROMPT_VERSION,
    REPAIR_INSTRUCTION,
    UPDATE_ASSESSMENT,
    Assessment,
    AssessmentBatch,
    AssessmentError,
    AssessmentStore,
    Candidates,
    DocumentAssessor,
    IndicatorTag,
    LlmSettings,
    PendingDocument,
    filter_tags,
)
from modules.llm import LlmError, UnsupportedResponseFormat

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
    language="ko",
    published_at=datetime(2026, 8, 14, 22, 30, tzinfo=UTC),
    content_hash="abc",
)

VALID = """{"instruments": ["005930"],
 "indicators": [{"provider": "yahoo", "series_id": "USDKRW"}],
 "topics": ["fx"], "direction": "negative",
 "scores": {"relevance": 2, "novelty": 1, "specificity": 2, "impact": 1},
 "new_facts": ["환율이 1,400원을 넘었다"], "reason": "수출주 원가에 직접 영향"}"""


class ScriptedModel:
    """LangChain 모델 자리에 끼운다. 실제 호출은 하지 않는다."""

    def __init__(self, *replies: str | Exception) -> None:
        self.replies = list(replies)
        self.calls: list[list] = []
        self.schemas: list[dict | None] = []
        self._schema: dict | None = None

    def bind(self, **kwargs) -> Self:
        self._schema = kwargs.get("response_format")
        return self

    def bind_tools(self, tools) -> Self:
        return self

    def invoke(self, messages) -> AIMessage:
        self.calls.append(list(messages))
        self.schemas.append(self._schema)
        self._schema = None
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return AIMessage(reply)


class PickyModel(ScriptedModel):
    """스키마를 거절하는 제공처. 강제가 안 되면 프롬프트와 검증이 형식을 지킨다."""

    def invoke(self, messages) -> AIMessage:
        if self._schema is not None:
            self._schema = None
            raise UnsupportedResponseFormat("json_schema is not supported")
        return super().invoke(messages)


def assessor(
    *replies: str | Exception, model_class: type[ScriptedModel] = ScriptedModel
) -> tuple[DocumentAssessor, ScriptedModel]:
    scripted = model_class(*replies)
    return DocumentAssessor(scripted, settings()), scripted


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
    return LlmSettings(perspective=perspective)


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


def test_the_settings_hold_no_credentials():
    """접속 정보가 이 객체에 없으면 로그와 예외에 실릴 자리도 없다.

    키는 LangChain 클래스가 자기 환경변수에서 읽고, 모델은 `modules/llm.py`가 코드로 정한다.
    """
    assert set(LlmSettings.model_fields) == {"perspective", "max_concurrency"}


def test_the_concurrency_comes_from_the_environment(monkeypatch):
    monkeypatch.delenv("LLM_MAX_CONCURRENCY", raising=False)
    assert LlmSettings.from_environment().max_concurrency == DEFAULT_MAX_CONCURRENCY

    monkeypatch.setenv("LLM_MAX_CONCURRENCY", "1")
    assert LlmSettings.from_environment().max_concurrency == 1

    # 오타가 조용히 기본값으로 떨어지면 병렬로 돌 줄 알았던 배치가 순차로 돈다.
    monkeypatch.setenv("LLM_MAX_CONCURRENCY", "네개")
    with pytest.raises(AssessmentError, match="must be an integer"):
        LlmSettings.from_environment()

    monkeypatch.setenv("LLM_MAX_CONCURRENCY", "0")
    with pytest.raises(AssessmentError, match="at least 1"):
        LlmSettings.from_environment()


def test_prompt_lists_the_allowed_values():
    # 자유 문자열을 받으면 document_instrument가 instrument와 조인되지 않는다.
    messages = DocumentAssessor.build_messages(DOCUMENT, CANDIDATES)
    prompt = messages[-1].content

    assert "005930: 삼성전자" in prompt
    assert "yahoo:USDKRW" in prompt
    assert DOCUMENT.title in prompt


def test_the_prompt_never_carries_the_body():
    """평가는 제목과 요약만 본다.

    본문을 채우기 시작해도 이 판단은 안 바뀐다(2026-08-30 사용자 결정). 본문을 프롬프트에
    실으면 토큰이 수십 배가 되는데 판단이 좋아진다는 근거가 없고, 무엇보다 `content_hash`가
    본문을 안 보는 것과 짝이 어긋난다 — 해시가 안 바뀌는데 입력만 바뀌면 문서가 옛 본문으로
    평가된 채 영영 갱신되지 않는다.

    본문의 소비자는 검색이지 평가가 아니다.
    """
    assert "body" not in PendingDocument.model_fields
    assert "body" not in PENDING_DOCUMENTS

    prompt = DocumentAssessor.build_messages(DOCUMENT, CANDIDATES)[-1].content
    assert "본문" not in prompt


def test_relevance_may_be_zero_and_the_prompt_says_so():
    """실측(grok-4)에서 태그를 하나도 안 달고도 relevance를 1로 줬다.

    0을 안 쓰면 점수 바닥이 1로 올라가 0~8이 아니라 4~8이 된다. 상위 몇 건을 고르는 것이 이
    점수의 유일한 쓸모라 눌리면 못 쓴다.
    """
    prompt = DocumentAssessor.build_messages(DOCUMENT, CANDIDATES)[-1].content

    assert "0을 쓰는 것을 주저하지 마라" in prompt
    assert "둘 다 비었다면 relevance는 0" in prompt


def test_relevance_is_asked_as_a_path_not_as_a_direct_mention():
    """켜져 있는 피드 아홉 중 여섯이 비한국이다.

    "한국에 직접 관련되는가"로 물으면 한국을 언급하지 않는 연준 성명이 0점을 받는다. 그
    문서들을 모으는 이유가 전이 효과이므로 경로를 묻는다.
    """
    prompt = DocumentAssessor.build_messages(DOCUMENT, CANDIDATES)[-1].content

    assert "직접 언급을 요구하지 않는다" in prompt
    assert "경로" in prompt


def test_the_default_perspective_is_global():
    # 기본이 한국 전용이면 아카이브의 대부분이 잘못 채점된다.
    assert DEFAULT_PERSPECTIVE == "global"
    system = DocumentAssessor.build_messages(DOCUMENT, CANDIDATES)[0].content
    assert PERSPECTIVES["global"] in system


def test_the_perspective_changes_the_system_prompt():
    korea = DocumentAssessor.build_messages(DOCUMENT, CANDIDATES, "korea")[0].content
    united_states = DocumentAssessor.build_messages(DOCUMENT, CANDIDATES, "us")[0].content

    assert PERSPECTIVES["korea"] in korea
    assert PERSPECTIVES["us"] in united_states
    # 문서 부분은 관점과 무관하게 같아야 한다.
    assert (
        DocumentAssessor.build_messages(DOCUMENT, CANDIDATES, "korea")[-1]
        == DocumentAssessor.build_messages(DOCUMENT, CANDIDATES, "us")[-1]
    )


def test_an_unknown_perspective_is_rejected():
    with pytest.raises(AssessmentError, match="Unknown perspective"):
        DocumentAssessor.build_messages(DOCUMENT, CANDIDATES, "mars")


def test_the_prompt_revision_carries_the_perspective():
    """관점이 바뀌면 같은 문서라도 점수가 달라진다. 재평가 대상이 돼야 한다."""
    assert settings("global").prompt_revision == f"{PROMPT_VERSION}/global"
    assert settings("korea").prompt_revision != settings("global").prompt_revision


def test_the_perspective_comes_from_the_environment(monkeypatch):
    monkeypatch.delenv("LLM_PERSPECTIVE", raising=False)
    assert LlmSettings.from_environment().perspective == DEFAULT_PERSPECTIVE

    monkeypatch.setenv("LLM_PERSPECTIVE", "us")
    assert LlmSettings.from_environment().perspective == "us"

    monkeypatch.setenv("LLM_PERSPECTIVE", "mars")
    with pytest.raises(AssessmentError, match="LLM_PERSPECTIVE must be one of"):
        LlmSettings.from_environment()


def test_parse_accepts_a_reply_wrapped_in_a_code_fence():
    # 제3자 OpenAI 호환 제공자가 JSON만 내라는 지시를 안 지키는 경우가 있다.
    assessment = DocumentAssessor.parse(f"```json\n{VALID}\n```")

    assert assessment.direction == "negative"
    assert assessment.scores.total == 6


def test_parse_rejects_a_score_outside_the_range():
    with pytest.raises(AssessmentError, match="invalid assessment"):
        DocumentAssessor.parse(VALID.replace('"relevance": 2', '"relevance": 5'))


def test_parse_rejects_an_unknown_direction():
    with pytest.raises(AssessmentError, match="invalid assessment"):
        DocumentAssessor.parse(VALID.replace('"negative"', '"bullish"'))


def test_parse_rejects_a_reply_without_a_json_object():
    with pytest.raises(AssessmentError, match="did not return a JSON object"):
        DocumentAssessor.parse("판단할 수 없습니다.")


def test_assess_forces_the_output_schema():
    """검증만 하지 않고 강제한다. 제공처가 받으면 깨진 응답이 아예 오지 않는다."""
    assessing, model = assessor(VALID)

    assessing.assess(DOCUMENT, CANDIDATES)

    schema = model.schemas[0]
    assert schema["type"] == "json_schema"
    assert schema["json_schema"]["strict"] is True
    properties = schema["json_schema"]["schema"]["properties"]
    # direction은 검증기가 아니라 스키마의 enum이 막는다.
    assert properties["direction"]["enum"] == ["positive", "negative", "neutral"]


def test_assess_falls_back_when_the_provider_rejects_the_schema():
    assessing, model = assessor(VALID, model_class=PickyModel)

    assessment = assessing.assess(DOCUMENT, CANDIDATES)

    assert assessment.scores.total == 6
    # 스키마 없이 다시 부른 요청 하나만 실제로 돌았다.
    assert model.schemas == [None]


def test_assess_retries_once_when_the_format_is_broken():
    assessing, model = assessor("형식이 틀린 답", VALID)

    assessment = assessing.assess(DOCUMENT, CANDIDATES)

    assert assessment.scores.total == 6
    assert len(model.calls) == 2
    # 두 번째 요청에는 첫 응답과 교정 지시가 붙는다.
    assert len(model.calls[1]) == len(model.calls[0]) + 2
    assert REPAIR_INSTRUCTION == model.calls[1][-1].content


def test_assess_gives_up_after_the_second_failure():
    assessing, model = assessor("틀림", "또 틀림")

    with pytest.raises(AssessmentError):
        assessing.assess(DOCUMENT, CANDIDATES)
    assert len(model.calls) == 2


def test_batch_keeps_one_failure_from_spreading():
    """문서 하나가 실패해도 나머지는 계속 간다. 저장은 배치가 하지 않는다."""
    other = DOCUMENT.model_copy(update={"id": 12})
    assessing, _ = assessor("틀림", "또 틀림", VALID)

    results = AssessmentBatch(assessing, max_concurrency=1).run([DOCUMENT, other], CANDIDATES)

    by_id = {result.document_id: result for result in results}
    assert by_id[DOCUMENT.id].assessment is None
    assert by_id[DOCUMENT.id].error
    assert by_id[other.id].assessment is not None


@pytest.mark.parametrize(
    ("error", "retryable"),
    [(ConnectionError("network is down"), True), (LlmError("HTTP 401: invalid key"), False)],
)
def test_batch_records_provider_failures_without_losing_other_results(error, retryable):
    """LLM 오류도 결과로 모아 성공 문서를 먼저 저장할 수 있어야 한다."""
    other = DOCUMENT.model_copy(update={"id": 12})
    assessing, _ = assessor(error, VALID)

    results = AssessmentBatch(assessing, max_concurrency=1).run([DOCUMENT, other], CANDIDATES)

    by_id = {result.document_id: result for result in results}
    assert by_id[DOCUMENT.id].assessment is None
    assert by_id[DOCUMENT.id].retryable is retryable
    assert by_id[other.id].assessment is not None


def test_batch_still_collects_every_document_before_retry_is_decided():
    """일시 오류가 있어도 전체 결과를 모아 성공 문서 저장 기회를 남긴다."""
    documents = [DOCUMENT.model_copy(update={"id": number}) for number in (31, 32, 33)]
    assessing, model = assessor(*([ConnectionError("HTTP 520: origin unavailable")] * len(documents)))

    results = AssessmentBatch(assessing, max_concurrency=1).run(documents, CANDIDATES)

    assert len(results) == len(documents)
    assert len(model.calls) == len(documents)
    assert all(result.retryable for result in results)


def test_batch_returns_one_result_per_document():
    documents = [DOCUMENT.model_copy(update={"id": number}) for number in (21, 22, 23)]
    assessing, _ = assessor(*([VALID] * 3))

    results = AssessmentBatch(assessing, max_concurrency=1).run(documents, CANDIDATES)

    assert {result.document_id for result in results} == {21, 22, 23}


def test_batch_does_not_call_the_model_for_an_empty_batch():
    assessing, model = assessor()

    assert AssessmentBatch(assessing).run([], CANDIDATES) == ()
    assert model.calls == []


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


def test_tags_copied_from_the_candidate_display_are_recovered():
    """모델이 후보 표시 줄을 그대로 복사해도 태그를 잃지 않는다.

    gpt-5.6-luna 실측(2026-08-20): `000660: SK하이닉스`를 instruments에,
    `yahoo:USDKRW`를 series_id에 그대로 넣었다. 유일하게 복원되는 두 모양만 복원한다.
    """
    assessment = Assessment.model_validate_json(VALID).model_copy(
        update={
            "instruments": ("000660: SK하이닉스", "005930: 삼성전자"),
            "indicators": (IndicatorTag(provider="yahoo", series_id="yahoo:USDKRW"),),
        }
    )

    instruments, indicators = filter_tags(assessment, CANDIDATES, DOCUMENT.id)

    assert instruments == ("000660", "005930")
    assert indicators == (IndicatorTag(provider="yahoo", series_id="USDKRW"),)


INDEX_CANDIDATES = Candidates(
    instruments=(("005930", "삼성전자"),),
    indicators=(("kis", "KOSPI", "코스피"), ("yahoo", "USDKRW", "원/달러")),
)


def test_an_index_in_the_instruments_slot_moves_to_indicators():
    """지수를 종목 칸에 넣는 실수(gpt-5.6-luna 실측 2026-08-20). 지표 후보에서
    provider가 하나뿐이면 그쪽으로 옮긴다."""
    assessment = Assessment.model_validate_json(VALID).model_copy(
        update={"instruments": ("KOSPI",), "indicators": ()}
    )

    instruments, indicators = filter_tags(assessment, INDEX_CANDIDATES, DOCUMENT.id)

    assert instruments == ()
    assert indicators == (IndicatorTag(provider="kis", series_id="KOSPI"),)


def test_a_made_up_provider_is_corrected_when_the_series_is_unique():
    """provider를 지어내는 실수(yahoo:KOSPI). series_id의 후보 provider가 하나뿐이면 교정한다."""
    assessment = Assessment.model_validate_json(VALID).model_copy(
        update={"instruments": (), "indicators": (IndicatorTag(provider="yahoo", series_id="KOSPI"),)}
    )

    instruments, indicators = filter_tags(assessment, INDEX_CANDIDATES, DOCUMENT.id)

    assert instruments == ()
    assert indicators == (IndicatorTag(provider="kis", series_id="KOSPI"),)


def test_an_ambiguous_series_is_not_guessed():
    """같은 series_id가 두 제공처에 있으면 복원이 유일하지 않다. 지어내지 않고 버린다."""
    ambiguous = Candidates(
        instruments=(("005930", "삼성전자"),),
        indicators=(("kis", "USDKRW", "실시간"), ("yahoo", "USDKRW", "장외")),
    )
    assessment = Assessment.model_validate_json(VALID).model_copy(
        update={
            "instruments": ("USDKRW",),
            "indicators": (IndicatorTag(provider="fred", series_id="USDKRW"),),
        }
    )

    instruments, indicators = filter_tags(assessment, ambiguous, DOCUMENT.id)

    assert instruments == ()
    assert indicators == ()


def test_recovered_duplicates_collapse_to_one_tag():
    """`005930`과 `005930: 삼성전자`가 함께 오면 하나만 남는다.

    같은 키가 한 배치에 두 번 들어가면 upsert가 죽는다.
    """
    assessment = Assessment.model_validate_json(VALID).model_copy(
        update={
            "instruments": ("005930", "005930: 삼성전자", "999999: 없는것"),
            "indicators": (
                IndicatorTag(provider="yahoo", series_id="USDKRW"),
                IndicatorTag(provider="yahoo", series_id="yahoo:USDKRW"),
            ),
        }
    )

    instruments, indicators = filter_tags(assessment, CANDIDATES, DOCUMENT.id)

    assert instruments == ("005930",)
    assert indicators == (IndicatorTag(provider="yahoo", series_id="USDKRW"),)


def test_pending_query_covers_the_three_reasons_to_assess():
    # 한 번도 평가하지 않은 문서, 본문이 바뀐 문서, 프롬프트 버전이 오른 문서.
    assert "assessed_at IS NULL" in PENDING_DOCUMENTS
    assert "assessed_content_hash IS DISTINCT FROM content_hash" in PENDING_DOCUMENTS
    assert "prompt_version IS DISTINCT FROM %s" in PENDING_DOCUMENTS


def test_pending_query_reserves_batch_slots_for_reassessment():
    # 신규 유입이 batch_size에 가까우면 최신순 하나로는 재평가 대상이 영영 차례가 안 온다.
    # 신규·재평가를 따로 순위 매겨 번갈아 집는다. 파라미터는 둘 그대로다.
    assert "PARTITION BY assessed_at IS NULL" in PENDING_DOCUMENTS
    assert re.search(r"ORDER BY rank\b", PENDING_DOCUMENTS)
    assert PENDING_DOCUMENTS.count("%s") == 2


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


def test_candidates_read_the_whole_instrument_master():
    """태그 후보는 `is_watched`를 안 본다. 시세를 안 받는 종목도 문서에서는 알아봐야 한다."""
    body = "\n".join(line for line in INSTRUMENT_CANDIDATES.splitlines() if not line.startswith("--"))
    assert "FROM instrument" in body
    assert "is_watched" not in body
    assert "WHERE" not in body


def test_an_empty_candidate_master_fails_the_task():
    """후보가 0행이면 모델이 아무 태그도 못 붙이고, 프롬프트 규칙("태그가 둘 다 비면
    relevance 0")이 걸려 아카이브 전체가 0점을 받는다. 그런데 태스크는 초록으로 끝난다.
    후보 목록이 런타임 데이터라 마이그레이션 미적용·SQL 오타가 그 상태를 만든다.
    """
    connection = FakeConnection()

    with pytest.raises(AssessmentError):
        AssessmentStore(connection, settings().prompt_revision).candidates()


def test_store_writes_the_assessment_and_both_tag_kinds():
    connection = FakeConnection()
    assessment = DocumentAssessor.parse(VALID)
    instruments, indicators = filter_tags(assessment, CANDIDATES, DOCUMENT.id)

    AssessmentStore(connection, settings().prompt_revision).store(
        DOCUMENT,
        assessment,
        instruments,
        indicators,
        "test-model",
        ASSESSED_AT,
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
    assessment = DocumentAssessor.parse(VALID)

    AssessmentStore(connection, settings().prompt_revision).store(
        DOCUMENT, assessment, (), (), "test-model", ASSESSED_AT
    )

    statements = [statement for statement, _ in connection.recorded_cursor.calls]
    assert not any("INSERT INTO document_instrument" in statement for statement in statements)
    assert any("UPDATE document" in statement for statement in statements)
