"""이벤트 기대치 추출과 서프라이즈 판정.

순수 함수(기간·단위·집계·분류)는 경계값을, 추출은 검증이 무엇을 버리는지를, 판정은
"조용히 틀리지 않는지"를 본다. 실 DB를 쓰지 않는다 — 판정 수식이 SQL이 아니라 Python에
있는 이유가 이것이다.
"""

import json
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Self

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import Table

from apps.models.analysis import (
    EVENT_METRICS as MODEL_EVENT_METRICS,
)
from apps.models.analysis import (
    PERIOD_KEY_PATTERN as MODEL_PERIOD_KEY_PATTERN,
)
from apps.models.analysis import (
    StockEventClaim,
    StockEventClaimKind,
    StockEventOutcome,
    StockEventType,
    SurpriseVerdict,
)
from apps.models.market import EarningsMetric
from modules.expectation import (
    CLAIM_INSERT,
    CLAIM_KINDS,
    EVENT_METRICS,
    EVENT_TYPES,
    EXTRACTION_UPSERT,
    MEET_BAND_PCT,
    OUTCOME_INSERT,
    PENDING_EXTRACTION,
    PROMPT_VERSION,
    ClaimRow,
    EarningsFactRow,
    ExpectationExtractor,
    ExpectationStore,
    ExtractionError,
    ExtractionResponse,
    PendingExtractionDocument,
    aggregate_expectations,
    amount_basis_for,
    classify_surprise,
    filter_claims,
    format_krw,
    normalize_amount,
    period_end_for,
    render_blocks,
    render_text,
    resolve_actual,
    resolve_earnings_actual,
)
from modules.expectation import (
    PERIOD_KEY_PATTERN as MODULE_PERIOD_KEY_PATTERN,
)
from modules.llm import UnsupportedResponseFormat

EXTRACTED_AT = datetime(2026, 8, 22, 9, 45, tzinfo=UTC)
ANNOUNCED_AT = datetime(2026, 8, 22, 8, 0, tzinfo=UTC)

DOCUMENT = PendingExtractionDocument(
    id=11,
    source_slug="naver_research_company",
    title="삼성전자: 주주환원 확대 기대 - 대신증권",
    summary="2026년 총 주주환원 9.5조원 전망.",
    body=None,
    published_at=datetime(2026, 8, 1, 0, 0, tzinfo=UTC),
    detected_at=datetime(2026, 8, 1, 1, 0, tzinfo=UTC),
    content_hash="abc",
    tickers=("005930",),
)

VALID = """{"claims": [{"stock_code": "005930", "event_type": "shareholder_return",
 "period_key": "2026", "metric": "total_return_amount", "kind": "expectation",
 "value": "9.5", "unit": "조원", "value_low": null, "value_high": null, "broker": "대신증권"}]}"""


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


class FakeCursor:
    """SQL 종류로 답을 고르는 커서. `RETURNING`은 `fetchone`이 받는다."""

    def __init__(self, rows: dict[str, list[tuple]] | None = None) -> None:
        self.rows = rows or {}
        self.calls: list[tuple[str, Any]] = []
        self._pending: list[tuple] = []
        self._returning: tuple | None = None
        self.blocked_inserts: set[tuple] = set()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, statement: str, parameters: Any = ()) -> None:
        self.calls.append((statement, parameters))
        key = _statement_key(statement)
        if key == "outcome_insert":
            natural = tuple(parameters[:4])
            self._returning = None if natural in self.blocked_inserts else (1,)
            self._pending = []
            return
        self._returning = None
        self._pending = list(self.rows.get(key, []))

    def executemany(self, statement: str, parameters) -> None:
        self.calls.extend((statement, tuple(row)) for row in parameters)

    def fetchall(self) -> list:
        return self._pending

    def fetchone(self):
        return self._returning


class FakeConnection:
    def __init__(self, rows: dict[str, list[tuple]] | None = None) -> None:
        self.recorded_cursor = FakeCursor(rows)

    def cursor(self) -> FakeCursor:
        return self.recorded_cursor


