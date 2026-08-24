import json
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Self

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from sqlalchemy import Table

from apps.models.analysis import Thesis, ThesisEvidence, ThesisOutcome, ThesisPrecedent
from modules.sql import read_sql
from modules.technical import TECHNICAL_LOOKBACK_BARS
from modules.thesis import (
    DART_VIEWER_URL,
    FLAT_THRESHOLD_PCT,
    HORIZON_DAYS,
    MAX_ITEM_DETAIL_CHARS,
    MAX_MECHANISM_CHARS,
    MAX_NARRATIVE_CHARS,
    MAX_OPINION_REASON_CHARS,
    MAX_REASONING_CHARS,
    MAX_TOOL_CALLS,
    MAX_TOOL_RESULT_CHARS,
    MAX_TOOL_RESULTS,
    MAX_TOOL_ROUNDS,
    MAX_WINDOW_HOURS,
    NARRATED_HORIZON_DAYS,
    PREFETCHED_PAST_THESES,
    PROMPT_VERSION,
    Evidence,
    FollowupNarrator,
    NarrativeDraft,
    NarrativeTarget,
    RunSlot,
    StoredEvidence,
    StoredThesis,
    Subject,
    ThesisBuilder,
    ThesisDirection,
    ThesisError,
    ThesisEvidenceKind,
    ThesisSubjectKind,
    ThesisToolbox,
    ThesisVerdict,
    ToolLimitExceeded,
    brier_score,
    classify_outcome,
    evidence_ref,
    existing_theses,
    normalize_probabilities,
    past_theses,
    pending_narratives,
    render_blocks,
    render_text,
    store_narratives,
    store_theses,
)
from modules.thesis_state import IndexObservation, ObservedState, StockObservation

THESIS_INSERT = read_sql("postgres", "thesis", "insert.sql")
THESIS_SELECT_BY_RUN = read_sql("postgres", "thesis", "select_by_run.sql")
PENDING_GRADES = read_sql("postgres", "thesis_outcome", "select_pending_grades.sql")
INSERT_GRADE = read_sql("postgres", "thesis_outcome", "insert_grade.sql")
OUTCOME_SELECT_BY_IDS = read_sql("postgres", "thesis_outcome", "select_by_thesis_ids.sql")
NTH_OPEN_DAY = read_sql("postgres", "market_session", "select_nth_open_day.sql")
STOCK_HORIZON_RETURN = read_sql("postgres", "stock_investor_trade_daily", "select_horizon_return.sql")
INDEX_HORIZON_RETURN = read_sql("postgres", "index_bar", "select_horizon_return.sql")
EVIDENCE_INSERT = read_sql("postgres", "thesis_evidence", "insert.sql")
PRECEDENT_INSERT = read_sql("postgres", "thesis_precedent", "insert.sql")
EVIDENCE_SELECT_ALL = read_sql("postgres", "thesis_evidence", "select_by_thesis_ids.sql")
EVIDENCE_SELECT_TOP = read_sql("postgres", "thesis_evidence", "select_top_by_thesis_ids.sql")
STOCK_SESSION_RETURN = read_sql("postgres", "stock_investor_trade_daily", "select_session_return.sql")
INDEX_SESSION_RETURN = read_sql("postgres", "index_bar", "select_session_return.sql")
TOOL_DOCUMENTS = read_sql("postgres", "document", "select_recent_top.sql")
TOOL_DISCLOSURES = read_sql("postgres", "disclosure_event", "select_recent.sql")
TOOL_WINDOW_CHANGES = read_sql("postgres", "quote_bar", "select_window_changes.sql")
TOOL_US_CLOSE = read_sql("postgres", "quote_bar", "select_thesis_us_close.sql")
TOOL_ANALYST_OPINIONS = read_sql("postgres", "stock_analyst_opinion", "select_thesis_recent.sql")
PAST_THESES = read_sql("postgres", "thesis", "select_past_with_outcomes.sql")
PENDING_NARRATIVES = read_sql("postgres", "thesis_outcome", "select_pending_narratives.sql")
THESIS_BACKLOG = read_sql("postgres", "thesis_outcome", "select_backlog.sql")
NXT_AFTER_HOURS = read_sql("postgres", "stock_bar", "select_nxt_after_hours.sql")
INSERT_NARRATIVE = read_sql("postgres", "thesis_outcome", "insert_narrative.sql")


def inserted_columns(statement: str) -> tuple[str, ...]:
    columns = re.search(r"INSERT INTO \w+ \(([^)]+)\)", statement, re.DOTALL)
    assert columns is not None
    names = re.sub(r"--[^\n]*", "", columns.group(1))
    return tuple(name.strip() for name in names.split(",") if name.strip())


def placeholder_count(statement: str) -> int:
    values = re.search(r"VALUES \(([^)]+)\)", statement, re.DOTALL)
    assert values is not None
    return values.group(1).count("%s")


def required_columns(table: Table) -> set[str]:
    return {
        column.name
        for column in table.columns
        if not column.nullable and column.server_default is None and not column.primary_key
    }


def body(statement: str) -> str:
    """주석을 뺀 실행 부분. 주석이 테이블·컬럼 이름을 언급해도 검증이 속지 않게 한다."""
    return re.sub(r"--[^\n]*", "", statement)


# --- 채점 -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("horizon_days", "return_pct", "expected"),
    [
        # 지평마다 임계가 다르다. 경계값은 방향 쪽이다.
        (0, "0.29", ThesisDirection.FLAT),
        (0, "0.30", ThesisDirection.UP),
        (1, "0.29", ThesisDirection.FLAT),
        (1, "0.30", ThesisDirection.UP),
        (1, "-0.29", ThesisDirection.FLAT),
        (1, "-0.30", ThesisDirection.DOWN),
        (3, "0.49", ThesisDirection.FLAT),
        (3, "0.50", ThesisDirection.UP),
        (3, "-0.49", ThesisDirection.FLAT),
        (3, "-0.50", ThesisDirection.DOWN),
        (5, "0.69", ThesisDirection.FLAT),
        (5, "0.70", ThesisDirection.UP),
        (5, "-0.69", ThesisDirection.FLAT),
        (5, "-0.70", ThesisDirection.DOWN),
        (1, "0", ThesisDirection.FLAT),
        (5, "12.5", ThesisDirection.UP),
        (5, "-12.5", ThesisDirection.DOWN),
    ],
)
def test_classify_outcome_uses_a_wider_flat_band_at_longer_horizons(horizon_days, return_pct, expected):
    assert classify_outcome(Decimal(return_pct), horizon_days) is expected


def test_the_flat_band_widens_with_the_horizon():
    thresholds = [FLAT_THRESHOLD_PCT[horizon] for horizon in (0, 1, 3, 5)]

    # 하루 임계를 5영업일 누적에 쓰면 flat이 사실상 사라져 prob_flat이 항상 틀린 쪽에 붙는다.
    assert thresholds == sorted(thresholds)
    assert thresholds[-1] > thresholds[0]
    assert set(FLAT_THRESHOLD_PCT) == set(HORIZON_DAYS)


def test_an_unknown_horizon_is_refused_rather_than_defaulted():
    # 임계를 안 정한 지평에 기본값을 주면 그 지평만 조용히 다른 기준으로 채점된다.
    with pytest.raises(ThesisError, match="flat threshold"):
        classify_outcome(Decimal("1.0"), 2)


def test_brier_score_is_zero_for_a_perfect_call():
    score = brier_score(
        prob_up=Decimal(1),
        prob_down=Decimal(0),
        prob_flat=Decimal(0),
        outcome=ThesisDirection.UP,
    )

    assert score == Decimal(0)


def test_brier_score_is_two_when_certainty_points_the_wrong_way():
    score = brier_score(
        prob_up=Decimal(0),
        prob_down=Decimal(1),
        prob_flat=Decimal(0),
        outcome=ThesisDirection.UP,
    )

    assert score == Decimal(2)


def test_brier_score_of_uniform_probabilities_is_the_baseline():
    third = Decimal(1) / Decimal(3)

    scores = {
        outcome: brier_score(prob_up=third, prob_down=third, prob_flat=third, outcome=outcome)
        for outcome in ThesisDirection
    }

    # 균등 확률은 결과와 무관하게 같은 값이다. 이것이 예측력 비교의 baseline 0.667이다.
    assert len(set(scores.values())) == 1
    assert abs(next(iter(scores.values())) - Decimal("0.667")) < Decimal("0.001")


@pytest.mark.parametrize(
    "probabilities",
    [
        ("0.62", "0.23", "0.15"),
        ("0.33", "0.33", "0.34"),
        ("0", "0", "1"),
        ("0.5", "0.5", "0"),
    ],
)
@pytest.mark.parametrize("outcome", list(ThesisDirection))
def test_brier_score_stays_inside_its_check_constraint(probabilities, outcome):
    up, down, flat = (Decimal(value) for value in probabilities)
    assert up + down + flat == Decimal(1)

    score = brier_score(prob_up=up, prob_down=down, prob_flat=flat, outcome=outcome)

    # DB의 CHECK(brier_score BETWEEN 0 AND 2)가 어떤 입력에도 걸리지 않아야 한다.
    assert Decimal(0) <= score <= Decimal(2)


def test_brier_score_punishes_a_hesitant_correct_call_more_than_a_confident_one():
    confident = brier_score(
        prob_up=Decimal("0.8"),
        prob_down=Decimal("0.1"),
        prob_flat=Decimal("0.1"),
        outcome=ThesisDirection.UP,
    )
    hesitant = brier_score(
        prob_up=Decimal("0.4"),
        prob_down=Decimal("0.3"),
        prob_flat=Decimal("0.3"),
        outcome=ThesisDirection.UP,
    )

    # 방향만 맞으면 같은 점수인 hit/miss와 다른 지점이다.
    assert confident < hesitant


# --- SQL 대조 ---------------------------------------------------------------


def test_thesis_insert_matches_the_model_and_never_updates():
    table = Thesis.__table__
    columns = inserted_columns(THESIS_INSERT)

    assert set(columns) <= {column.name for column in table.columns}
    assert placeholder_count(THESIS_INSERT) == len(columns)
    assert "RETURNING id" in THESIS_INSERT
    # 첫 성공본 불변. upsert로 덮어쓰면 최초 판단이 사라진다.
    assert "ON CONFLICT ON CONSTRAINT uq_thesis_natural_key DO NOTHING" in THESIS_INSERT
    assert "DO UPDATE" not in THESIS_INSERT


def test_thesis_insert_leaves_the_grading_columns_to_the_grading_statement():
    columns = set(inserted_columns(THESIS_INSERT))
    grading = {"evaluated_at", "actual_return_pct", "actual_outcome", "brier_score"}

    assert not columns & grading
    # 추론 컬럼(NOT NULL)은 전부 채운다. 채점 넷은 nullable이라 required에 없다.
    assert required_columns(Thesis.__table__) <= columns


def test_thesis_evidence_insert_matches_the_model():
    table = ThesisEvidence.__table__
    columns = inserted_columns(EVIDENCE_INSERT)

    assert set(columns) <= {column.name for column in table.columns}
    assert required_columns(table) <= set(columns)
    assert placeholder_count(EVIDENCE_INSERT) == len(columns)
    assert "DO UPDATE" not in EVIDENCE_INSERT


SELECT_BY_RUN_COLUMNS = {
    # 근거를 붙이려면 id가, 어느 실행이 썼는지 알려면 dag_run_id가 필요하다.
    "id",
    "run_slot",
    "run_date",
    "as_of_at",
    "dag_run_id",
    "subject_kind",
    "subject_code",
    "label",
    "prob_up",
    "prob_down",
    "prob_flat",
    "up_reasoning",
    "down_reasoning",
    "flat_reasoning",
    "tool_rounds",
    "llm_model",
    "prompt_version",
}
EVIDENCE_SELECT_COLUMNS = {
    "thesis_id",
    "outcome_horizon_days",
    "evidence_kind",
    "evidence_ref",
    "evidence_title",
    "evidence_url",
    "rank",
}
OUTCOME_SELECT_COLUMNS = {
    "thesis_id",
    "horizon_days",
    "as_of_at",
    "dag_run_id",
    "evaluated_at",
    "actual_return_pct",
    "actual_outcome",
    "brier_score",
    "narrative",
    "verdict",
    "narrative_at",
    "llm_model",
    "prompt_version",
}


@pytest.mark.parametrize(
    ("statement", "model", "expected"),
    [
        (THESIS_SELECT_BY_RUN, Thesis, SELECT_BY_RUN_COLUMNS),
        (EVIDENCE_SELECT_ALL, ThesisEvidence, EVIDENCE_SELECT_COLUMNS),
        (EVIDENCE_SELECT_TOP, ThesisEvidence, EVIDENCE_SELECT_COLUMNS),
        (OUTCOME_SELECT_BY_IDS, ThesisOutcome, OUTCOME_SELECT_COLUMNS),
    ],
)
def test_selects_name_only_columns_the_model_has(statement, model, expected):
    names = {column.name for column in model.__table__.columns}
    projection = body(statement)
    projection = projection[projection.index("SELECT") : projection.index("FROM")]

    # 모델에 없는 이름을 고르면 조회가 런타임에야 죽는다. 이름 목록을 여기서 굳힌다.
    assert expected <= names
    for column in expected:
        assert re.search(rf"\b{column}\b", projection)


