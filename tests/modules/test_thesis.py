import json
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Self

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from sqlalchemy import Table

from apps.models.analysis import Thesis, ThesisEvidence, ThesisOutcome
from modules.sql import read_sql
from modules.thesis import (
    DART_VIEWER_URL,
    FLAT_THRESHOLD_PCT,
    HORIZON_DAYS,
    MAX_ITEM_DETAIL_CHARS,
    MAX_NARRATIVE_CHARS,
    MAX_REASONING_CHARS,
    MAX_TOOL_CALLS,
    MAX_TOOL_RESULT_CHARS,
    MAX_TOOL_ROUNDS,
    NARRATED_HORIZON_DAYS,
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
    render_blocks,
    render_text,
    store_narratives,
    store_theses,
)

THESIS_INSERT = read_sql("postgres", "thesis", "insert.sql")
THESIS_SELECT_BY_RUN = read_sql("postgres", "thesis", "select_by_run.sql")
PENDING_GRADES = read_sql("postgres", "thesis_outcome", "select_pending_grades.sql")
INSERT_GRADE = read_sql("postgres", "thesis_outcome", "insert_grade.sql")
OUTCOME_SELECT_BY_IDS = read_sql("postgres", "thesis_outcome", "select_by_thesis_ids.sql")
NTH_OPEN_DAY = read_sql("postgres", "market_session", "select_nth_open_day.sql")
STOCK_HORIZON_RETURN = read_sql("postgres", "stock_investor_trade_daily", "select_horizon_return.sql")
INDEX_HORIZON_RETURN = read_sql("postgres", "index_bar", "select_horizon_return.sql")
EVIDENCE_INSERT = read_sql("postgres", "thesis_evidence", "insert.sql")
EVIDENCE_SELECT_ALL = read_sql("postgres", "thesis_evidence", "select_by_thesis_ids.sql")
EVIDENCE_SELECT_TOP = read_sql("postgres", "thesis_evidence", "select_top_by_thesis_ids.sql")
STOCK_SESSION_RETURN = read_sql("postgres", "stock_investor_trade_daily", "select_session_return.sql")
INDEX_SESSION_RETURN = read_sql("postgres", "index_bar", "select_session_return.sql")
TOOL_DOCUMENTS = read_sql("postgres", "document", "select_recent_top.sql")
TOOL_DISCLOSURES = read_sql("postgres", "disclosure_event", "select_recent.sql")
TOOL_WINDOW_CHANGES = read_sql("postgres", "quote_bar", "select_window_changes.sql")
PAST_THESES = read_sql("postgres", "thesis", "select_past_with_outcomes.sql")
PENDING_NARRATIVES = read_sql("postgres", "thesis_outcome", "select_pending_narratives.sql")
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
        self._connection.calls.append((statement, tuple(parameters)))
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
        self.calls: list[tuple[str, tuple]] = []
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
    if query.startswith("INSERT INTO thesis_outcome"):
        return "narrative_insert" if "narrative" in query else "grade_insert"
    if query.startswith("INSERT INTO thesis"):
        return "thesis_insert"
    if "FROM thesis\nCROSS JOIN bounds" in query:
        return "past"
    if "FROM document" in query:
        return "documents"
    if "FROM disclosure_event" in query:
        return "disclosures"
    if "FROM quote_bar" in query:
        return "macro"
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


def toolbox(connection: FakeConnection) -> ThesisToolbox:
    return ThesisToolbox(
        connection,
        as_of_at=AS_OF,
        macro_window_start=MACRO_WINDOW_START,
        watched_codes=["005930", "000660"],
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
OBSERVED = {"KOSPI": {"prev_return_pct": -2.1}}


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


def thesis_payload(code: str = "KOSPI", refs: list[str] | None = None, **overrides: Any) -> dict[str, Any]:
    payload = {
        "subject_code": code,
        "prob_up": 0.62,
        "prob_down": 0.23,
        "prob_flat": 0.15,
        "up_reasoning": "밤사이 미국 지수가 올랐다",
        "down_reasoning": "공시가 수급을 눌렀다",
        "flat_reasoning": "재료가 상쇄됐다",
        "evidence_refs": refs if refs is not None else [],
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
    # 빠지거나 둘이면 제공처가 다음 요청을 거절한다.
    assert [message.tool_call_id for message in tool_messages] == ["a", "b", "c"]
    # 모르는 툴도 예외가 아니라 오류 ToolMessage다. 모델이 고쳐 부를 기회를 준다.
    assert "모르는 툴" in tool_messages[2].content


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
    )

    kinds = [_statement_key(statement) for statement, _ in connection.calls]
    assert "evidence_insert" not in kinds
    assert [row.id for row in rows] == [11]


def test_evidence_ranks_follow_the_citation_order():
    connection = FakeConnection({"documents": [document_row(7), document_row(9)], "macro": [macro_row()]})
    box = toolbox(connection)
    box.run("recent_documents", {"hours": 6, "min_score": 0})
    box.run("macro_changes", {})
    model = scripted(answer_message(thesis_payload(refs=["macro_change:SP500_FUT", "document:9"])))
    builder = ThesisBuilder(model, box)
    drafts, _ = builder.run(run_slot=RunSlot.PRE_OPEN, as_of_at=AS_OF, subjects=SUBJECTS, observed_state=OBSERVED)

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
        run_slot=RunSlot.PRE_OPEN,
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
        run_slot=RunSlot.PRE_OPEN,
        horizon_days=1,
        as_of_at=REVIEW_AS_OF,
        targets=(narrative_target(),),
    )

    assert drafts[0].verdict is ThesisVerdict.CONTRADICTED
    assert drafts[0].evidence_refs == ("document:7",)


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