def _statement_key(statement: str) -> str:
    query = re.sub(r"--[^\n]*", "", statement).strip()
    if query.startswith("INSERT INTO stock_event_outcome"):
        return "outcome_insert"
    if query.startswith("INSERT INTO stock_event_claim"):
        return "claim_insert"
    if query.startswith("INSERT INTO stock_event_extraction"):
        return "extraction_upsert"
    if "FROM earnings_fact" in query:
        return "earnings_actual"
    if "c.event_type <> 'earnings'" in query:
        return "pending_judgment"
    if "FROM stock_event_claim" in query:
        return "pending_earnings"
    if "FROM document" in query:
        return "pending_documents"
    return "other"


def inserted_columns(statement: str) -> tuple[str, ...]:
    columns = re.search(r"INSERT INTO \w+ \(([^)]+)\)", statement, re.DOTALL)
    assert columns is not None
    return tuple(name.strip() for name in re.sub(r"--[^\n]*", "", columns.group(1)).split(",") if name.strip())


def model_columns(table: Table) -> set[str]:
    return {column.name for column in table.columns}


def extractor(*replies: str | Exception, model_class: type[ScriptedModel] = ScriptedModel):
    scripted = model_class(*replies)
    return ExpectationExtractor(scripted), scripted


def claim(
    *,
    kind: str = "expectation",
    value: str = "9500000000000",
    stated_at: datetime | None = None,
    broker: str | None = "대신증권",
    source_record_id: int | None = None,
    document_id: int | None = 11,
    metric: str = "total_return_amount",
    event_type: str = "shareholder_return",
) -> ClaimRow:
    return ClaimRow(
        stock_code="005930",
        event_type=event_type,
        period_key="2026",
        metric=metric,
        claim_kind=kind,
        value=Decimal(value),
        stated_at=stated_at or datetime(2026, 8, 1, tzinfo=UTC),
        broker=broker,
        document_id=document_id,
        source_record_id=source_record_id,
    )


# --- 도메인 상수는 백엔드와 대조한다 -------------------------------------------


def test_the_event_constants_match_the_backend_models():
    """`airflow/`는 `apps/`를 import하지 못한다. 중복은 허용하되 어긋나면 여기서 잡는다."""
    assert set(EVENT_TYPES) == {member.value for member in StockEventType}
    assert set(CLAIM_KINDS) == {member.value for member in StockEventClaimKind}
    assert {key: set(value) for key, value in EVENT_METRICS.items()} == {
        event.value: {metric.value for metric in metrics} for event, metrics in MODEL_EVENT_METRICS.items()
    }
    assert MODULE_PERIOD_KEY_PATTERN.pattern == MODEL_PERIOD_KEY_PATTERN


def test_the_earnings_metrics_match_the_earnings_fact_table():
    """판정이 `earnings_fact`를 대응표 없이 조인한다. 값이 갈리면 그 조인이 조용히 0행이 된다."""
    assert set(EVENT_METRICS["earnings"]) == {member.value for member in EarningsMetric}


def test_the_verdict_values_match_the_backend_enum():
    from modules.expectation import VERDICT_LABELS

    assert set(VERDICT_LABELS) == {member.value for member in SurpriseVerdict}


# --- 순수 함수: 기간과 단위 ----------------------------------------------------


def test_the_period_key_maps_to_the_accounting_period_end():
    assert period_end_for("2026") == date(2026, 12, 31)
    assert period_end_for("2026Q1") == date(2026, 3, 31)
    assert period_end_for("2026Q2") == date(2026, 6, 30)
    assert period_end_for("2026Q3") == date(2026, 9, 30)
    assert period_end_for("2026Q4") == date(2026, 12, 31)
    assert period_end_for("2026H1") == date(2026, 6, 30)
    assert period_end_for("2026H2") == date(2026, 12, 31)


def test_a_loose_period_key_is_rejected_not_guessed():
    """느슨하게 받으면 기대와 실제가 다른 표기로 저장돼 조용히 매칭이 깨진다."""
    for value in ("2026-06", "26Q2", "2026Q5", "2026H3", "FY2026", ""):
        with pytest.raises(ValueError, match="period_key"):
            period_end_for(value)