def test_the_thesis_row_carries_no_grading_columns():
    names = {column.name for column in Thesis.__table__.columns}

    # 지평별 결과는 thesis_outcome이 갖는다. 여기 두면 두 번째 지평이 첫 판단을 덮어써야 한다.
    assert not names & {"evaluated_at", "actual_return_pct", "actual_outcome", "brier_score"}
    assert not set(body(THESIS_SELECT_BY_RUN).split()) & {"brier_score,", "actual_outcome,"}


def test_grading_scan_covers_every_horizon_and_has_no_date_limit():
    predicate = body(PENDING_GRADES)

    assert "run_slot = 'pre_open'" in predicate
    # 지평 목록은 파라미터다. 상수를 SQL과 파이썬 두 곳에 두면 한쪽만 고쳐지는 날이 온다.
    assert "unnest(%s::integer[])" in predicate
    assert "thesis_outcome.evaluated_at IS NOT NULL" in predicate
    # 장후가 실패한 날의 forecast도 다음 실행이 회수해야 한다.
    assert "thesis.run_date =" not in predicate
    assert "thesis.run_date >" not in predicate


def test_the_grade_write_never_overwrites_a_score():
    statement = body(INSERT_GRADE)

    assert "ON CONFLICT ON CONSTRAINT uq_thesis_outcome_natural_key DO UPDATE" in statement
    # 이미 매긴 점수는 그대로 남는다. 해설이 먼저 만든 행만 채워진다.
    assert "WHERE thesis_outcome.evaluated_at IS NULL" in statement
    assert set(inserted_columns(INSERT_GRADE)) <= {column.name for column in ThesisOutcome.__table__.columns}
    # 해설 칸은 채점이 건드리지 않는다.
    assert not set(inserted_columns(INSERT_GRADE)) & {"narrative", "verdict", "narrative_at"}


def test_business_days_are_counted_by_the_calendar_not_by_us():
    query = body(NTH_OPEN_DAY)

    assert "market_session" in query
    assert "effective_open_day" in query
    assert "market_code = 'KRX'" in query
    # 아직 판정 못 한 날(NULL)을 개장일로 세면 나중에 기준일이 틀려 있게 된다.
    assert "IS NOT FALSE" not in query


@pytest.mark.parametrize("statement", [STOCK_HORIZON_RETURN, INDEX_HORIZON_RETURN])
def test_horizon_returns_keep_one_base_price_across_horizons(statement):
    query = body(statement)

    # 기준가를 지평마다 옮기면 누적이 연속되지 않아 T+1과 T+5를 비교할 수 없다.
    assert "base_close" in query
    assert "target_close" in query
    assert "return_pct" in query


def test_the_stock_horizon_return_reads_the_settled_close_not_the_minute_bars():
    query = body(STOCK_HORIZON_RETURN)

    # is_final은 REST 응답이라는 뜻이지 세션 완결이 아니다. 마감 동시호가가 빠진 날이 있다.
    assert "stock_investor_trade_daily" in query
    assert "stock_bar" not in query


def test_session_return_reads_the_settled_close_not_the_minute_bars():
    query = body(STOCK_SESSION_RETURN)

    # is_final은 REST 응답이라는 뜻이지 세션 완결이 아니다. 마감 동시호가가 빠진 날이 있다.
    assert "stock_investor_trade_daily" in query
    assert "stock_bar" not in query
    assert "close_price" in query
    assert "return_pct" in query


def test_index_session_return_takes_its_bar_time_as_a_parameter():
    query = body(INDEX_SESSION_RETURN)

    assert "index_bar" in query
    assert "previous_close" in query
    # KST 경계 계산은 파이썬이 한다. SQL에 시간대 변환을 넣으면 컨테이너 설정을 탄다.
    assert "bar_at = %s" in query
    assert "AT TIME ZONE" not in query


@pytest.mark.parametrize(
    "statement",
    [
        THESIS_SELECT_BY_RUN,
        PENDING_GRADES,
        OUTCOME_SELECT_BY_IDS,
        EVIDENCE_SELECT_ALL,
        EVIDENCE_SELECT_TOP,
        STOCK_SESSION_RETURN,
        INDEX_SESSION_RETURN,
        STOCK_HORIZON_RETURN,
        INDEX_HORIZON_RETURN,
        NXT_AFTER_HOURS,
    ],
)
def test_lookups_never_read_the_wall_clock(statement):
    # 조회의 기준 시각은 슬롯이 정하는 as_of_at이다(event-time cutoff).
    query = body(statement)
    assert "now()" not in query
    assert "CURRENT_TIMESTAMP" not in query


# --- 툴 SQL -----------------------------------------------------------------


def test_no_sql_comment_carries_a_percent_sign():
    """psycopg는 **주석까지** 훑어 플레이스홀더를 센다.

    두 가지로 터진다(둘 다 2026-08-21 실측). 주석의 `%` 다음 글자가 `s`가 아니면
    `only '%s', '%b', '%t' are allowed as placeholders`로 거절되고, `%s`면 자리 수에
    같이 세어져 `the query has N placeholders but M parameters were passed`가 된다.

    설명하려고 주석에 적은 퍼센트가 런타임에야 터지므로 여기서 막는다. 저장소의 모든 SQL이
    대상이다 — 이 함정은 thesis 전용이 아니다.
    """
    from pathlib import Path

    from modules.sql import SQL_ROOT

    offenders = []
    for path in sorted(Path(SQL_ROOT).rglob("*.sql")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            comment = line.partition("--")[2] if "--" in line else ""
            if "%" in comment:
                offenders.append(f"{path.relative_to(SQL_ROOT)}:{number}")
    assert not offenders, offenders


def test_document_tool_cuts_on_every_event_time_column():
    query = body(TOOL_DOCUMENTS)

    # 셋 다 걸어야 "그 시각에 알 수 있었던 것"에 가까워진다.
    assert "document.detected_at <= bounds.as_of_at" in query
    assert "document.assessed_at <= bounds.as_of_at" in query
    assert "document.updated_at <= bounds.as_of_at" in query
    # 이유 문장을 쓸 재료. 둘 다 컬럼이 아니라 assessment JSONB 안의 키다.
    assert "assessment -> 'new_facts'" in query
    assert "assessment ->> 'reason'" in query


def test_disclosure_tool_cuts_on_detection_not_on_the_receipt_date():
    query = body(TOOL_DISCLOSURES)

    # 접수일은 날짜뿐이라 창의 끝을 시각으로 자를 수 없다.
    assert "disclosure_event.detected_at <= bounds.as_of_at" in query
    assert "receipt_date <=" not in query
    assert "stock_code = ANY(%s)" in query


def test_macro_tool_excludes_the_boundary_bar_and_reads_only_the_view():
    query = body(TOOL_WINDOW_CHANGES)

    # bar_at은 봉의 시작 시각이라 그대로 자르면 경계 봉의 미래 1분이 섞인다.
    assert "bar.bar_at + interval '1 minute' <= bounds.as_of_at" in query
    assert "FROM quote_bar AS bar" in query
    # 뷰는 읽기 전용이다. 쓰기는 kind별 물리 테이블로 간다.
    assert "INSERT" not in query.upper()
    assert "UPDATE" not in query.upper()
    # 변화를 퍼센트로 만들지 않는다. 금리는 bp로 읽어야 해서 표기는 파이썬이 정한다.
    assert "first_close" in query
    assert "last_close" in query


def test_us_close_tool_reads_the_last_bar_of_us_symbols_only():
    query = body(TOOL_US_CLOSE)

    # macro_changes와 같은 경계 규칙. bar_at은 봉의 시작 시각이다.
    assert "bar.bar_at + interval '1 minute' <= bounds.as_of_at" in query
    assert "FROM quote_bar AS bar" in query
    # 창의 첫 봉이 아니라 마지막 봉 하나를 고르고, 비교 대상은 봉이 들고 온 전일 종가다.
    assert "DISTINCT ON (bar.provider, bar.symbol)" in query
    assert "ORDER BY bar.provider, bar.symbol, bar.bar_at DESC" in query
    assert "bar.previous_close" in query
    # 크립토(country XX)와 ADR(equity)은 여기 안 들어온다.
    assert "symbol.country = 'US'" in query
    assert "INSERT" not in query.upper()
    assert "UPDATE" not in query.upper()


# --- Toolbox ----------------------------------------------------------------

AS_OF = datetime(2026, 8, 21, 6, 30, tzinfo=UTC)
MACRO_WINDOW_START = datetime(2026, 8, 20, 6, 30, tzinfo=UTC)


class FakeCursor:
    def __init__(self, connection: "FakeConnection") -> None:
        self._connection = connection
        self._rows: list[tuple] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: str, parameters: Any = ()) -> None:
        # psycopg는 위치(tuple)와 이름(dict) 둘 다 받는다. dict를 tuple로 바꾸면 값이
        # 사라지고 키만 남으므로 모양을 그대로 보존한다.
        recorded = dict(parameters) if isinstance(parameters, dict) else tuple(parameters)
        self._connection.calls.append((statement, recorded))
        if self._connection.raises is not None:
            raise self._connection.raises
        self._rows = list(self._connection.results.get(_statement_key(statement), []))

    @property
    def rowcount(self) -> int:
        """UPDATE가 실제로 몇 행을 바꿨는지. 조건부 upsert가 이 값으로 갈린다."""
        return self._connection.rowcount

    def fetchall(self) -> list[tuple]:
        return self._rows

    def fetchone(self) -> tuple | None:
        return self._rows.pop(0) if self._rows else None


class FakeConnection:
    """PEP 249 연결 자리. SQL 문자열로 응답을 고른다."""

    def __init__(self, results: dict[str, list[tuple]] | None = None) -> None:
        self.results = results or {}
        self.calls: list[tuple[str, tuple | dict[str, Any]]] = []
        self.commits = 0
        self.rollbacks = 0
        self.raises: Exception | None = None
        # 조건부 upsert가 몇 행을 바꿨다고 할지. 테스트가 정한다.
        self.rowcount = 1

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _statement_key(statement: str) -> str:
    """어느 SQL인지 가르는 짧은 키. 주석을 먼저 뺀다 — 파일마다 머리말이 길다."""
    query = body(statement).strip()
    if query.startswith("INSERT INTO thesis_evidence"):
        return "evidence_insert"
    if query.startswith("INSERT INTO thesis_precedent"):
        return "precedent_insert"
    if query.startswith("INSERT INTO thesis_outcome"):
        return "narrative_insert" if "narrative" in query else "grade_insert"
    if query.startswith("INSERT INTO thesis"):
        return "thesis_insert"
    if "FROM thesis\nCROSS JOIN bounds" in query:
        return "past"
    # 투자의견 조회가 리포트 요약을 LATERAL로 붙이느라 `FROM document`를 품고 있다.
    # 문서 툴보다 먼저 본다 — 순서가 바뀌면 투자의견이 문서 결과를 받는다.
    if "FROM stock_analyst_opinion" in query:
        return "analyst_opinions"
    if "FROM document" in query:
        return "documents"
    if "FROM disclosure_event" in query:
        return "disclosures"
    if "FROM quote_bar" in query:
        # 둘 다 quote_bar를 읽는다. 마감 쿼리만 previous_close를 고른다.
        return "us_close" if "previous_close" in query else "macro"
    # 기술지표 조회는 두 원천을 UNION해서 `FROM stock_investor_trade_daily`와 `FROM quote_daily`를
    # 둘 다 품는다. 그 둘보다 먼저 보지 않으면 수급 조회 결과를 받는다.
    if "WITH requested AS" in query:
        return "daily_history"
    if "WITH available AS" in query:
        return "daily_history_symbols"
    if "FROM technical_signal" in query:
        return "recent_signals"
    if "FROM indicator_observation" in query:
        return "indicators"
    if "FROM market_investor_flow_snapshot" in query:
        return "market_flows"
    if "FROM market_movement_snapshot" in query:
        return "breadth"
    if "FROM stock_investor_trade_daily" in query:
        return "stock_flows"
    if "FROM stock_investor_estimate_snapshot" in query:
        return "stock_flow_estimates"
    if "FROM krx_market_funds_daily" in query:
        return "market_funds"
    if "FROM krx_stock_short_sale_daily" in query:
        return "short_and_credit"
    if "FROM thesis_evidence" in query:
        return "evidence_select"
    if "FROM thesis" in query:
        return "select_by_run"
    return "other"


def document_row(document_id: int = 1, *, new_facts: list[str] | None = None, reason: str = "이유") -> tuple:
    return (
        document_id,
        f"문서 {document_id}",
        f"https://example.test/{document_id}",
        "fed",
        datetime(2026, 8, 21, 0, 0, tzinfo=UTC),
        7,
        "positive",
        new_facts if new_facts is not None else ["새 사실"],
        reason,
        ["005930"],
    )


def disclosure_row(rcept_no: str = "20260821000123") -> tuple:
    return (
        rcept_no,
        "000660",
        "SK하이닉스",
        "단일판매·공급계약 해지",
        date(2026, 8, 21),
        datetime(2026, 8, 21, 1, 0, tzinfo=UTC),
    )


def macro_row(symbol: str = "SP500_FUT", kind: str = "index_future", first: str = "100", last: str = "101") -> tuple:
    return (
        "yahoo",
        symbol,
        f"{symbol} 라벨",
        kind,
        "US",
        Decimal(first),
        Decimal(last),
        MACRO_WINDOW_START,
        AS_OF,
        120,
    )


def us_close_row(
    symbol: str = "SP500",
    kind: str = "index",
    close: str = "7674.37",
    previous_close: str = "7641.16",
) -> tuple:
    """`quote_bar/select_thesis_us_close.sql`의 한 행."""
    return (
        "kis" if symbol in ("SP500", "NASDAQ") else "yahoo",
        symbol,
        f"{symbol} 라벨",
        kind,
        Decimal(close),
        Decimal(previous_close),
        AS_OF - timedelta(hours=10),
    )


