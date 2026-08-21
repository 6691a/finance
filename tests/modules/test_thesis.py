import re
from decimal import Decimal

import pytest
from sqlalchemy import Table

from apps.models.analysis import Thesis, ThesisEvidence
from modules.sql import read_sql
from modules.thesis import FLAT_THRESHOLD_PCT, ThesisDirection, brier_score, classify_outcome

THESIS_INSERT = read_sql("postgres", "thesis", "insert.sql")
THESIS_SELECT_BY_RUN = read_sql("postgres", "thesis", "select_by_run.sql")
THESIS_SELECT_TO_GRADE = read_sql("postgres", "thesis", "select_forecasts_to_grade.sql")
THESIS_UPDATE_OUTCOME = read_sql("postgres", "thesis", "update_outcome.sql")
EVIDENCE_INSERT = read_sql("postgres", "thesis_evidence", "insert.sql")
EVIDENCE_SELECT_ALL = read_sql("postgres", "thesis_evidence", "select_by_thesis_ids.sql")
EVIDENCE_SELECT_TOP = read_sql("postgres", "thesis_evidence", "select_top_by_thesis_ids.sql")
STOCK_SESSION_RETURN = read_sql("postgres", "stock_investor_trade_daily", "select_session_return.sql")
INDEX_SESSION_RETURN = read_sql("postgres", "index_bar", "select_session_return.sql")


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
    ("return_pct", "expected"),
    [
        ("0.29", ThesisDirection.FLAT),
        ("0.30", ThesisDirection.UP),
        ("-0.29", ThesisDirection.FLAT),
        ("-0.30", ThesisDirection.DOWN),
        ("0", ThesisDirection.FLAT),
        ("12.5", ThesisDirection.UP),
        ("-12.5", ThesisDirection.DOWN),
    ],
)
def test_classify_outcome_puts_the_boundary_on_the_moving_side(return_pct, expected):
    # 경계값 0.30은 flat이 아니라 방향이다. 0.29는 flat이다.
    assert classify_outcome(Decimal(return_pct)) is expected
    assert FLAT_THRESHOLD_PCT == Decimal("0.3")


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
    "evaluated_at",
    "actual_return_pct",
    "actual_outcome",
    "brier_score",
}
SELECT_TO_GRADE_COLUMNS = {"id", "run_date", "subject_kind", "subject_code", "prob_up", "prob_down", "prob_flat"}
EVIDENCE_SELECT_COLUMNS = {"thesis_id", "evidence_kind", "evidence_ref", "evidence_title", "evidence_url", "rank"}


@pytest.mark.parametrize(
    ("statement", "model", "expected"),
    [
        (THESIS_SELECT_BY_RUN, Thesis, SELECT_BY_RUN_COLUMNS),
        (THESIS_SELECT_TO_GRADE, Thesis, SELECT_TO_GRADE_COLUMNS),
        (EVIDENCE_SELECT_ALL, ThesisEvidence, EVIDENCE_SELECT_COLUMNS),
        (EVIDENCE_SELECT_TOP, ThesisEvidence, EVIDENCE_SELECT_COLUMNS),
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


def test_forecast_grading_scan_has_no_date_limit():
    predicate = body(THESIS_SELECT_TO_GRADE)

    assert "run_slot = 'pre_open'" in predicate
    assert "evaluated_at IS NULL" in predicate
    # 장후가 실패한 날의 forecast도 다음 실행이 회수해야 한다.
    assert "run_date =" not in predicate
    assert "run_date >" not in predicate


def test_update_outcome_touches_only_the_grading_columns_and_is_idempotent():
    assignments = THESIS_UPDATE_OUTCOME[THESIS_UPDATE_OUTCOME.index("SET") : THESIS_UPDATE_OUTCOME.index("WHERE")]

    assert set(re.findall(r"(\w+) =", assignments)) == {
        "evaluated_at",
        "actual_return_pct",
        "actual_outcome",
        "brier_score",
        "updated_at",
    }
    # 이미 채점된 행은 0행이 갱신되고 처음 매긴 점수가 그대로 남는다.
    assert "evaluated_at IS NULL" in THESIS_UPDATE_OUTCOME


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
        THESIS_SELECT_TO_GRADE,
        EVIDENCE_SELECT_ALL,
        EVIDENCE_SELECT_TOP,
        STOCK_SESSION_RETURN,
        INDEX_SESSION_RETURN,
    ],
)
def test_lookups_never_read_the_wall_clock(statement):
    # 조회의 기준 시각은 슬롯이 정하는 as_of_at이다(event-time cutoff).
    query = body(statement)
    assert "now()" not in query
    assert "CURRENT_TIMESTAMP" not in query