def test_the_amount_basis_follows_the_period_length():
    # 분기·반기는 그 기간 금액, 연간은 사업연도 누계다. 섞으면 자릿수가 어긋난다.
    assert amount_basis_for("2026") == "cumulative"
    assert amount_basis_for("2026Q2") == "period"
    assert amount_basis_for("2026H1") == "period"


def test_amounts_are_normalized_to_won():
    assert normalize_amount("9.5", "조원") == Decimal("9500000000000.00")
    assert normalize_amount("9.5", "조") == Decimal("9500000000000.00")
    assert normalize_amount("1,416", "원") == Decimal("1416.00")
    assert normalize_amount("3,200", "억원") == Decimal("320000000000.00")


def test_an_unknown_unit_drops_the_claim_instead_of_guessing():
    """조용히 엉뚱한 자릿수로 저장하는 것보다 그 주장을 버리는 편이 낫다."""
    assert normalize_amount("9.5", "trillion") is None
    assert normalize_amount("9.5", "") is None
    assert normalize_amount("아홉", "조원") is None


# --- 순수 함수: 집계와 분류 ----------------------------------------------------


def test_expectations_stated_after_the_announcement_are_excluded():
    """발표 뒤 "기대치는 X였다"라고 회고한 기사가 기대로 섞이면 판정이 오염된다."""
    rows = [
        claim(value="9500000000000", stated_at=datetime(2026, 8, 1, tzinfo=UTC)),
        claim(value="8000000000000", stated_at=datetime(2026, 8, 23, tzinfo=UTC), broker="회고"),
    ]

    aggregated = aggregate_expectations(rows, ANNOUNCED_AT)

    assert aggregated == (Decimal(9500000000000), 1)


def test_a_consensus_row_wins_over_report_excerpts():
    """정형 집계가 개별 리포트 발췌보다 정확하다."""
    rows = [
        claim(value="9500000000000", broker="대신증권"),
        claim(
            value="9000000000000",
            broker=None,
            document_id=None,
            source_record_id=5,
            stated_at=datetime(2026, 8, 15, tzinfo=UTC),
        ),
    ]

    expected, count = aggregate_expectations(rows, ANNOUNCED_AT)

    assert expected == Decimal(9000000000000)
    assert count == 2


def test_only_the_latest_expectation_per_broker_counts():
    """같은 증권사가 기대를 올려 잡으면 옛 값이 중앙값을 끌면 안 된다."""
    rows = [
        claim(value="8000000000000", broker="대신증권", stated_at=datetime(2026, 7, 1, tzinfo=UTC)),
        claim(value="10000000000000", broker="대신증권", stated_at=datetime(2026, 8, 1, tzinfo=UTC)),
        claim(value="10000000000000", broker="키움", stated_at=datetime(2026, 8, 2, tzinfo=UTC)),
    ]

    expected, count = aggregate_expectations(rows, ANNOUNCED_AT)

    assert expected == Decimal(10000000000000)
    # 대조한 행 수는 버린 것까지 센다 — 몇 건을 보고 낸 값인지가 판정 행에 남는다.
    assert count == 3


def test_no_expectation_means_no_judgment():
    """기대가 없던 발표는 그것대로 사실이다. 억지 판정이 더 나쁘다."""
    assert aggregate_expectations([claim(kind="actual")], ANNOUNCED_AT) is None


def test_the_meet_band_is_inclusive_at_the_boundary():
    assert classify_surprise(Decimal(100), Decimal(105)) == (Decimal("5.0000"), "meet")
    assert classify_surprise(Decimal(100), Decimal(95)) == (Decimal("-5.0000"), "meet")
    assert classify_surprise(Decimal(100), Decimal("105.01"))[1] == "beat"
    assert classify_surprise(Decimal(100), Decimal("94.99"))[1] == "miss"


def test_the_samsung_case_lands_on_miss():
    """8/22 사례. 기대 9.5조 대비 발표 8조는 -15.8퍼센트로 미달이다."""
    surprise, verdict = classify_surprise(Decimal(9500000000000), Decimal(8000000000000))

    assert verdict == "miss"
    assert surprise == Decimal("-15.7895")