def toolbox(connection: FakeConnection, *, subject_codes: list[str] | None = None) -> ThesisToolbox:
    return ThesisToolbox(
        connection,
        as_of_at=AS_OF,
        macro_window_start=MACRO_WINDOW_START,
        watched_codes=["005930", "000660"],
        subject_codes=subject_codes or [],
    )


def indicator_row(
    value: Decimal,
    previous: Decimal | None,
    *,
    kind: str = "government_bond",
) -> tuple:
    """`indicator_observation/select_thesis_latest.sql`의 한 행."""
    return (
        "fred",
        "DGS10",
        "US",
        "미국",
        "미국 국채 10년",
        kind,
        120,
        "Percent",
        date(2026, 8, 20),
        value,
        previous,
        date(2026, 8, 19) if previous is not None else None,
    )


def test_tool_windows_end_at_the_slot_time_not_at_the_wall_clock():
    connection = FakeConnection({"documents": [document_row()]})
    box = toolbox(connection)

    box.run("recent_documents", {"hours": 12, "min_score": 5})

    _, parameters = connection.calls[0]
    # 창의 끝은 as_of_at이고 시작은 거기서 12시간 거슬러 올라간 시각이다.
    assert parameters[0] == AS_OF - timedelta(hours=12)
    assert parameters[1] == AS_OF
    assert parameters[2] == 5


@pytest.mark.parametrize(
    ("hours", "expected_hours"),
    [(0, 1), (73, 72), (12, 12), ("bad", 72), (None, 72)],
)
def test_tool_arguments_outside_the_range_are_clamped_not_rejected(hours, expected_hours):
    connection = FakeConnection({"documents": []})
    box = toolbox(connection)

    box.run("recent_documents", {"hours": hours, "min_score": 5})

    _, parameters = connection.calls[0]
    assert parameters[0] == AS_OF - timedelta(hours=expected_hours)


@pytest.mark.parametrize(("min_score", "expected"), [(-1, 0), (101, 100), (5, 5)])
def test_the_score_floor_is_clamped_too(min_score, expected):
    connection = FakeConnection({"documents": []})
    box = toolbox(connection)

    box.run("recent_documents", {"hours": 6, "min_score": min_score})

    _, parameters = connection.calls[0]
    assert parameters[2] == expected


def test_the_result_cap_travels_to_the_database_as_a_limit():
    connection = FakeConnection({"documents": []})
    box = toolbox(connection)

    box.run("recent_documents", {"hours": 6, "min_score": 0})

    _, parameters = connection.calls[0]
    # 20건 상한을 파이썬에서 자르지 않고 SQL LIMIT으로 넘긴다.
    assert parameters[3] == 20


def test_tool_results_register_refs_whose_prefix_is_the_evidence_kind():
    connection = FakeConnection(
        {
            "documents": [document_row(7)],
            "disclosures": [disclosure_row()],
            "macro": [macro_row()],
        }
    )
    box = toolbox(connection)

    box.run("recent_documents", {"hours": 6, "min_score": 0})
    box.run("recent_disclosures", {"hours": 6})
    box.run("macro_changes", {})

    assert set(box.registry) == {
        "document:7",
        "disclosure:20260821000123",
        "macro_change:SP500_FUT",
    }
    for ref, item in box.registry.items():
        assert ref.split(":", 1)[0] == item.kind.value


def test_disclosures_carry_a_viewer_url_and_macro_changes_do_not():
    connection = FakeConnection({"disclosures": [disclosure_row()], "macro": [macro_row()]})
    box = toolbox(connection)

    box.run("recent_disclosures", {"hours": 6})
    box.run("macro_changes", {})

    assert box.registry["disclosure:20260821000123"].url == DART_VIEWER_URL.format(rcept_no="20260821000123")
    # 매크로 변화는 링크할 곳이 없다. Slack 근거 줄이 제목만 그린다.
    assert box.registry["macro_change:SP500_FUT"].url is None


def test_rate_changes_are_reported_in_basis_points_not_percent():
    connection = FakeConnection({"macro": [macro_row("US10Y", "rate", "4.65", "4.70")]})
    box = toolbox(connection)

    body_text = box.run("macro_changes", {})

    item = box.registry["macro_change:US10Y"]
    # 4.65 -> 4.70은 "+1.08%"가 아니라 "+5bp"다. 퍼센트로 주면 모델이 급등으로 읽는다.
    assert item.detail["change_bp"] == pytest.approx(5.0)
    assert "change_pct" not in item.detail
    assert "bp" in body_text


def test_us_close_is_measured_against_the_previous_session_close():
    """창 첫 봉 대비로는 KIS 현물의 밤사이 등락이 보이지 않는다. 마감 툴은 전일 종가와 비교한다."""
    connection = FakeConnection({"us_close": [us_close_row()]})
    box = toolbox(connection)

    body_text = box.run("us_market_close", {})

    item = box.registry["macro_change:SP500@close"]
    assert item.detail["close"] == pytest.approx(7674.37)
    assert item.detail["previous_close"] == pytest.approx(7641.16)
    assert item.detail["change_pct"] == pytest.approx(0.43, abs=0.01)
    # 시각 칸은 이름이 시간대를 밝힌다. 모델이 "어느 날 장이었나"를 이 값으로 정한다.
    assert item.detail["closed_at_kst"].endswith("KST")
    assert "마감" in body_text


def test_us_close_rates_are_reported_in_basis_points():
    connection = FakeConnection({"us_close": [us_close_row("US10Y", "rate", "4.70", "4.65")]})
    box = toolbox(connection)

    box.run("us_market_close", {})

    item = box.registry["macro_change:US10Y@close"]
    assert item.detail["change_bp"] == pytest.approx(5.0)
    assert "change_pct" not in item.detail


def test_us_close_refs_do_not_overwrite_the_window_change_of_the_same_symbol():
    """겹치면 나중에 부른 툴이 앞의 근거를 조용히 덮는다. 창 변화와 마감 등락은 다른 숫자다."""
    connection = FakeConnection(
        {
            "macro": [macro_row("SP500_FUT", "index_future", "100", "101")],
            "us_close": [us_close_row("SP500_FUT", "index_future", "101", "99")],
        }
    )
    box = toolbox(connection)

    box.run("macro_changes", {})
    box.run("us_market_close", {})

    assert set(box.registry) == {"macro_change:SP500_FUT", "macro_change:SP500_FUT@close"}
    assert box.registry["macro_change:SP500_FUT"].detail["change_pct"] == pytest.approx(1.0)
    assert box.registry["macro_change:SP500_FUT@close"].detail["change_pct"] == pytest.approx(2.02, abs=0.01)


def test_non_rate_changes_are_reported_in_percent():
    connection = FakeConnection({"macro": [macro_row("SP500_FUT", "index_future", "100", "101")]})
    box = toolbox(connection)

    box.run("macro_changes", {})

    item = box.registry["macro_change:SP500_FUT"]
    assert item.detail["change_pct"] == pytest.approx(1.0)
    assert "change_bp" not in item.detail


def test_a_long_document_is_trimmed_so_one_item_cannot_eat_the_context():
    long_reason = "가" * 400
    facts = ["나" * 150, "다" * 150, "라" * 150]
    connection = FakeConnection({"documents": [document_row(new_facts=facts, reason=long_reason)]})
    box = toolbox(connection)

    box.run("recent_documents", {"hours": 6, "min_score": 0})

    detail = box.registry["document:1"].detail
    spent = len(detail["reason"]) + sum(len(fact) for fact in detail["new_facts"])
    assert spent <= MAX_ITEM_DETAIL_CHARS
    assert len(detail["new_facts"]) < len(facts)


def test_an_unknown_tool_name_is_refused_without_touching_the_database():
    connection = FakeConnection()
    box = toolbox(connection)

    with pytest.raises(ToolLimitExceeded, match="모르는 툴"):
        box.run("web_search", {"q": "삼성전자"})

    assert connection.calls == []


def test_the_call_budget_stops_the_investigation():
    connection = FakeConnection({"documents": []})
    box = toolbox(connection)

    for _ in range(MAX_TOOL_CALLS):
        box.run("recent_documents", {"hours": 1, "min_score": 0})

    with pytest.raises(ToolLimitExceeded, match="상한 초과"):
        box.run("recent_documents", {"hours": 1, "min_score": 0})


def test_the_character_budget_stops_the_investigation():
    wide = [document_row(index, reason="가" * 500) for index in range(20)]
    connection = FakeConnection({"documents": wide})
    box = toolbox(connection)

    with pytest.raises(ToolLimitExceeded, match="상한 초과"):
        for _ in range(MAX_TOOL_CALLS):
            box.run("recent_documents", {"hours": 1, "min_score": 0})


def test_database_failures_are_not_disguised_as_empty_results():
    connection = FakeConnection()
    connection.raises = ConnectionError("server closed the connection")
    box = toolbox(connection)

    # 빈 결과는 "그 창에 문서가 없다"는 뜻이어야 한다. 오류를 그것으로 바꾸지 않는다.
    with pytest.raises(ConnectionError):
        box.run("recent_documents", {"hours": 6, "min_score": 0})


# --- 확률 정규화 -------------------------------------------------------------


@pytest.mark.parametrize(
    "probabilities",
    [(0.62, 0.23, 0.15), (0.6, 0.2, 0.19), (0.34, 0.33, 0.34), (1.0, 0.0, 0.0)],
)
def test_probabilities_inside_the_tolerance_are_scaled_to_exactly_one(probabilities):
    scaled = normalize_probabilities(*probabilities)

    assert scaled is not None
    # DB CHECK가 합 오차 0.001 미만을 요구한다. 정확히 1이어야 통과한다.
    assert sum(scaled) == Decimal(1)
    assert all(Decimal(0) <= value <= Decimal(1) for value in scaled)


@pytest.mark.parametrize("probabilities", [(0.3, 0.3, 0.3), (0.5, 0.5, 0.5), (0.0, 0.0, 0.0)])
def test_probabilities_outside_the_tolerance_are_refused(probabilities):
    # 억지로 정규화하면 모델이 부르지 않은 확률을 우리가 지어내게 된다.
    assert normalize_probabilities(*probabilities) is None


def test_scaling_keeps_the_relative_order():
    scaled = normalize_probabilities(0.6, 0.2, 0.19)

    assert scaled is not None
    assert scaled[0] > scaled[1] > scaled[2]


# --- Builder ----------------------------------------------------------------

SUBJECTS = (
    Subject(kind=ThesisSubjectKind.INDEX, code="KOSPI", label="코스피"),
    Subject(kind=ThesisSubjectKind.STOCK, code="000660", label="SK하이닉스"),
)
# 관측 상태의 모양은 `thesis_state`가 정한다. 맨 dict를 넘기면 프롬프트에 실릴 키가
# 테스트에서만 존재할 수 있다.
OBSERVED = ObservedState(
    session=date(2026, 8, 20),
    index={"KOSPI": IndexObservation(close=3150.0, return_pct=-2.1)},
    stock={"005930": StockObservation(close=71500.0)},
)


class ScriptedModel:
    """LangChain 모델 자리. 네트워크를 쓰지 않는다."""

    def __init__(self, *replies: AIMessage) -> None:
        self.replies = list(replies)
        self.bound: dict[str, Any] = {}
        self.tools: Any = None
        self.calls: list[list[Any]] = []

    def bind(self, **kwargs: Any) -> "ScriptedModel":
        self.bound.update(kwargs)
        return self

    def bind_tools(self, tools: Any) -> "ScriptedModel":
        self.tools = tools
        return self

    def invoke(self, messages: list[Any]) -> AIMessage:
        self.calls.append(list(messages))
        return self.replies.pop(0)


def answer_message(*theses: dict[str, Any]) -> AIMessage:
    return AIMessage(json.dumps({"theses": list(theses)}))


def claim_payload(ref: str, direction: str = "up", mechanism: str = "수급 경로") -> dict[str, Any]:
    return {"ref": ref, "direction": direction, "mechanism": mechanism}


def thesis_payload(code: str = "KOSPI", refs: list[str] | None = None, **overrides: Any) -> dict[str, Any]:
    """`refs`는 같은 방향·경로의 인용으로 편다. 방향을 가르는 테스트는 `claims=`로 직접 준다."""
    payload = {
        "subject_code": code,
        "prob_up": 0.62,
        "prob_down": 0.23,
        "prob_flat": 0.15,
        "up_reasoning": "밤사이 미국 지수가 올랐다",
        "down_reasoning": "공시가 수급을 눌렀다",
        "flat_reasoning": "재료가 상쇄됐다",
        "claims": [claim_payload(ref) for ref in refs] if refs is not None else [],
    }
    payload.update(overrides)
    return payload


def tool_call_message(name: str = "recent_documents", args: dict[str, Any] | None = None) -> AIMessage:
    return AIMessage(
        "",
        tool_calls=[{"name": name, "args": args or {"hours": 6, "min_score": 5}, "id": f"call_{name}"}],
    )


# 조사 단계가 툴을 부르지 않고 끝냈다는 응답. 그래프는 항상 investigate로 시작하므로
# 답변부터 검사하는 테스트도 이것을 하나 앞에 둔다.
DONE_INVESTIGATING = "조사할 것이 없다"


