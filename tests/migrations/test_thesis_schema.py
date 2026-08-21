"""추론 테이블 리비전의 테이블 단위 사실.

특정 리비전 ID나 전체 문자열에 고정하지 않는다. offline SQL에 다음이 있으면 충분하다:
두 테이블, 멱등키 UNIQUE, 값 집합 CHECK, 확률·채점 CHECK, CASCADE, 주석.
"""

import pytest

from tests.helpers import NO_REVISION_REASON, head_sql, revision_files

pytestmark = pytest.mark.skipif(not revision_files(), reason=NO_REVISION_REASON)


def _table_statement(sql: str, table: str) -> str:
    statement = sql[sql.index(f"CREATE TABLE {table} (") :]
    return statement[: statement.index(";")]


def test_thesis_tables_are_created_without_a_schema_prefix(capsys):
    sql = head_sql(capsys)

    # 저장소 규칙대로 연결의 search_path(기본 public)를 따른다. 파일 이름 analysis.py는
    # 도메인 구분일 뿐이라 스키마가 되지 않는다.
    assert "CREATE TABLE thesis (" in sql
    assert "CREATE TABLE thesis_evidence (" in sql
    assert "CREATE SCHEMA" not in sql
    assert "CREATE TABLE analysis." not in sql


def test_thesis_keeps_one_row_per_slot_and_subject(capsys):
    sql = head_sql(capsys)

    assert (
        "CONSTRAINT uq_thesis_natural_key UNIQUE (run_date, run_slot, subject_kind, subject_code)"
        in _table_statement(sql, "thesis")
    )


def test_thesis_constrains_the_closed_value_sets(capsys):
    statement = _table_statement(head_sql(capsys), "thesis")

    # PostgreSQL native enum을 쓰지 않는 대신 CHECK로 막는다(프로젝트 규칙).
    assert "run_slot IN ('pre_open', 'post_close')" in statement
    assert "subject_kind IN ('index', 'stock')" in statement
    assert "actual_outcome IN ('up', 'down', 'flat')" in statement


def test_thesis_constrains_the_three_probabilities(capsys):
    statement = _table_statement(head_sql(capsys), "thesis")

    assert "prob_up BETWEEN 0 AND 1" in statement
    assert "prob_down BETWEEN 0 AND 1" in statement
    assert "prob_flat BETWEEN 0 AND 1" in statement
    # 저장 전에 애플리케이션이 이미 정규화한다. 이 제약은 그 뒤의 최종 안전장치다.
    assert "abs(prob_up + prob_down + prob_flat - 1) < 0.001" in statement


def test_thesis_grading_columns_are_all_or_none(capsys):
    statement = _table_statement(head_sql(capsys), "thesis")

    # 등락률만 있고 점수가 없는 중간 상태를 두면 "채점했는데 점수가 없는" 행이 조용히 생긴다.
    assert "evaluated_at IS NULL AND actual_return_pct IS NULL" in statement
    assert "evaluated_at IS NOT NULL AND actual_return_pct IS NOT NULL" in statement
    assert "brier_score BETWEEN 0 AND 2" in statement


def test_thesis_indexes_the_ungraded_forecast_scan(capsys):
    sql = head_sql(capsys)

    # 미채점 forecast 조회에 날짜 제한이 없어 인덱스 없이는 스캔이 계속 커진다.
    assert "CREATE INDEX ix_thesis_run_slot_evaluated_at ON thesis (run_slot, evaluated_at)" in sql


def test_thesis_evidence_keeps_one_row_per_ref_and_per_rank(capsys):
    statement = _table_statement(head_sql(capsys), "thesis_evidence")

    assert "CONSTRAINT uq_thesis_evidence_ref UNIQUE (thesis_id, evidence_kind, evidence_ref)" in statement
    assert "CONSTRAINT uq_thesis_evidence_rank UNIQUE (thesis_id, rank)" in statement
    assert "evidence_kind IN ('document', 'disclosure', 'macro_change')" in statement
    assert "rank > 0" in statement


def test_thesis_evidence_follows_its_thesis_on_delete(capsys):
    statement = _table_statement(head_sql(capsys), "thesis_evidence")

    # 근거는 추론 없이 의미가 없다. source_record 계보(RESTRICT)와 반대 방향의 결정이다.
    assert "FOREIGN KEY(thesis_id) REFERENCES thesis (id) ON DELETE CASCADE" in statement


def test_thesis_tables_and_columns_carry_comments(capsys):
    sql = head_sql(capsys)

    assert "COMMENT ON TABLE thesis IS" in sql
    assert "COMMENT ON TABLE thesis_evidence IS" in sql
    for column in ("run_slot", "as_of_at", "dag_run_id", "prob_up", "input_state", "brier_score"):
        assert f"COMMENT ON COLUMN thesis.{column} IS" in sql
    for column in ("evidence_kind", "evidence_ref", "evidence_url", "rank"):
        assert f"COMMENT ON COLUMN thesis_evidence.{column} IS" in sql