def test_a_zero_expectation_is_not_forced_into_a_ratio():
    assert classify_surprise(Decimal(0), Decimal(100)) is None
    # 둘 다 0이면 정확히 부합이다.
    assert classify_surprise(Decimal(0), Decimal(0)) == (Decimal("0.0000"), "meet")


def test_the_meet_band_is_a_named_constant_not_a_literal():
    """실측이 아니라 시작값이다. 판정 분포가 쌓이면 이 상수를 다시 정한다."""
    assert MEET_BAND_PCT == Decimal("5.0")


# --- 순수 함수: 실제값 고르기 --------------------------------------------------


def test_conflicting_actual_values_hold_the_judgment():
    """ "총 환원 8조"와 "배당+자사주 8.5조"처럼 집계 범위가 다른 숫자가 실제로 온다."""
    rows = [claim(kind="actual", value="8000000000000"), claim(kind="actual", value="8500000000000")]

    assert resolve_actual(rows) is None


def test_the_earliest_actual_claim_sets_the_announcement_time():
    """가장 이른 보도가 발표 시각이다. 늦은 기사를 쓰면 그 사이 회고가 기대로 샌다."""
    rows = [
        claim(kind="actual", value="8000000000000", stated_at=datetime(2026, 8, 22, 12, 0, tzinfo=UTC), document_id=9),
        claim(kind="actual", value="8000000000000", stated_at=ANNOUNCED_AT, document_id=7),
    ]

    value, announced_at, ref = resolve_actual(rows)

    assert value == Decimal(8000000000000)
    assert announced_at == ANNOUNCED_AT
    assert ref == "document:7"


def test_consolidated_statements_win_and_corrections_are_the_latest_receipt():
    """`EarningsFact` docstring의 조회 규칙 그대로다."""
    rows = [
        EarningsFactRow(
            id=3,
            statement_scope="CFS",
            amount_basis="period",
            release_type="provisional",
            rcept_no="20260810000002",
            current_amount=Decimal(12000000000000),
            created_at=ANNOUNCED_AT,
        ),
        EarningsFactRow(
            id=2,
            statement_scope="CFS",
            amount_basis="period",
            release_type="provisional",
            rcept_no="20260801000001",
            current_amount=Decimal(11000000000000),
            created_at=ANNOUNCED_AT,
        ),
        EarningsFactRow(
            id=1,
            statement_scope="OFS",
            amount_basis="period",
            release_type="provisional",
            rcept_no="20260810000003",
            current_amount=Decimal(9000000000000),
            created_at=ANNOUNCED_AT,
        ),
    ]

    value, _, ref = resolve_earnings_actual(rows, "2026Q2")

    assert value == Decimal(12000000000000)
    assert ref == "earnings_fact:3"


def test_the_wrong_amount_basis_is_not_used_as_the_actual():
    """분기 기대에 사업연도 누계를 맞대면 서프라이즈가 자릿수로 나온다."""
    rows = [
        EarningsFactRow(
            id=1,
            statement_scope="CFS",
            amount_basis="cumulative",
            release_type="periodic",
            rcept_no="20260810000001",
            current_amount=Decimal(30000000000000),
            created_at=ANNOUNCED_AT,
        )
    ]

    assert resolve_earnings_actual(rows, "2026Q2") is None
    assert resolve_earnings_actual(rows, "2026")[0] == Decimal(30000000000000)


# --- 추출 ---------------------------------------------------------------------


def test_the_extractor_returns_claims_and_forces_the_schema():
    ext, model = extractor(VALID)

    response = ext.extract(DOCUMENT)

    (item,) = response.claims
    assert item.metric == "total_return_amount"
    assert item.kind == "expectation"
    assert model.schemas[0] is not None


def test_a_provider_without_schema_support_falls_back_to_validation():
    ext, _ = extractor(VALID, model_class=PickyModel)

    assert ext.extract(DOCUMENT).claims


def test_a_broken_response_is_repaired_once_then_raises():
    ext, _ = extractor("설명입니다", "여전히 아닙니다")

    with pytest.raises(ExtractionError):
        ext.extract(DOCUMENT)