def scripted(*replies: AIMessage) -> ScriptedModel:
    """조사를 건너뛰고 곧바로 답변 단계로 가는 모델."""
    return ScriptedModel(AIMessage(DONE_INVESTIGATING), *replies)


def build(model: ScriptedModel, connection: FakeConnection) -> ThesisBuilder:
    return ThesisBuilder(model, toolbox(connection))


def run_builder(builder: ThesisBuilder) -> tuple[Any, int]:
    return builder.run(
        run_slot=RunSlot.PRE_OPEN,
        as_of_at=AS_OF,
        subjects=SUBJECTS,
        observed_state=OBSERVED,
        past_theses={},
    )


def test_the_builder_investigates_with_tools_then_answers_with_a_schema():
    connection = FakeConnection({"documents": [document_row(7)]})
    model = ScriptedModel(
        tool_call_message(),
        AIMessage(DONE_INVESTIGATING),
        answer_message(thesis_payload(refs=["document:7"])),
    )
    builder = build(model, connection)

    drafts, tool_rounds = run_builder(builder)

    assert tool_rounds == 1
    assert len(drafts) == 1
    assert drafts[0].evidence_refs == ("document:7",)
    # 조사 요청에는 툴이, 답변 요청에는 스키마가 실린다. 한 요청에 섞이지 않는다.
    assert model.tools is not None
    assert "response_format" in model.bound
    # 툴 결과가 그 사이 대화에 들어가 있다.
    assert any(isinstance(message, ToolMessage) for message in model.calls[-1])


def test_the_tool_schema_is_derived_from_the_code_not_hand_written():
    """`args_schema`에서 뽑는다. 손으로 쓴 wire format dict는 코드와 어긋나도 아무도 못 잡는다."""
    box = toolbox(FakeConnection())

    by_name = {tool.name: tool for tool in box.tools}
    assert set(by_name) == {
        "recent_documents",
        "recent_disclosures",
        "macro_changes",
        "us_market_close",
        "past_theses",
        "macro_indicators",
        "market_investor_flows",
        "market_breadth",
        "stock_investor_flows",
        "market_funds",
        "daily_history",
        "short_and_credit",
        "analyst_opinions",
    }

    schema = by_name["recent_documents"].args_schema.model_json_schema()
    assert schema["properties"]["hours"]["type"] == "integer"
    # 상한 상수가 description에 실려 프롬프트가 코드를 따라간다.
    assert str(MAX_WINDOW_HOURS) in schema["properties"]["hours"]["description"]


def test_database_failures_survive_the_tool_node():
    """**`handle_tool_errors` 기본값(True)이면 여기서 깨진다.**

    연결 끊김이 "결과 없음" `ToolMessage`로 위장되면 모델이 "그 창에 문서가 없었다"로 읽고
    태스크는 성공으로 끝난다. `ToolLimitExceeded`만 잡도록 타입을 준 이유다.
    """
    connection = FakeConnection({"documents": []})
    connection.raises = ConnectionError("server closed the connection")
    reply = AIMessage("", tool_calls=[{"name": "recent_documents", "args": {"hours": 6, "min_score": 0}, "id": "a"}])
    builder = build(ScriptedModel(reply), connection)

    with pytest.raises(ConnectionError):
        run_builder(builder)


# --- 2026-08-21에 연 툴 일곱 -------------------------------------------------


def test_every_tool_window_ends_at_the_slot_time():
    """새 툴도 예외가 아니다. `now()`를 보면 장전 슬롯 재실행이 장중 데이터를 끌어온다."""
    connection = FakeConnection()
    box = toolbox(connection, subject_codes=["KOSPI"])

    for name, arguments in (
        ("us_market_close", {}),
        ("macro_indicators", {"kind": "government_bond"}),
        ("market_investor_flows", {}),
        ("market_breadth", {}),
        ("stock_investor_flows", {"days": 5}),
        ("market_funds", {"days": 10}),
        ("daily_history", {"symbol": "KOSPI", "days": 10}),
        ("short_and_credit", {}),
        ("analyst_opinions", {"ticker": "005930"}),
    ):
        connection.calls.clear()
        box.run(name, arguments)
        statement, parameters = connection.calls[0]
        assert parameters["as_of_at"] == AS_OF, name
        # 창의 끝을 SQL이 실제로 걸고 있는지도 본다. 파라미터만 넘기고 술어가 없으면 소용없다.
        assert "as_of_at" in body(statement), name


# --- 6단계(2026-08-22) 애널리스트 투자의견 --------------------------------------


def test_analyst_opinions_refuses_a_stock_outside_the_watch_list_without_touching_the_database():
    """`past_theses`의 `subject_code`와 같다. 모델이 아무 종목이나 조회하며 문맥을 채우게 두지 않는다."""
    connection = FakeConnection()
    box = toolbox(connection)

    with pytest.raises(ToolLimitExceeded, match="추적 종목 밖") as error:
        box.run("analyst_opinions", {"ticker": "003550"})

    assert "005930" in str(error.value)
    assert connection.calls == []


def test_analyst_opinions_keeps_the_broker_wording_and_does_not_cite():
    """문맥 툴이다 — 레지스트리에 넣지 않는다. 인용할 출처는 리포트 문서이고 이것은 관측이다."""
    rows = [
        (date(2026, 8, 10), "키움", "BUY", "BUY", Decimal(350000), Decimal(231000), Decimal("-34.00"), None),
        (date(2026, 7, 31), "한국투자", "매수", "매수", Decimal(650000), Decimal(207000), Decimal("-68.15"), None),
    ]
    connection = FakeConnection({"analyst_opinions": rows})
    box = toolbox(connection)

    reply = json.loads(box.run("analyst_opinions", {"ticker": "005930"}))

    assert reply["stock_code"] == "005930"
    assert [item["broker_name"] for item in reply["opinions"]] == ["키움", "한국투자"]
    assert reply["opinions"][0]["target_price"] == 350000
    assert reply["opinions"][1]["opinion"] == "매수"
    # 리포트를 못 찾으면 사유 칸 자체가 없다. 빈 문자열을 주면 모델이 "사유 없음"으로 읽는다.
    assert "reason" not in reply["opinions"][0]
    assert box.registry == {}
    _, parameters = connection.calls[0]
    assert parameters["stock_code"] == "005930"
    assert parameters["limit"] == MAX_TOOL_RESULTS


def test_analyst_opinions_carries_the_report_summary_as_the_reason():
    """KIS는 숫자만 준다. 사유가 없으면 모델이 목표가만 보고 이유를 지어낸다."""
    summary = "투자의견 Buy · 목표가 350,000 · " + "HBM4 공급 정상화로 " * 40
    rows = [(date(2026, 8, 10), "키움", "BUY", "BUY", Decimal(350000), Decimal(231000), Decimal("-34.00"), summary)]
    box = toolbox(FakeConnection({"analyst_opinions": rows}))

    (item,) = json.loads(box.run("analyst_opinions", {"ticker": "005930"}))["opinions"]

    assert item["reason"].startswith("투자의견 Buy · 목표가 350,000 · HBM4")
    # 스무 건까지 오므로 한 건이 컨텍스트를 다 먹으면 안 된다.
    assert len(item["reason"]) == MAX_OPINION_REASON_CHARS


def test_the_opinion_query_joins_the_report_by_stock_broker_and_day():
    """세 조건이 다 걸려야 한다. 하나라도 빠지면 남의 리포트가 사유로 붙는다."""
    query = body(TOOL_ANALYST_OPINIONS)

    assert "strpos(document.title, instrument.name || ': ') = 1" in query
    assert "strpos(document.title, ' - ' || opinion.broker_name) > 0" in query
    assert "(document.published_at AT TIME ZONE 'Asia/Seoul')::date = opinion.business_date" in query
    # 리포트가 없어도 숫자는 준다. INNER JOIN이면 네이버에 안 올라온 증권사가 통째로 사라진다.
    assert "LEFT JOIN LATERAL" in query
    # 평가 전에도 붙어야 한다. `document_instrument`는 LLM 태깅이 채우는 값이다.
    assert "document_instrument" not in query
    # 창의 끝은 여기서도 기준 시각이다.
    assert query.count("as_of_at") >= 2


def test_macro_indicators_reports_rate_moves_in_basis_points():
    """4.65에서 4.70은 `+1.08%`가 아니라 `+5bp`다. 퍼센트로 주면 모델이 급등으로 읽는다."""
    connection = FakeConnection({"indicators": [indicator_row(Decimal("4.70"), Decimal("4.65"))]})
    box = toolbox(connection)

    payload = json.loads(box.run("macro_indicators", {"kind": "government_bond"}))

    assert payload["series"][0]["change_bp"] == 5.0
    assert "change" not in payload["series"][0]


def test_macro_indicators_reports_non_rate_moves_as_plain_values():
    """물가지수는 퍼센트도 bp도 아니다. 지수 포인트를 bp로 부르면 그것도 거짓이다."""
    connection = FakeConnection({"indicators": [indicator_row(Decimal("315.6"), Decimal("314.8"), kind="price_index")]})
    box = toolbox(connection)

    payload = json.loads(box.run("macro_indicators", {"kind": "price_index"}))

    assert payload["series"][0]["change"] == 0.8
    assert "change_bp" not in payload["series"][0]


def test_macro_indicators_never_mixes_kinds_in_one_answer():
    """단위가 다른 값이 한 표에 섞이면 화면이 아니라 모델이 조용히 거짓말을 한다."""
    connection = FakeConnection({"indicators": []})
    box = toolbox(connection)

    box.run("macro_indicators", {"kind": "온갖 것"})

    _, parameters = connection.calls[0]
    # 모르는 값은 거절이 아니라 기본 종류로 읽는다. 왕복 하나를 오타에 쓰지 않는다.
    assert parameters["kinds"] == ["government_bond"]


def test_a_missing_previous_observation_is_not_dressed_up_as_no_change():
    """첫 관측을 "변화 0"으로 꾸미면 모델이 없는 사실을 근거로 쓴다."""
    connection = FakeConnection({"indicators": [indicator_row(Decimal("4.70"), None)]})
    box = toolbox(connection)

    series = json.loads(box.run("macro_indicators", {"kind": "government_bond"}))["series"][0]

    assert series["previous_value"] is None
    assert "change_bp" not in series


def test_settled_and_estimated_flows_stay_in_separate_boxes():
    """확정 수급과 장중 추정은 어긋난다. 한 칸에 담으면 모델이 그 차이를 모른 채 읽는다."""
    connection = FakeConnection({"stock_flows": [], "stock_flow_estimates": []})
    box = toolbox(connection)

    payload = json.loads(box.run("stock_investor_flows", {"days": 5}))

    assert set(payload) == {"settled", "intraday_estimate", "note"}


def test_short_and_credit_excludes_the_current_business_day():
    """KIS가 장중에 당일 공매도를 0으로 보낸다. 그 행을 주면 "오늘 공매도 0주"가 관측이 된다.

    2026-08-21 실측: `business_date = 2026-08-21`, `short_sale_quantity = 0`인 행이 그날
    08:10 KST에 이미 들어와 있었다. `created_at` cutoff로는 안 걸러진다 — 전날 밤에
    들어온 행이라서다. 날짜로 직접 잘라야 한다.
    """
    connection = FakeConnection({"short_and_credit": []})
    box = toolbox(connection)

    box.run("short_and_credit", {})

    statement = body(connection.calls[0][0])
    assert "business_date < (%(as_of_at)s AT TIME ZONE 'Asia/Seoul')::date" in statement


def test_daily_history_says_which_symbols_have_bars_when_it_finds_none():
    """빈 배열만 주면 모델이 "이력이 없다"가 아니라 "움직임이 없었다"로 읽는다."""
    connection = FakeConnection({"daily_history": []})
    box = toolbox(connection)

    payload = json.loads(box.run("daily_history", {"symbol": "KOSPI", "days": 10}))

    assert payload["bars"] == []
    assert "KOSPI" in payload["note"]
    assert "available_symbols" in payload


# --- 기술지표 (docs/market-technical-indicators.md 7.1절) ----------------------


def daily_history_row(
    business_date: date,
    close: float,
    *,
    symbol: str = "KOSPI",
    provider: str = "kis",
    label: str = "코스피",
    kind: str = "index",
    country: str = "KR",
    volume: int | None = 1000,
) -> tuple:
    """`technical/select_history.sql` 한 행. 조회는 최신순이라 부르는 쪽이 뒤집는다."""
    return (
        provider,
        symbol,
        label,
        kind,
        country,
        business_date,
        Decimal(str(close)),
        Decimal(str(close * 1.01)),
        Decimal(str(close * 0.99)),
        Decimal(str(close)),
        volume,
    )


def rising_history(count: int = 120, base: float = 0.0, **row_options) -> list[tuple]:
    """최신순 일봉. 종가가 `base + 1 .. base + count`로 올라간다.

    `base=0`이 문서 5.3절의 고정 벡터다. 국내 대상에는 큰 `base`를 준다 — 1에서 2로 가는
    +100%는 35% 단절 guard에 걸려 지표가 나오지 않는다.
    """
    rows = []
    cursor = date(2026, 1, 5)
    made = 0
    while made < count:
        if cursor.weekday() < 5:
            rows.append(daily_history_row(cursor, base + made + 1, **row_options))
            made += 1
        cursor += timedelta(days=1)
    return list(reversed(rows))


