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
    assert "CREATE TABLE thesis_outcome (" in sql
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


def test_thesis_constrains_the_three_probabilities(capsys):
    statement = _table_statement(head_sql(capsys), "thesis")

    assert "prob_up BETWEEN 0 AND 1" in statement
    assert "prob_down BETWEEN 0 AND 1" in statement
    assert "prob_flat BETWEEN 0 AND 1" in statement
    # 저장 전에 애플리케이션이 이미 정규화한다. 이 제약은 그 뒤의 최종 안전장치다.
    assert "abs(prob_up + prob_down + prob_flat - 1) < 0.001" in statement


def test_the_thesis_row_holds_no_grading_columns(capsys):
    statement = _table_statement(head_sql(capsys), "thesis")

    # 지평별 결과는 thesis_outcome이 갖는다. 여기 두면 두 번째 지평이 첫 판단을 덮어써야 한다.
    for column in ("evaluated_at", "actual_return_pct", "actual_outcome", "brier_score"):
        assert column not in statement


def test_thesis_outcome_keeps_one_row_per_thesis_and_horizon(capsys):
    statement = _table_statement(head_sql(capsys), "thesis_outcome")

    assert "CONSTRAINT uq_thesis_outcome_natural_key UNIQUE (thesis_id, horizon_days)" in statement
    assert "horizon_days IN (0, 1, 3, 5)" in statement
    assert "actual_outcome IN ('up', 'down', 'flat')" in statement
    assert "verdict IN ('supported', 'contradicted', 'unresolved')" in statement
    assert "brier_score BETWEEN 0 AND 2" in statement


def test_thesis_outcome_keeps_grading_and_narrative_all_or_none(capsys):
    statement = _table_statement(head_sql(capsys), "thesis_outcome")

    # 등락률만 있고 점수가 없는 중간 상태를 두면 "채점했는데 점수가 없는" 행이 조용히 생긴다.
    assert "evaluated_at IS NULL AND actual_return_pct IS NULL" in statement
    assert "evaluated_at IS NOT NULL AND actual_return_pct IS NOT NULL" in statement
    # 판정만 있고 근거 문장이 없으면 되짚을 수 없다.
    assert "narrative IS NULL AND verdict IS NULL" in statement
    assert "narrative IS NOT NULL AND verdict IS NOT NULL" in statement


def test_thesis_outcome_refuses_a_row_that_says_nothing(capsys):
    statement = _table_statement(head_sql(capsys), "thesis_outcome")

    # 채점도 해설도 없으면 그 행은 없는 것과 같다.
    assert "evaluated_at IS NOT NULL OR narrative IS NOT NULL" in statement
    # 지평 0은 그날의 후속 보도가 아직 쌓이지 않아 해설을 못 쓴다.
    assert "horizon_days <> 0 OR (narrative IS NULL" in statement


def test_thesis_outcome_follows_its_thesis_on_delete(capsys):
    statement = _table_statement(head_sql(capsys), "thesis_outcome")

    assert "FOREIGN KEY(thesis_id) REFERENCES thesis (id) ON DELETE CASCADE" in statement


def test_thesis_evidence_keeps_one_row_per_ref_and_per_rank(capsys):
    statement = _table_statement(head_sql(capsys), "thesis_evidence")

    # 원 추론과 지평별 해설이 같은 테이블에 있어 인용 주체가 키에 들어간다.
    assert (
        "CONSTRAINT uq_thesis_evidence_ref UNIQUE (thesis_id, outcome_horizon_days, evidence_kind, evidence_ref)"
        in statement
    )
    assert "CONSTRAINT uq_thesis_evidence_rank UNIQUE (thesis_id, outcome_horizon_days, rank)" in statement
    assert "evidence_kind IN ('document', 'disclosure', 'macro_change')" in statement
    assert "outcome_horizon_days IS NULL OR outcome_horizon_days IN (1, 3, 5)" in statement
    assert "rank > 0" in statement


def test_thesis_evidence_follows_its_thesis_on_delete(capsys):
    statement = _table_statement(head_sql(capsys), "thesis_evidence")

    # 근거는 추론 없이 의미가 없다. source_record 계보(RESTRICT)와 반대 방향의 결정이다.
    assert "FOREIGN KEY(thesis_id) REFERENCES thesis (id) ON DELETE CASCADE" in statement


def test_thesis_tables_and_columns_carry_comments(capsys):
    sql = head_sql(capsys)

    assert "COMMENT ON TABLE thesis IS" in sql
    assert "COMMENT ON TABLE thesis_outcome IS" in sql
    assert "COMMENT ON TABLE thesis_evidence IS" in sql
    for column in ("run_slot", "as_of_at", "dag_run_id", "prob_up", "input_state"):
        assert f"COMMENT ON COLUMN thesis.{column} IS" in sql
    for column in ("horizon_days", "actual_return_pct", "brier_score", "narrative", "verdict", "prompt_version"):
        assert f"COMMENT ON COLUMN thesis_outcome.{column} IS" in sql
    for column in ("outcome_horizon_days", "evidence_kind", "evidence_ref", "evidence_url", "rank"):
        assert f"COMMENT ON COLUMN thesis_evidence.{column} IS" in sql


def test_thesis_precedent_links_a_thesis_to_the_past_theses_it_saw(capsys):
    sql = head_sql(capsys)
    statement = _table_statement(sql, "thesis_precedent")

    # 한 추론이 같은 과거 추론을 두 번 봤다는 행은 없다. 자기 자신을 봤다는 행도 없다.
    assert "CONSTRAINT uq_thesis_precedent_natural_key UNIQUE (thesis_id, precedent_id)" in statement
    assert "thesis_id <> precedent_id" in statement
    # 추론이 지워지면 본 기록도 지운다. 남이 본 과거 추론은 지우지 못한다.
    assert "FOREIGN KEY(thesis_id) REFERENCES thesis (id) ON DELETE CASCADE" in statement
    assert "FOREIGN KEY(precedent_id) REFERENCES thesis (id) ON DELETE RESTRICT" in statement
    # thesis_evidence가 아니다 — 인용이 아니라 보여 준 것이라 rank가 없다.
    assert "rank" not in statement


def test_thesis_evidence_records_how_the_thesis_used_each_citation(capsys):
    sql = head_sql(capsys)

    # 이유 문장은 산문이라 엣지에 못 싣는다. 근거마다 방향과 경로가 칸으로 있어야 그래프가 된다.
    assert "ALTER TABLE thesis_evidence ADD COLUMN direction VARCHAR(20)" in sql
    assert "ALTER TABLE thesis_evidence ADD COLUMN mechanism TEXT" in sql
    assert "direction IS NULL OR direction IN ('up', 'down', 'flat')" in sql
    # 방향만 있고 경로가 없는 행은 "왜"를 잃는다. 쌍으로만 들어간다.
    assert "(direction IS NULL AND mechanism IS NULL) OR (direction IS NOT NULL AND mechanism IS NOT NULL)" in sql