def test_the_prompt_carries_only_the_document_tags_as_candidates():
    """전체 마스터가 아니라 그 문서의 태그다. 후보를 넓히면 남의 종목 주장이 들어온다."""
    messages = ExpectationExtractor.build_messages(DOCUMENT)

    prompt = messages[-1].content
    assert "005930" in prompt
    assert "000660" not in prompt


def test_an_empty_claim_list_is_a_valid_answer():
    """대부분 문서에는 이벤트 주장이 없다. 그것이 오류가 되면 매시간 재시도가 돈다."""
    ext, _ = extractor('{"claims": []}')

    assert ext.extract(DOCUMENT).claims == ()


# --- 추출 검증 ----------------------------------------------------------------


def response_with(**overrides: Any) -> ExtractionResponse:
    payload = {
        "stock_code": "005930",
        "event_type": "shareholder_return",
        "period_key": "2026",
        "metric": "total_return_amount",
        "kind": "expectation",
        "value": "9.5",
        "unit": "조원",
        "broker": "대신증권",
    }
    payload.update(overrides)
    return ExtractionResponse.model_validate({"claims": [payload]})


def test_a_valid_claim_is_normalized_to_won():
    (kept,) = filter_claims(response_with(), DOCUMENT)

    assert kept.value == Decimal("9500000000000.00")
    assert kept.broker == "대신증권"
    assert kept.value_low is None


def test_a_claim_about_a_stock_outside_the_document_tags_is_dropped():
    assert filter_claims(response_with(stock_code="000660"), DOCUMENT) == ()


def test_a_metric_that_does_not_belong_to_the_event_is_dropped():
    """`guidance`에는 순이익 지표가 없다. 조합을 안 보면 이벤트 키가 조용히 뒤섞인다."""
    assert filter_claims(response_with(event_type="guidance", metric="net_income"), DOCUMENT) == ()


def test_an_invalid_period_key_is_dropped():
    assert filter_claims(response_with(period_key="2026-06"), DOCUMENT) == ()


def test_an_earnings_actual_is_dropped_because_the_parser_owns_it():
    """DART 파서가 원문 표에서 정확히 읽는 값을 기사 산문에서 다시 뽑으면 어긋난 쪽을 못 고른다."""
    dropped = response_with(event_type="earnings", metric="revenue", kind="actual")
    assert filter_claims(dropped, DOCUMENT) == ()
    # 기대는 그대로 받는다.
    kept = response_with(event_type="earnings", metric="revenue", kind="expectation")
    assert len(filter_claims(kept, DOCUMENT)) == 1


def test_an_unparseable_amount_is_dropped():
    assert filter_claims(response_with(unit="trillion"), DOCUMENT) == ()


def test_a_range_claim_keeps_both_bounds_in_won():
    (kept,) = filter_claims(response_with(value="9.5", value_low="9", value_high="10"), DOCUMENT)

    assert kept.value_low == Decimal("9000000000000.00")
    assert kept.value_high == Decimal("10000000000000.00")


def test_an_inverted_range_is_dropped():
    assert filter_claims(response_with(value_low="10", value_high="9"), DOCUMENT) == ()


def test_duplicate_claims_keep_the_first():
    """같은 키가 둘이면 저장 INSERT가 한 배치에서 같은 키를 두 번 만난다."""
    payload = {
        "stock_code": "005930",
        "event_type": "shareholder_return",
        "period_key": "2026",
        "metric": "total_return_amount",
        "kind": "expectation",
        "unit": "조원",
    }
    response = ExtractionResponse.model_validate({"claims": [{**payload, "value": "9.5"}, {**payload, "value": "8.0"}]})

    (kept,) = filter_claims(response, DOCUMENT)

    assert kept.value == Decimal("9500000000000.00")


# --- 저장 ---------------------------------------------------------------------