def test_daily_history_reads_both_sources_through_one_query():
    connection = FakeConnection({"daily_history": rising_history()})
    box = toolbox(connection)

    box.run("daily_history", {"symbol": "KOSPI", "days": 10})

    statement, parameters = connection.calls[0]
    query = body(statement)
    # 지수는 뷰에서, 국내 종목은 확정 수급 테이블에서 온다. 한쪽만 보면 종목이 빠진다.
    assert "FROM quote_daily" in query
    assert "FROM stock_investor_trade_daily" in query
    # KIS equity를 뷰 쪽에서 빼지 않으면 같은 종목이 두 번 들어온다.
    assert "NOT (daily.provider = 'kis' AND symbol.kind = 'equity')" in query
    assert parameters["symbols"] == ["KOSPI"]
    assert parameters["include_watched"] is False
    # 지표 계산에 쓸 만큼 받되 모델에게 보여 주는 건 요청한 days뿐이다.
    assert parameters["limit"] == TECHNICAL_LOOKBACK_BARS


def test_daily_history_returns_the_snapshot_next_to_the_raw_bars():
    # 문서 5.3절의 고정 벡터(종가 1..120). 해외 심볼이라 국내 단절 guard를 타지 않는다.
    rows = rising_history(symbol="SP500_FUT", provider="yahoo", country="US", kind="index_future", label="S&P500 선물")
    connection = FakeConnection({"daily_history": rows})
    box = toolbox(connection)

    payload = json.loads(box.run("daily_history", {"symbol": "SP500_FUT", "days": 10}))

    assert len(payload["bars"]) == 10
    # 원시 봉은 기존처럼 최신순이고 키도 그대로다.
    assert payload["bars"][0]["close"] > payload["bars"][-1]["close"]
    assert set(payload["bars"][0]) == {
        "label",
        "kind",
        "country",
        "business_date",
        "open",
        "high",
        "low",
        "close",
        "volume",
    }
    snapshot = payload["technical_snapshot"]
    assert snapshot["observations"] == 120
    assert snapshot["sma20"] == pytest.approx(110.5)
    assert snapshot["sma60"] == pytest.approx(90.5)
    assert snapshot["macd"] == pytest.approx(7.0)
    assert snapshot["macd_histogram"] == pytest.approx(0.0)
    assert snapshot["rsi14"] == pytest.approx(100.0)


def test_daily_history_omits_the_snapshot_when_the_sample_is_short():
    """지표를 못 내는 것과 원시 봉이 없는 것은 다르다. 봉은 그대로 준다."""
    connection = FakeConnection({"daily_history": rising_history(30)})
    box = toolbox(connection)

    payload = json.loads(box.run("daily_history", {"symbol": "KOSPI", "days": 10}))

    assert payload["technical_snapshot"] is None
    assert len(payload["bars"]) == 10


def domestic_history(count: int = 120) -> list[tuple]:
    """국내 종목의 실제 가격대에 가까운 최신순 일봉."""
    return rising_history(count, base=70_000.0, symbol="005930", kind="equity", label="삼성전자")


def test_a_domestic_series_gets_a_snapshot():
    connection = FakeConnection({"daily_history": domestic_history()})
    box = toolbox(connection)

    payload = json.loads(box.run("daily_history", {"symbol": "005930", "days": 5}))

    assert payload["technical_snapshot"]["observations"] == 120


def test_a_domestic_price_gap_hides_the_snapshot():
    """분할·병합이나 원천 이상이 의심되면 이동평균을 그대로 보여 주지 않는다."""
    broken = domestic_history()
    # 최신순이라 인덱스 0이 마지막 봉이다. 하루 만에 반토막 난 모양을 만든다.
    gap_row = list(broken[40])
    gap_row[9] = Decimal(35000)
    gap_row[6] = Decimal(35000)
    broken[40] = tuple(gap_row)
    connection = FakeConnection({"daily_history": broken})
    box = toolbox(connection)

    payload = json.loads(box.run("daily_history", {"symbol": "005930", "days": 5}))

    assert payload["technical_snapshot"] is None
    assert len(payload["bars"]) == 5


def test_a_foreign_series_is_not_held_to_the_domestic_gap_guard():
    """환율·해외 지수는 가격제한폭이 없어 같은 잣대를 댈 수 없다."""
    rows = rising_history(symbol="USDKRW", provider="yahoo", country="US", kind="fx", label="원달러")
    payload = json.loads(toolbox(FakeConnection({"daily_history": rows})).run("daily_history", {"symbol": "USDKRW"}))

    assert payload["technical_snapshot"] is not None


def signal_row(
    signal_id: int = 1042,
    *,
    symbol: str = "005930",
    signal_date: date = date(2026, 8, 19),
    kind: str = "sma_cross",
    direction: str = "up",
) -> tuple:
    return (
        signal_id,
        symbol,
        signal_date,
        kind,
        direction,
        Decimal(71500),
        Decimal("61.30"),
        Decimal("1.1200"),
    )


def test_the_snapshot_is_not_evidence_but_signals_are():
    """지표는 문맥이라 인용할 수 없고, 신호는 사건이라 인용할 수 있다.

    "신호를 근거로 쓴 추론이 안 쓴 추론보다 나았나"를 재려면 인용이 엣지로 남아야 한다.
    """
    connection = FakeConnection({"daily_history": domestic_history(), "recent_signals": [signal_row()]})
    box = toolbox(connection)

    box.run("daily_history", {"symbol": "005930", "days": 10})

    assert list(box.registry) == ["technical_signal:1042"]
    item = box.registry["technical_signal:1042"]
    assert item.kind is ThesisEvidenceKind.TECHNICAL_SIGNAL
    assert "골든크로스" in item.title
    assert item.url is None
    assert item.detail["direction"] == "up"


def test_recent_signals_come_with_the_daily_history():
    connection = FakeConnection(
        {
            "daily_history": domestic_history(),
            "recent_signals": [signal_row(), signal_row(1041, kind="macd_cross", direction="down")],
        }
    )
    box = toolbox(connection)

    payload = json.loads(box.run("daily_history", {"symbol": "005930", "days": 10}))

    assert payload["recent_signals"] == [
        {"ref": "technical_signal:1042", "signal_date": "2026-08-19", "kind": "sma_cross", "direction": "up"},
        {"ref": "technical_signal:1041", "signal_date": "2026-08-19", "kind": "macd_cross", "direction": "down"},
    ]


def test_the_signal_query_ends_at_the_slot_time():
    """신호는 마감 뒤 계산된다. `signal_date`로만 걸면 장후 슬롯이 당일 신호를 본 것으로 읽는다."""
    connection = FakeConnection({"daily_history": domestic_history(), "recent_signals": []})
    box = toolbox(connection)

    box.run("daily_history", {"symbol": "005930", "days": 10})

    statement, parameters = next(
        (statement, parameters) for statement, parameters in connection.calls if "FROM technical_signal" in statement
    )
    assert parameters["as_of_at"] == AS_OF
    assert parameters["symbol"] == "005930"
    assert "created_at <= %(as_of_at)s" in body(statement)


def test_no_signals_is_an_empty_list_not_a_missing_key():
    connection = FakeConnection({"daily_history": domestic_history(), "recent_signals": []})
    box = toolbox(connection)

    payload = json.loads(box.run("daily_history", {"symbol": "005930", "days": 10}))

    assert payload["recent_signals"] == []
    assert box.registry == {}


def test_the_new_tools_do_not_create_evidence():
    """`thesis_evidence`의 근거 종류는 document·disclosure·macro_change 셋 그대로 둔다.

    시장 상태는 인용할 "출처"가 아니라 관측이다. 레지스트리에 넣으면 모델이 그것을
    `evidence_refs`로 인용하고 근거 표에 "코스피 상승 종목 수"가 실린다.
    """
    connection = FakeConnection({"indicators": [indicator_row(Decimal("4.70"), Decimal("4.65"))]})
    box = toolbox(connection)

    box.run("macro_indicators", {"kind": "government_bond"})
    box.run("market_breadth", {})

    assert box.registry == {}


def test_the_new_tools_count_against_the_call_budget():
    """상한을 안 달면 툴이 늘어난 만큼 문맥이 무한정 자란다."""
    connection = FakeConnection({"indicators": []})
    box = toolbox(connection)

    for _ in range(MAX_TOOL_CALLS):
        box.run("macro_indicators", {"kind": "government_bond"})

    with pytest.raises(ToolLimitExceeded, match="상한 초과"):
        box.run("market_breadth", {})


def test_every_tool_call_gets_exactly_one_tool_message():
    connection = FakeConnection({"documents": [], "macro": [macro_row()]})
    reply = AIMessage(
        "",
        tool_calls=[
            {"name": "recent_documents", "args": {"hours": 6, "min_score": 5}, "id": "a"},
            {"name": "macro_changes", "args": {}, "id": "b"},
            {"name": "nope", "args": {}, "id": "c"},
        ],
    )
    model = ScriptedModel(reply, AIMessage(DONE_INVESTIGATING), answer_message(thesis_payload()))
    builder = build(model, connection)

    run_builder(builder)

    tool_messages = [message for message in model.calls[-1] if isinstance(message, ToolMessage)]
    # 빠지거나 둘이면 제공처가 다음 요청을 거절한다. 이 보장은 이제 `ToolNode`가 한다.
    assert [message.tool_call_id for message in tool_messages] == ["a", "b", "c"]
    # 모르는 툴도 예외가 아니라 오류 ToolMessage다. 모델이 고쳐 부를 기회를 준다.
    # 문구는 `ToolNode`의 것이고 쓸 수 있는 툴 이름을 함께 싣는다.
    assert tool_messages[2].status == "error"
    assert "recent_documents" in tool_messages[2].content


def test_the_round_cap_forces_the_answer_step():
    connection = FakeConnection({"documents": []})
    replies = [tool_call_message() for _ in range(MAX_TOOL_ROUNDS + 2)]
    model = ScriptedModel(*replies, answer_message(thesis_payload()))
    builder = build(model, connection)

    _, tool_rounds = run_builder(builder)

    assert tool_rounds == MAX_TOOL_ROUNDS


def test_subjects_outside_the_request_list_are_dropped():
    connection = FakeConnection()
    model = scripted(answer_message(thesis_payload("KOSPI"), thesis_payload("AAPL")))
    builder = build(model, connection)

    drafts, _ = run_builder(builder)

    assert [draft.subject.code for draft in drafts] == ["KOSPI"]


def test_a_subject_answered_twice_is_refused_entirely():
    connection = FakeConnection()
    model = scripted(
        answer_message(thesis_payload("KOSPI"), thesis_payload("KOSPI", prob_up=0.1, prob_down=0.8, prob_flat=0.1)),
        # KOSPI 둘이 다 빠지면 남는 것이 없어 교정이 한 번 돈다.
        answer_message(thesis_payload("000660")),
    )
    builder = build(model, connection)

    drafts, _ = run_builder(builder)

    # 어느 쪽이 진짜인지 알 수 없다. 먼저 넣은 것도 함께 뺀다.
    assert [draft.subject.code for draft in drafts] == ["000660"]


def test_a_missing_subject_is_left_out_and_never_re_requested():
    connection = FakeConnection()
    model = scripted(answer_message(thesis_payload("KOSPI")))
    builder = build(model, connection)

    drafts, _ = run_builder(builder)

    assert [draft.subject.code for draft in drafts] == ["KOSPI"]
    # 조사 한 번, 답변 한 번. 빠진 subject를 다시 묻지 않는다.
    assert len(model.calls) == 2


def test_a_subject_whose_probabilities_do_not_sum_to_one_is_dropped():
    connection = FakeConnection()
    model = scripted(
        answer_message(
            thesis_payload("KOSPI", prob_up=0.3, prob_down=0.3, prob_flat=0.3),
            thesis_payload("000660"),
        )
    )
    builder = build(model, connection)

    drafts, _ = run_builder(builder)

    assert [draft.subject.code for draft in drafts] == ["000660"]


def test_everything_unusable_triggers_exactly_one_repair():
    connection = FakeConnection()
    model = scripted(answer_message(thesis_payload("AAPL")), answer_message(thesis_payload("KOSPI")))
    builder = build(model, connection)

    drafts, _ = run_builder(builder)

    assert [draft.subject.code for draft in drafts] == ["KOSPI"]
    # 조사 한 번, 답변 한 번, 교정 뒤 답변 한 번.
    assert len(model.calls) == 3


def test_a_second_unusable_answer_raises():
    connection = FakeConnection()
    model = scripted(answer_message(thesis_payload("AAPL")), answer_message(thesis_payload("MSFT")))
    builder = build(model, connection)

    with pytest.raises(ThesisError):
        run_builder(builder)


def test_refs_no_tool_returned_are_dropped_and_duplicates_keep_their_first_rank():
    connection = FakeConnection({"documents": [document_row(7), document_row(9)]})
    model = ScriptedModel(
        tool_call_message(),
        AIMessage(DONE_INVESTIGATING),
        answer_message(thesis_payload(refs=["document:9", "document:7", "document:9", "document:404"])),
    )
    builder = build(model, connection)

    drafts, _ = run_builder(builder)

    # 순서가 곧 rank다. 중복은 첫 등장 자리에 합쳐지고 목록 밖 ref는 버려진다.
    assert drafts[0].evidence_refs == ("document:9", "document:7")


def test_a_thesis_with_no_evidence_is_allowed():
    connection = FakeConnection()
    model = scripted(answer_message(thesis_payload(refs=[])))
    builder = build(model, connection)

    drafts, _ = run_builder(builder)

    # 억지 인용이 근거 없음보다 나쁘다.
    assert drafts[0].evidence_refs == ()