def test_a_document_with_no_claims_still_lands_in_the_ledger():
    """ "뽑았는데 없었다"와 "아직 안 뽑았다"가 구분돼야 매시간 같은 문서를 다시 뽑지 않는다."""
    connection = FakeConnection()

    ExpectationStore(connection).store_extraction(DOCUMENT, (), "gpt-5.6-luna", EXTRACTED_AT)

    (statement, parameters) = connection.recorded_cursor.calls[-1]
    assert _statement_key(statement) == "extraction_upsert"
    assert parameters[-1] == 0
    assert parameters[1] == DOCUMENT.content_hash


def test_the_claim_time_comes_from_the_document_not_the_model():
    """모델에게 시각을 만들게 하지 않는다. 발행 시각이 없으면 감지 시각이다."""
    connection = FakeConnection()
    claims = filter_claims(response_with(), DOCUMENT)

    ExpectationStore(connection).store_extraction(DOCUMENT, claims, "gpt-5.6-luna", EXTRACTED_AT)

    (_, parameters) = connection.recorded_cursor.calls[0]
    assert parameters[8] == DOCUMENT.published_at

    undated = DOCUMENT.model_copy(update={"published_at": None})
    assert undated.stated_at == DOCUMENT.detected_at


def test_a_stored_claim_carries_the_document_and_never_a_source_record():
    connection = FakeConnection()

    ExpectationStore(connection).store_extraction(DOCUMENT, filter_claims(response_with(), DOCUMENT), "m", EXTRACTED_AT)

    (_, parameters) = connection.recorded_cursor.calls[0]
    assert parameters[10] == DOCUMENT.id
    # 컨센서스 수집만 source_record를 채운다. CHECK가 둘 중 하나만 차는 것을 강제한다.
    assert parameters[11] is None


# --- SQL과 모델 대조 -----------------------------------------------------------


def test_the_insert_columns_exist_on_the_models():
    for statement, table in (
        (CLAIM_INSERT, StockEventClaim.__table__),
        (OUTCOME_INSERT, StockEventOutcome.__table__),
    ):
        assert set(inserted_columns(statement)) <= model_columns(table)


def test_the_outcome_insert_never_overwrites_a_judgment():
    """첫 성공본 불변. 덮어쓰면 Slack으로 이미 나간 판정과 DB가 어긋난다."""
    query = re.sub(r"--[^\n]*", "", OUTCOME_INSERT)

    assert "ON CONFLICT (stock_code, event_type, period_key, metric) DO NOTHING" in query
    assert "DO UPDATE" not in query
    # 이번 실행이 새로 쓴 것만 발송하려면 RETURNING이 필요하다.
    assert "RETURNING id" in query


def test_the_claim_insert_does_not_overwrite_an_earlier_claim():
    query = re.sub(r"--[^\n]*", "", CLAIM_INSERT)

    assert "DO NOTHING" in query
    assert "DO UPDATE" not in query


def test_the_extraction_ledger_is_updated_when_the_body_changes():
    """원장은 갱신된다 — 본문이 바뀌면 그 문서를 다시 뽑은 사실이 최신으로 남아야 한다."""
    query = re.sub(r"--[^\n]*", "", EXTRACTION_UPSERT)

    assert "ON CONFLICT (document_id) DO UPDATE" in query


def test_the_pending_query_needs_an_assessment_and_a_ticker_tag():
    """종목 태그는 평가가 만든다. 평가 전 문서를 뽑으면 후보 목록이 비어 주장이 다 버려진다."""
    query = re.sub(r"--[^\n]*", "", PENDING_EXTRACTION)

    assert "assessed_at IS NOT NULL" in query
    assert "FROM document_instrument" in query
    # 대표에 연결된 중복은 대표가 뽑는다.
    assert "canonical_document_id IS NULL" in query
    # 프롬프트 판이 오르면 재추출 대상이다.
    assert "prompt_version = %s" in query


# --- 판정 흐름 ----------------------------------------------------------------


def judgment_row(kind: str, value: str, stated_at: datetime, broker: str | None = "대신증권") -> tuple:
    return (
        "005930",
        "shareholder_return",
        "2026",
        "total_return_amount",
        kind,
        Decimal(value),
        stated_at,
        broker,
        7,
        None,
    )