def test_each_reasoning_field_is_trimmed_on_its_own():
    connection = FakeConnection()
    long_text = "가" * (MAX_REASONING_CHARS + 200)
    model = scripted(
        answer_message(thesis_payload(up_reasoning=long_text, down_reasoning="짧다", flat_reasoning=long_text))
    )
    builder = build(model, connection)

    drafts, _ = run_builder(builder)

    assert len(drafts[0].up_reasoning) == MAX_REASONING_CHARS
    assert len(drafts[0].flat_reasoning) == MAX_REASONING_CHARS
    assert drafts[0].down_reasoning == "짧다"


# --- 저장 --------------------------------------------------------------------


def stored_row(thesis_id: int = 1, code: str = "KOSPI") -> tuple:
    return (
        thesis_id,
        "pre_open",
        date(2026, 8, 21),
        AS_OF,
        "manual__run",
        "index",
        code,
        "코스피",
        Decimal("0.6200"),
        Decimal("0.2300"),
        Decimal("0.1500"),
        "오를 이유",
        "내릴 이유",
        "횡보 이유",
        1,
        "gpt-5.6-luna",
        PROMPT_VERSION,
    )


def draft_for(builder_connection: FakeConnection) -> Any:
    model = scripted(answer_message(thesis_payload(refs=["document:7"])))
    connection = FakeConnection({"documents": [document_row(7)]})
    box = toolbox(connection)
    box.run("recent_documents", {"hours": 6, "min_score": 0})
    builder = ThesisBuilder(model, box)
    drafts, _ = builder.run(
        run_slot=RunSlot.PRE_OPEN,
        as_of_at=AS_OF,
        subjects=SUBJECTS,
        observed_state=OBSERVED,
        past_theses={},
    )
    return drafts, box.registry


def test_existing_theses_is_what_the_caller_checks_before_paying_for_a_model():
    connection = FakeConnection({"select_by_run": [stored_row()]})

    rows = existing_theses(connection, run_date=date(2026, 8, 21), run_slot=RunSlot.PRE_OPEN)

    assert [row.subject_code for row in rows] == ["KOSPI"]
    assert rows[0].run_slot is RunSlot.PRE_OPEN
    # 채점은 이 행에 없다. thesis_outcome이 지평별로 갖는다.
    assert not hasattr(rows[0], "brier_score")


def test_storing_writes_the_thesis_and_its_evidence_in_one_transaction():
    drafts, registry = draft_for(FakeConnection())
    connection = FakeConnection({"thesis_insert": [(11,)], "select_by_run": [stored_row(11)]})

    store_theses(
        connection,
        run_date=date(2026, 8, 21),
        run_slot=RunSlot.PRE_OPEN,
        as_of_at=AS_OF,
        dag_run_id="manual__run",
        drafts=drafts,
        registry=registry,
        observed_state=OBSERVED,
        llm_model="gpt-5.6-luna",
        tool_rounds=1,
        precedents={},
    )

    kinds = [_statement_key(statement) for statement, _ in connection.calls]
    assert kinds[:2] == ["thesis_insert", "evidence_insert"]
    # 추론만 들어가고 근거가 빠진 상태를 남기지 않는다.
    assert connection.commits == 1
    assert connection.rollbacks == 0


def test_storing_never_updates_and_falls_back_to_the_stored_row_on_conflict():
    drafts, registry = draft_for(FakeConnection())
    # RETURNING이 0행이면 삽입 직전에 다른 실행이 먼저 넣은 것이다.
    connection = FakeConnection({"thesis_insert": [], "select_by_run": [stored_row(11)]})

    rows = store_theses(
        connection,
        run_date=date(2026, 8, 21),
        run_slot=RunSlot.PRE_OPEN,
        as_of_at=AS_OF,
        dag_run_id="manual__run",
        drafts=drafts,
        registry=registry,
        observed_state=OBSERVED,
        llm_model="gpt-5.6-luna",
        tool_rounds=1,
        precedents={},
    )

    kinds = [_statement_key(statement) for statement, _ in connection.calls]
    assert "evidence_insert" not in kinds
    assert [row.id for row in rows] == [11]


def test_the_prompt_states_the_reference_time_in_kst():
    """UTC 그대로 주면 모델이 "오늘"을 하루 어긋나게 읽는다.

    장전 슬롯의 as_of_at은 KST 08:35이고 UTC로는 **전날** 23:35다. 프롬프트에 UTC만
    실리면 "오늘 한국 장이 열리기 전이다"와 날짜가 어긋난다.
    """
    pre_open_as_of = datetime(2026, 8, 20, 23, 35, tzinfo=UTC)
    prompt = ThesisBuilder.build_messages(
        run_slot=RunSlot.PRE_OPEN,
        as_of_at=pre_open_as_of,
        subjects=SUBJECTS,
        observed_state=OBSERVED,
        past_theses={},
    )[1].content

    assert "2026-08-21 08:35 KST" in prompt
    # 툴 결과는 UTC로 남으므로 그 규약을 프롬프트가 직접 알려 준다.
    assert "UTC다" in prompt
    assert "9시간" in prompt


def test_the_narrative_prompt_states_the_reference_time_in_kst():
    prompt = (
        narrator(scripted(), FakeConnection())
        .build_messages(
            run_date=date(2026, 8, 21),
            run_slot=RunSlot.PRE_OPEN,
            horizon_days=1,
            as_of_at=datetime(2026, 8, 24, 6, 30, tzinfo=UTC),
            targets=(narrative_target(),),
        )[1]
        .content
    )

    assert "2026-08-24 15:30 KST" in prompt
    assert "UTC다" in prompt


def test_evidence_ranks_follow_the_citation_order():
    connection = FakeConnection({"documents": [document_row(7), document_row(9)], "macro": [macro_row()]})
    box = toolbox(connection)
    box.run("recent_documents", {"hours": 6, "min_score": 0})
    box.run("macro_changes", {})
    model = scripted(answer_message(thesis_payload(refs=["macro_change:SP500_FUT", "document:9"])))
    builder = ThesisBuilder(model, box)
    drafts, _ = builder.run(
        run_slot=RunSlot.PRE_OPEN, as_of_at=AS_OF, subjects=SUBJECTS, observed_state=OBSERVED, past_theses={}
    )

    writer = FakeConnection({"thesis_insert": [(11,)], "select_by_run": [stored_row(11)]})
    store_theses(
        writer,
        run_date=date(2026, 8, 21),
        run_slot=RunSlot.PRE_OPEN,
        as_of_at=AS_OF,
        dag_run_id="manual__run",
        drafts=drafts,
        registry=box.registry,
        observed_state=OBSERVED,
        llm_model="gpt-5.6-luna",
        tool_rounds=2,
        precedents={},
    )

    # (outcome_horizon_days, evidence_ref, rank). 원 추론의 근거라 지평 칸은 NULL이다.
    ranks = [
        (parameters[1], parameters[3], parameters[7])
        for statement, parameters in writer.calls
        if _statement_key(statement) == "evidence_insert"
    ]
    assert ranks == [(None, "macro_change:SP500_FUT", 1), (None, "document:9", 2)]


def test_the_airflow_enums_match_the_backend_vocabulary():
    from apps.models import analysis

    for airflow_enum, backend_enum in (
        (RunSlot, analysis.RunSlot),
        (ThesisSubjectKind, analysis.ThesisSubjectKind),
        (ThesisDirection, analysis.ThesisDirection),
        (ThesisVerdict, analysis.ThesisVerdict),
        (ThesisEvidenceKind, analysis.ThesisEvidenceKind),
    ):
        assert {member.value for member in airflow_enum} == {member.value for member in backend_enum}


# --- 사후 해설 ---------------------------------------------------------------

REVIEW_AS_OF = datetime(2026, 8, 24, 6, 30, tzinfo=UTC)


def narrative_target(code: str = "KOSPI", **overrides: Any) -> NarrativeTarget:
    values: dict[str, Any] = {
        "thesis_id": 11 if code == "KOSPI" else 12,
        "run_slot": RunSlot.PRE_OPEN,
        "subject": next(s for s in SUBJECTS if s.code == code),
        "prob_up": Decimal("0.6200"),
        "prob_down": Decimal("0.2300"),
        "prob_flat": Decimal("0.1500"),
        "up_reasoning": "밤사이 미국 지수가 올랐다",
        "down_reasoning": "공시가 수급을 눌렀다",
        "flat_reasoning": "재료가 상쇄됐다",
        "actual_return_pct": Decimal("-4.0000"),
        "actual_outcome": ThesisDirection.DOWN,
        "brier_score": Decimal("0.14000"),
    }
    values.update(overrides)
    return NarrativeTarget(**values)


def narrative_message(*items: dict[str, Any]) -> AIMessage:
    return AIMessage(json.dumps({"narratives": list(items)}))


def narrative_payload(code: str = "KOSPI", **overrides: Any) -> dict[str, Any]:
    payload = {
        "subject_code": code,
        "narrative": "이 기사들은 금리 급등을 원인으로 본다",
        "verdict": "unresolved",
        "evidence_refs": [],
    }
    payload.update(overrides)
    return payload


def narrator(model: ScriptedModel, connection: FakeConnection, *, include_outcome: bool = True) -> FollowupNarrator:
    return FollowupNarrator(model, toolbox(connection), include_outcome=include_outcome)


def run_narrator(built: FollowupNarrator, targets: tuple[NarrativeTarget, ...] | None = None) -> Any:
    return built.run(
        run_date=date(2026, 8, 21),
        horizon_days=1,
        as_of_at=REVIEW_AS_OF,
        targets=targets if targets is not None else (narrative_target(),),
    )


def test_the_prompt_variant_decides_whether_the_result_is_shown():
    connection = FakeConnection()
    informed = narrator(scripted(), connection, include_outcome=True)
    blind = narrator(scripted(), connection, include_outcome=False)
    target = narrative_target()

    shown = informed.build_messages(
        run_date=date(2026, 8, 21),
        run_slot=RunSlot.PRE_OPEN,
        horizon_days=1,
        as_of_at=REVIEW_AS_OF,
        targets=(target,),
    )[1].content
    hidden = blind.build_messages(
        run_date=date(2026, 8, 21),
        run_slot=RunSlot.PRE_OPEN,
        horizon_days=1,
        as_of_at=REVIEW_AS_OF,
        targets=(target,),
    )[1].content

    assert "실제 결과" in shown
    assert "-4.00%" in shown
    # blind는 결과를 못 본다. 다만 후속 기사가 등락을 싣고 있어 완전한 차단은 아니다
    # (docs/market-thesis/5-followup.md 12절 실측).
    assert "실제 결과" not in hidden
    assert "-4.00%" not in hidden
    # 원 추론의 확률과 이유는 양쪽 다 본다.
    for body_text in (shown, hidden):
        assert "밤사이 미국 지수가 올랐다" in body_text


def test_the_variant_travels_in_the_prompt_version():
    connection = FakeConnection()

    assert narrator(scripted(), connection, include_outcome=True).prompt_revision.endswith("/informed")
    assert narrator(scripted(), connection, include_outcome=False).prompt_revision.endswith("/blind")


@pytest.mark.parametrize("verdict", ["supported", "contradicted"])
def test_a_verdict_without_evidence_is_downgraded(verdict):
    connection = FakeConnection({"documents": [document_row(7)]})
    model = scripted(narrative_message(narrative_payload(verdict=verdict, evidence_refs=[])))
    built = narrator(model, connection)

    drafts = run_narrator(built)

    # 프롬프트 규칙만으로는 역산을 못 막는다. 이 검사가 막는다.
    assert drafts[0].verdict is ThesisVerdict.UNRESOLVED


def test_a_verdict_with_evidence_survives():
    connection = FakeConnection({"documents": [document_row(7)]})
    box = toolbox(connection)
    box.run("recent_documents", {"hours": 6, "min_score": 0})
    model = scripted(narrative_message(narrative_payload(verdict="contradicted", evidence_refs=["document:7"])))
    built = FollowupNarrator(model, box)

    drafts = built.run(
        run_date=date(2026, 8, 21),
        horizon_days=1,
        as_of_at=REVIEW_AS_OF,
        targets=(narrative_target(),),
    )

    assert drafts[0].verdict is ThesisVerdict.CONTRADICTED
    assert drafts[0].evidence_refs == ("document:7",)


def test_the_prompt_names_the_slot_the_targets_came_from():
    """2026-08-23까지 장후 리뷰의 해설도 "장전 전망에 쓴 추론"이라고 모델에게 말했다."""
    model = scripted(narrative_message(narrative_payload()))
    built = narrator(model, FakeConnection())

    built.run(
        run_date=date(2026, 8, 21),
        horizon_days=1,
        as_of_at=REVIEW_AS_OF,
        targets=(narrative_target(run_slot=RunSlot.POST_CLOSE),),
    )

    prompt = model.calls[0][1].content
    assert prompt.startswith("2026-08-21 장후 리뷰에 쓴 추론을")
    assert "장전" not in prompt


def test_a_narration_call_refuses_mixed_slots():
    """같은 날 장전·장후 추론이 같은 대상을 갖는다. 섞이면 응답을 대상에 되돌릴 수 없다."""
    built = narrator(scripted(), FakeConnection())

    with pytest.raises(ThesisError, match="span 2 slots"):
        built.run(
            run_date=date(2026, 8, 21),
            horizon_days=1,
            as_of_at=REVIEW_AS_OF,
            targets=(narrative_target(), narrative_target(run_slot=RunSlot.POST_CLOSE, thesis_id=21)),
        )


def test_unresolved_needs_no_evidence():
    connection = FakeConnection()
    model = scripted(narrative_message(narrative_payload(verdict="unresolved")))

    drafts = run_narrator(narrator(model, connection))

    assert drafts[0].verdict is ThesisVerdict.UNRESOLVED
    assert drafts[0].evidence_refs == ()


def test_a_long_narrative_is_trimmed():
    connection = FakeConnection()
    model = scripted(narrative_message(narrative_payload(narrative="가" * (MAX_NARRATIVE_CHARS + 200))))

    drafts = run_narrator(narrator(model, connection))

    assert len(drafts[0].narrative) == MAX_NARRATIVE_CHARS


def test_narratives_outside_the_target_list_are_dropped():
    connection = FakeConnection()
    model = scripted(narrative_message(narrative_payload("KOSPI"), narrative_payload("AAPL")))

    drafts = run_narrator(narrator(model, connection), (narrative_target("KOSPI"),))

    assert [d.subject_code for d in drafts] == ["KOSPI"]


def test_every_target_unusable_triggers_one_repair_then_raises():
    connection = FakeConnection()
    model = scripted(narrative_message(narrative_payload("AAPL")), narrative_message(narrative_payload("MSFT")))

    with pytest.raises(ThesisError):
        run_narrator(narrator(model, connection), (narrative_target("KOSPI"),))


# --- past_theses 툴 ----------------------------------------------------------


def past_thesis_row(run_date: date = date(2026, 8, 20)) -> tuple:
    return (
        7,
        run_date,
        Decimal("0.6200"),
        Decimal("0.2300"),
        Decimal("0.1500"),
        "오를 이유",
        "내릴 이유",
        "횡보 이유",
        [{"horizon_days": 1, "actual_outcome": "down", "brier_score": "0.14", "verdict": "contradicted"}],
    )


def test_past_theses_refuses_a_subject_outside_this_run():
    connection = FakeConnection({"past": [past_thesis_row()]})
    box = ThesisToolbox(
        connection,
        as_of_at=AS_OF,
        macro_window_start=MACRO_WINDOW_START,
        watched_codes=["005930"],
        subject_codes=["KOSPI"],
    )

    # 모델이 아무 종목이나 조회하며 문맥을 채우게 두지 않는다.
    with pytest.raises(ToolLimitExceeded, match="대상 목록 밖"):
        box.run("past_theses", {"subject_code": "AAPL", "n": 3})


def test_past_theses_is_unavailable_without_a_subject_list():
    connection = FakeConnection()
    box = ThesisToolbox(connection, as_of_at=AS_OF, macro_window_start=MACRO_WINDOW_START, watched_codes=["005930"])

    with pytest.raises(ToolLimitExceeded, match="대상 목록이 없어"):
        box.run("past_theses", {"subject_code": "KOSPI", "n": 3})


@pytest.mark.parametrize(("given", "expected"), [(0, 1), (11, 10), (3, 3), ("bad", 1)])
def test_past_theses_clamps_its_count(given, expected):
    connection = FakeConnection({"past": []})
    box = ThesisToolbox(
        connection,
        as_of_at=AS_OF,
        macro_window_start=MACRO_WINDOW_START,
        watched_codes=["005930"],
        subject_codes=["KOSPI"],
    )

    box.run("past_theses", {"subject_code": "KOSPI", "n": given})

    _, parameters = connection.calls[0]
    assert parameters[0] == AS_OF
    assert parameters[2] == expected


def test_past_theses_results_never_become_evidence():
    connection = FakeConnection({"past": [past_thesis_row()]})
    box = ThesisToolbox(
        connection,
        as_of_at=AS_OF,
        macro_window_start=MACRO_WINDOW_START,
        watched_codes=["005930"],
        subject_codes=["KOSPI"],
    )

    body_text = box.run("past_theses", {"subject_code": "KOSPI", "n": 3})

    # 자기 과거 추론은 근거가 아니다. 근거 종류는 셋 그대로 둔다.
    assert box.registry == {}
    assert "contradicted" in body_text


def test_past_theses_cuts_its_window_at_the_slot_time():
    query = body(PAST_THESES)

    # 없으면 장전 슬롯을 오후에 재실행할 때 그날 저녁의 채점이 아침 예측에 섞인다.
    assert "run_slot = 'pre_open'" in query
    assert "outcome.evaluated_at <= bounds.as_of_at" in query
    assert "outcome.narrative_at <= bounds.as_of_at" in query
    assert "thesis.run_date < (bounds.as_of_at AT TIME ZONE 'Asia/Seoul')::date" in query


# --- 해설 저장 ---------------------------------------------------------------


def test_pending_narratives_covers_both_slots():
    query = body(PENDING_NARRATIVES)

    # post_close 추론은 채점을 안 받아 thesis_outcome 행이 없다. INNER JOIN이면 영영 빠진다.
    assert "LEFT JOIN thesis_outcome" in query
    assert "outcome.narrative IS NULL" in query
    assert "run_slot = 'pre_open'" not in query


def test_pending_narratives_carry_their_slot():
    """같은 대상의 장전·장후 행이 함께 온다. 슬롯이 없으면 부르는 쪽이 둘을 가를 수 없다."""

    def row(thesis_id: int, slot: str) -> tuple:
        return (thesis_id, date(2026, 8, 21), slot, "index", "KOSPI", "코스피", 0.6, 0.3, 0.1, "u", "d", "f", None, None, None)

    connection = FakeConnection({"select_by_run": [row(11, "post_close"), row(12, "pre_open")]})

    targets = pending_narratives(connection, run_date=date(2026, 8, 21), horizon_days=1)

    assert [(t.thesis_id, t.run_slot) for t in targets] == [(11, RunSlot.POST_CLOSE), (12, RunSlot.PRE_OPEN)]


def test_the_after_hours_query_isolates_the_nxt_evening():
    """NXT는 프리·주간도 체결한다. 거래소만 걸면 하루 전체가 섞인다."""
    query = body(NXT_AFTER_HOURS)

    assert "bar.exchange = 'NXT'" in query
    # 창의 양 끝이 파라미터다. KST 경계 계산은 파이썬이 한다.
    assert "bar.bar_at >= bounds.window_start" in query
    assert "bar.bar_at <= bounds.window_end" in query


def test_the_after_hours_return_is_measured_from_the_settled_close():
    """분모가 `previous_close`면 전일 대비가 된다 — 애프터 등락이 하루 등락으로 부풀려진다."""
    query = body(NXT_AFTER_HOURS)

    assert "settled.close_price" in query
    assert "LEFT JOIN stock_investor_trade_daily AS settled" in query
    assert "previous_close" not in query
    # 확정 종가가 없으면 0으로 꾸미지 않고 NULL로 둔다.
    assert "WHEN settled.close_price IS NULL OR settled.close_price = 0 THEN NULL" in query


def test_the_after_hours_query_reports_its_own_completeness():
    """봉 수와 확정 여부가 readiness guard의 판정 둘을 만든다."""
    query = body(NXT_AFTER_HOURS)

    assert "bar_count" in query
    assert "bool_and(bar.is_final)" in query


def test_narratives_and_backlog_watch_the_same_slots():
    """해설을 만드는 슬롯과 밀림을 세는 슬롯이 같아야 한다.

    어긋나면 한쪽은 해설을 안 만들고 다른 쪽은 그것을 밀림으로 세서, ops 브리핑의
    `unnarrated`가 영영 줄지 않는 거짓 경보가 된다. 슬롯이 또 늘 때 이 테스트가 먼저 깨진다.
    """
    narratives = body(PENDING_NARRATIVES)
    backlog = body(THESIS_BACKLOG)
    narrated_slots = "IN ('pre_open', 'post_close')"

    assert narrated_slots in narratives
    assert narrated_slots in backlog
    # NXT 애프터마켓 리뷰는 아직 해설 루프 밖이다(`docs/market-thesis/7-nxt-review.md` 3절).
    assert "post_nxt_close" not in narratives
    assert "post_nxt_close" not in backlog


def test_the_narrative_write_never_overwrites():
    statement = body(INSERT_NARRATIVE)

    assert "ON CONFLICT ON CONSTRAINT uq_thesis_outcome_natural_key DO UPDATE" in statement
    assert "WHERE thesis_outcome.narrative IS NULL" in statement
    # 채점 칸은 해설이 건드리지 않는다.
    assert not set(inserted_columns(INSERT_NARRATIVE)) & {"evaluated_at", "actual_outcome", "brier_score"}
    assert set(inserted_columns(INSERT_NARRATIVE)) <= {c.name for c in ThesisOutcome.__table__.columns}


def test_storing_a_narrative_writes_its_evidence_in_the_same_transaction():
    connection = FakeConnection({"documents": [document_row(7)]})
    box = toolbox(connection)
    box.run("recent_documents", {"hours": 6, "min_score": 0})
    writer = FakeConnection({"narrative_insert": [(1,)]})
    writer.rowcount = 1
    draft = NarrativeDraft(
        thesis_id=11,
        subject_code="KOSPI",
        narrative="해설",
        verdict=ThesisVerdict.CONTRADICTED,
        evidence_refs=("document:7",),
    )

    stored = store_narratives(
        writer,
        horizon_days=1,
        as_of_at=REVIEW_AS_OF,
        dag_run_id="manual__run",
        drafts=[draft],
        registry=box.registry,
        llm_model="grok-4.6",
        prompt_revision="1/informed",
    )

    kinds = [_statement_key(statement) for statement, _ in writer.calls]
    assert stored == 1
    assert kinds == ["narrative_insert", "evidence_insert"]
    # 근거는 그 지평의 것으로 표시된다.
    assert writer.calls[1][1][1] == 1
    assert writer.commits == 1


def test_an_already_written_narrative_gets_no_new_evidence():
    connection = FakeConnection({"documents": [document_row(7)]})
    box = toolbox(connection)
    box.run("recent_documents", {"hours": 6, "min_score": 0})
    writer = FakeConnection()
    # SQL의 WHERE가 막아 0행이 갱신된 상황.
    writer.rowcount = 0
    draft = NarrativeDraft(
        thesis_id=11,
        subject_code="KOSPI",
        narrative="해설",
        verdict=ThesisVerdict.CONTRADICTED,
        evidence_refs=("document:7",),
    )

    stored = store_narratives(
        writer,
        horizon_days=1,
        as_of_at=REVIEW_AS_OF,
        dag_run_id="manual__run",
        drafts=[draft],
        registry=box.registry,
        llm_model="grok-4.6",
        prompt_revision="1/informed",
    )

    kinds = [_statement_key(statement) for statement, _ in writer.calls]
    assert stored == 0
    # 근거를 덧붙이면 그 해설과 어긋난 인용이 남는다.
    assert "evidence_insert" not in kinds


@pytest.mark.parametrize("horizon", [0, 2, 7])
def test_a_horizon_that_takes_no_narrative_is_refused(horizon):
    # 지평 0은 그날의 후속 보도가 아직 쌓이지 않아 해설을 쓸 재료가 없다.
    with pytest.raises(ThesisError, match="does not take a narrative"):
        store_narratives(
            FakeConnection(),
            horizon_days=horizon,
            as_of_at=REVIEW_AS_OF,
            dag_run_id="manual__run",
            drafts=[],
            registry={},
            llm_model="grok-4.6",
            prompt_revision="1/informed",
        )


def test_the_airflow_horizons_match_the_backend_lists():
    from apps.models import analysis

    # Airflow는 apps/를 보지 못해 목록을 한 벌 더 든다. 어긋나면 코드가 저장하려는 지평을
    # DB CHECK가 거절한다.
    assert set(HORIZON_DAYS) == set(analysis.THESIS_HORIZON_DAYS)
    assert set(NARRATED_HORIZON_DAYS) == set(analysis.NARRATED_HORIZON_DAYS)


def test_evidence_refs_are_built_from_the_kind_itself():
    item = Evidence(
        kind=ThesisEvidenceKind.MACRO_CHANGE, ref=evidence_ref(ThesisEvidenceKind.MACRO_CHANGE, "US10Y"), title="x"
    )

    assert item.ref == "macro_change:US10Y"
    assert item.ref.split(":", 1)[0] == item.kind.value


def test_the_character_budget_constant_leaves_room_for_the_answer_step():
    # 컨텍스트가 근거로 가득 차면 답변 단계에 쓸 자리가 없다.
    assert MAX_TOOL_RESULT_CHARS < 32_000


# --- Slack 렌더링 -------------------------------------------------------------


def stored_thesis(thesis_id: int = 1, code: str = "KOSPI", label: str = "코스피") -> StoredThesis:
    return StoredThesis(
        id=thesis_id,
        run_slot=RunSlot.PRE_OPEN,
        run_date=date(2026, 8, 21),
        as_of_at=AS_OF,
        dag_run_id="manual__run",
        subject_kind=ThesisSubjectKind.INDEX,
        subject_code=code,
        label=label,
        prob_up=Decimal("0.6200"),
        prob_down=Decimal("0.2300"),
        prob_flat=Decimal("0.1500"),
        up_reasoning="오를 이유",
        down_reasoning="내릴 이유",
        flat_reasoning="횡보 이유",
        tool_rounds=2,
        llm_model="grok-4.6",
        prompt_version=PROMPT_VERSION,
    )


def linked_evidence() -> tuple[StoredEvidence, ...]:
    return (
        StoredEvidence(thesis_id=1, evidence_title="기사", evidence_url="https://x.test/1", rank=1),
        # 매크로 변화는 링크할 곳이 없다.
        StoredEvidence(thesis_id=1, evidence_title="S&P500 선물 +0.8%", rank=2),
    )