def test_judging_writes_one_row_and_reports_it_for_slack():
    connection = FakeConnection(
        {
            "pending_judgment": [
                judgment_row("expectation", "9500000000000", datetime(2026, 8, 1, tzinfo=UTC)),
                judgment_row("expectation", "9500000000000", datetime(2026, 8, 2, tzinfo=UTC), "키움"),
                judgment_row("actual", "8000000000000", ANNOUNCED_AT, None),
            ]
        }
    )

    (judged,) = ExpectationStore(connection).judge("manual__2026-08-22")

    assert judged.verdict == "miss"
    assert judged.expected_value == Decimal(9500000000000)
    assert judged.expectation_count == 2
    assert judged.announced_at == ANNOUNCED_AT


def test_a_judgment_written_by_another_run_is_not_sent_again():
    """동시 실행이 먼저 썼다. `RETURNING` 0행이면 이번 실행의 발송 대상이 아니다."""
    connection = FakeConnection(
        {
            "pending_judgment": [
                judgment_row("expectation", "9500000000000", datetime(2026, 8, 1, tzinfo=UTC)),
                judgment_row("actual", "8000000000000", ANNOUNCED_AT, None),
            ]
        }
    )
    connection.recorded_cursor.blocked_inserts.add(("005930", "shareholder_return", "2026", "total_return_amount"))

    assert ExpectationStore(connection).judge("run") == ()


def test_an_event_without_pre_announcement_expectations_is_skipped():
    connection = FakeConnection(
        {
            "pending_judgment": [
                judgment_row("expectation", "9500000000000", datetime(2026, 8, 23, tzinfo=UTC)),
                judgment_row("actual", "8000000000000", ANNOUNCED_AT, None),
            ]
        }
    )

    assert ExpectationStore(connection).judge("run") == ()
    # 판정 행을 쓰지 않았다. 다음 실행이 다시 본다.
    assert not [call for call in connection.recorded_cursor.calls if _statement_key(call[0]) == "outcome_insert"]


def test_earnings_are_judged_against_the_parsed_disclosure_not_a_claim():
    connection = FakeConnection(
        {
            "pending_earnings": [
                (
                    "005930",
                    "earnings",
                    "2026Q2",
                    "operating_profit",
                    "expectation",
                    Decimal(10000000000000),
                    datetime(2026, 7, 1, tzinfo=UTC),
                    "대신증권",
                    7,
                    None,
                )
            ],
            "earnings_actual": [
                ("3", "CFS", "period", "provisional", "20260810000002", Decimal(12000000000000), ANNOUNCED_AT)
            ],
        }
    )

    (judged,) = ExpectationStore(connection).judge("run")

    assert judged.verdict == "beat"
    assert judged.actual_value == Decimal(12000000000000)
    # 조회에 넘어간 기간 종료일이 회계 기간 규칙을 따른다.
    (_, parameters) = next(
        call for call in connection.recorded_cursor.calls if _statement_key(call[0]) == "earnings_actual"
    )
    assert parameters[1] == date(2026, 6, 30)


# --- Slack 렌더링 --------------------------------------------------------------


def test_the_slack_line_shows_both_sides_and_the_verdict():
    connection = FakeConnection(
        {
            "pending_judgment": [
                judgment_row("expectation", "9500000000000", datetime(2026, 8, 1, tzinfo=UTC)),
                judgment_row("actual", "8000000000000", ANNOUNCED_AT, None),
            ]
        }
    )
    judged = ExpectationStore(connection).judge("run")

    text = render_text(judged)
    blocks = render_blocks(judged)

    assert "005930" in text
    assert "주주환원" in text
    assert "총 환원액" in text
    assert "▼ 미달" in text
    assert "9.50조" in text and "8.00조" in text
    assert blocks[0]["type"] == "header"
    assert json.dumps(blocks, ensure_ascii=False).count('"section"') == 1


def test_amounts_are_shown_in_units_people_read():
    assert format_krw(Decimal(9500000000000)) == "9.50조"
    assert format_krw(Decimal(320000000000)) == "3,200억"
    assert format_krw(Decimal(1416)) == "1,416원"


def test_the_prompt_version_is_a_plain_string_that_can_be_raised():
    assert PROMPT_VERSION == "1"