def _texts(built: list[dict[str, Any]]) -> list[str]:
    """블록에 실린 글자 전부. `context`는 `elements` 안에 있어 따로 꺼낸다."""
    collected = []
    for block in built:
        collected.append((block.get("text") or {}).get("text", ""))
        collected += [element.get("text", "") for element in block.get("elements", [])]
    return collected


def test_the_slack_message_shows_all_three_directions():
    built = render_blocks(RunSlot.PRE_OPEN, date(2026, 8, 21), [stored_thesis()], {1: linked_evidence()})

    body = "\n".join(_texts(built))
    # 사용자가 요청한 "오를 확률/이유, 내릴 확률/이유, 횡보 확률/이유" 그대로다.
    for piece in ("상승 62%", "하락 23%", "횡보 15%", "오를 이유", "내릴 이유", "횡보 이유"):
        assert piece in body
    # 가장 높은 확률만 굵게 한다. 순서는 ▲▼– 로 고정이라 눈이 매번 다시 읽지 않는다.
    assert "*▲ 상승 62%*" in body
    assert "*▼ 하락 23%*" not in body


def test_only_evidence_with_a_url_becomes_a_link():
    built = render_blocks(RunSlot.PRE_OPEN, date(2026, 8, 21), [stored_thesis()], {1: linked_evidence()})

    body = "\n".join(_texts(built))
    # 근거는 context 블록으로 내려 본문보다 작게 그려진다.
    assert "📎 <https://x.test/1|기사>" in body
    assert "· S&P500 선물 +0.8%" in body


def test_no_evidence_says_so_rather_than_leaving_a_blank():
    built = render_blocks(RunSlot.PRE_OPEN, date(2026, 8, 21), [stored_thesis()], {})

    # 억지 인용보다 근거 없음이 낫다는 판단의 결과라 그렇게 적는다.
    assert "📎 근거 없음 — 관측 상태만으로 추론" in "\n".join(_texts(built))


def test_an_empty_run_says_there_is_nothing():
    built = render_blocks(RunSlot.POST_CLOSE, date(2026, 8, 21), [], {})

    assert "남은 추론이 없다" in "\n".join(_texts(built))
    assert render_text(RunSlot.POST_CLOSE, date(2026, 8, 21), []).endswith("추론 결과 없음")


def test_the_header_names_the_slot():
    morning = render_blocks(RunSlot.PRE_OPEN, date(2026, 8, 21), [], {})[0]
    evening = render_blocks(RunSlot.POST_CLOSE, date(2026, 8, 21), [], {})[0]

    assert "장전 전망" in morning["text"]["text"]
    assert "장후 리뷰" in evening["text"]["text"]


def test_the_market_message_carries_no_grading(self_check=None):
    """채점·해설은 시장 메시지에 없다(2026-08-21 결정).

    읽는 사람이 다르다 — 오늘 전망은 시장을 보는 사람이 읽고, "우리 추론이 잘 맞고 있나"는
    운영자가 본다. 지표는 `slack_ops_briefing`이 낸다.
    """
    built = render_blocks(RunSlot.PRE_OPEN, date(2026, 8, 21), [stored_thesis()], {1: linked_evidence()})

    body = "\n".join(_texts(built))
    for piece in ("Brier", "되돌아보기", "판정", "지지됨", "반박됨"):
        assert piece not in body


def test_the_slack_message_stays_inside_the_block_budget():
    theses = [stored_thesis(index, f"CODE{index}", f"이름{index}") for index in range(1, 11)]

    built = render_blocks(RunSlot.PRE_OPEN, date(2026, 8, 21), theses, {})

    # Slack은 메시지당 블록 50개다. 대상이 늘어 가까워지면 메시지를 나눈다.
    assert len(built) <= 50


# --- 과거 추론 프리페치와 thesis_precedent ---------------------------------------


def test_thesis_precedent_insert_matches_the_model():
    table = ThesisPrecedent.__table__
    columns = inserted_columns(PRECEDENT_INSERT)

    assert set(columns) <= {column.name for column in table.columns}
    assert required_columns(table) <= set(columns)
    assert placeholder_count(PRECEDENT_INSERT) == len(columns)
    assert "DO UPDATE" not in PRECEDENT_INSERT


def test_past_theses_carries_the_id_the_edge_needs():
    connection = FakeConnection({"past": [past_thesis_row()]})

    rows = past_theses(connection, as_of_at=AS_OF, subject_code="KOSPI", n=PREFETCHED_PAST_THESES)

    # 프롬프트에 실리는 행과 엣지 끝이 같은 조회에서 나온다. 따로 조회하면 둘이 어긋날 수 있다.
    assert [row.id for row in rows] == [7]
    assert rows[0].outcomes[0].verdict == "contradicted"
    assert rows[0].run_date == date(2026, 8, 20)
    _, parameters = connection.calls[0]
    assert parameters == (AS_OF, "KOSPI", PREFETCHED_PAST_THESES)


def test_past_theses_zero_is_the_off_switch():
    connection = FakeConnection({"past": [past_thesis_row()]})

    assert past_theses(connection, as_of_at=AS_OF, subject_code="KOSPI", n=0) == []
    # 조회조차 하지 않는다. 상수 하나를 0으로 두면 프롬프트 절과 엣지가 같이 꺼진다.
    assert connection.calls == []


def test_the_prompt_carries_the_past_theses_it_was_given():
    source = FakeConnection({"past": [past_thesis_row()]})
    past = {"KOSPI": past_theses(source, as_of_at=AS_OF, subject_code="KOSPI", n=3)}

    prompt = ThesisBuilder.build_messages(
        run_slot=RunSlot.PRE_OPEN,
        as_of_at=AS_OF,
        subjects=SUBJECTS,
        observed_state=OBSERVED,
        past_theses=past,
    )[1].content

    assert "## 과거 추론과 결과" in prompt
    assert '"run_date": "2026-08-20"' in prompt
    assert "contradicted" in prompt
    # 해설은 사실이 아니라 그때의 해석이라고 프롬프트가 직접 말한다(사후확신 순환 방지).
    assert "그때의 해석" in prompt


def test_the_prompt_keeps_the_section_when_there_is_nothing_to_show():
    prompt = ThesisBuilder.build_messages(
        run_slot=RunSlot.POST_CLOSE,
        as_of_at=AS_OF,
        subjects=SUBJECTS,
        observed_state=OBSERVED,
        past_theses={"KOSPI": []},
    )[1].content

    # 절을 빼면 프롬프트 모양이 슬롯마다 달라진다. 빈 목록도 "(없음)"으로 같은 자리에 둔다.
    assert "## 과거 추론과 결과" in prompt
    assert "(없음)" in prompt
    assert "```json\n{}" not in prompt


def test_storing_writes_the_precedent_edges_in_the_same_transaction():
    drafts, registry = draft_for(FakeConnection())
    connection = FakeConnection({"thesis_insert": [(11,)], "select_by_run": [stored_row(11)]})

    store_theses(
        connection,
        run_date=date(2026, 8, 21),
        run_slot=RunSlot.PRE_OPEN,
        as_of_at=AS_OF,
        dag_run_id="manual__run",
        drafts=drafts,
        registry=registry,
        observed_state=OBSERVED,
        llm_model="grok-4.6",
        tool_rounds=1,
        precedents={"KOSPI": [7, 5]},
    )

    edges = [
        parameters for statement, parameters in connection.calls if _statement_key(statement) == "precedent_insert"
    ]
    # 새 thesis id 11에서 보여 준 과거 추론 둘로 나가는 엣지 둘. 추론과 같은 커밋이다.
    assert edges == [(11, 7), (11, 5)]
    assert connection.commits == 1


def test_a_thesis_that_already_existed_gets_no_new_edges():
    drafts, registry = draft_for(FakeConnection())
    connection = FakeConnection({"thesis_insert": [], "select_by_run": [stored_row(11)]})

    store_theses(
        connection,
        run_date=date(2026, 8, 21),
        run_slot=RunSlot.PRE_OPEN,
        as_of_at=AS_OF,
        dag_run_id="manual__run",
        drafts=drafts,
        registry=registry,
        observed_state=OBSERVED,
        llm_model="grok-4.6",
        tool_rounds=1,
        precedents={"KOSPI": [7]},
    )

    # 첫 성공본 불변. 행이 이미 있으면 근거도 엣지도 덧붙이지 않는다.
    assert all(_statement_key(statement) != "precedent_insert" for statement, _ in connection.calls)


# --- 인용의 방향과 경로(claims) -------------------------------------------------


def test_each_claim_keeps_its_direction_and_mechanism():
    connection = FakeConnection({"documents": [document_row(7), document_row(9)]})
    model = ScriptedModel(
        tool_call_message(),
        AIMessage(DONE_INVESTIGATING),
        answer_message(
            thesis_payload(
                claims=[
                    claim_payload("document:9", "down", "외국인 매도 압력"),
                    claim_payload("document:7", "up", "실적 기대"),
                ]
            )
        ),
    )
    builder = build(model, connection)

    drafts, _ = run_builder(builder)

    # 산문 이유와 별개로 근거마다 "어느 쪽으로, 왜"가 남는다. 이것이 그래프 엣지 속성이다.
    assert [(c.ref, c.direction, c.mechanism) for c in drafts[0].claims] == [
        ("document:9", ThesisDirection.DOWN, "외국인 매도 압력"),
        ("document:7", ThesisDirection.UP, "실적 기대"),
    ]
    assert drafts[0].evidence_refs == ("document:9", "document:7")


def test_a_repeated_ref_keeps_its_first_claim():
    connection = FakeConnection({"documents": [document_row(7)]})
    model = ScriptedModel(
        tool_call_message(),
        AIMessage(DONE_INVESTIGATING),
        answer_message(
            thesis_payload(
                claims=[claim_payload("document:7", "up", "첫 번째"), claim_payload("document:7", "down", "두 번째")]
            )
        ),
    )
    builder = build(model, connection)

    drafts, _ = run_builder(builder)

    # 행이 ref당 하나라 방향 둘을 담을 수 없다. 첫 것이 남고 rank도 하나다.
    assert [(c.direction, c.mechanism) for c in drafts[0].claims] == [(ThesisDirection.UP, "첫 번째")]


def test_a_long_mechanism_is_trimmed_on_its_own():
    connection = FakeConnection({"documents": [document_row(7)]})
    model = ScriptedModel(
        tool_call_message(),
        AIMessage(DONE_INVESTIGATING),
        answer_message(thesis_payload(claims=[claim_payload("document:7", "up", "가" * (MAX_MECHANISM_CHARS + 50))])),
    )
    builder = build(model, connection)

    drafts, _ = run_builder(builder)

    # 경로 한 문장 때문에 인용을 버리지 않는다. 그 칸만 자른다.
    assert len(drafts[0].claims[0].mechanism) == MAX_MECHANISM_CHARS
    assert drafts[0].claims[0].mechanism.endswith("…")


def test_a_claim_with_an_unknown_direction_fails_the_whole_answer():
    connection = FakeConnection({"documents": [document_row(7)]})
    model = ScriptedModel(
        tool_call_message(),
        AIMessage(DONE_INVESTIGATING),
        answer_message(thesis_payload(claims=[claim_payload("document:7", "sideways")])),
        answer_message(thesis_payload(claims=[claim_payload("document:7", "sideways")])),
    )
    builder = build(model, connection)

    # 방향은 닫힌 집합이다. 스키마가 막고, 교정 한 번 뒤에도 틀리면 ThesisError다.
    with pytest.raises(ThesisError):
        run_builder(builder)


def test_stored_evidence_carries_the_claim_and_narrative_citations_do_not():
    drafts, registry = draft_for(FakeConnection())
    connection = FakeConnection({"thesis_insert": [(11,)], "select_by_run": [stored_row(11)]})

    store_theses(
        connection,
        run_date=date(2026, 8, 21),
        run_slot=RunSlot.PRE_OPEN,
        as_of_at=AS_OF,
        dag_run_id="manual__run",
        drafts=drafts,
        registry=registry,
        observed_state=OBSERVED,
        llm_model="grok-4.6",
        tool_rounds=1,
        precedents={},
    )

    rows = [parameters for statement, parameters in connection.calls if _statement_key(statement) == "evidence_insert"]
    assert len(rows) == 1
    # 마지막 두 칸이 direction·mechanism이다. 원 추론의 인용이라 둘 다 채워진다.
    assert rows[0][-2:] == ("up", "수급 경로")


def test_narrative_citations_leave_direction_and_mechanism_empty():
    connection = FakeConnection()
    registry = {
        "document:7": Evidence(
            kind=ThesisEvidenceKind.DOCUMENT, ref=evidence_ref(ThesisEvidenceKind.DOCUMENT, "7"), title="t"
        )
    }

    store_narratives(
        connection,
        horizon_days=1,
        as_of_at=AS_OF,
        dag_run_id="manual__run",
        drafts=(
            NarrativeDraft(
                thesis_id=11,
                subject_code="KOSPI",
                narrative="해설",
                verdict=ThesisVerdict.SUPPORTED,
                evidence_refs=("document:7",),
            ),
        ),
        registry=registry,
        llm_model="grok-4.6",
        prompt_revision="1/informed",
    )

    rows = [parameters for statement, parameters in connection.calls if _statement_key(statement) == "evidence_insert"]
    # 해설의 인용은 "어느 쪽으로 썼나"가 없다. CHECK가 쌍을 강제하므로 둘 다 NULL이어야 들어간다.
    assert rows[0][-2:] == (None, None)
    assert rows[0][1] == 1
