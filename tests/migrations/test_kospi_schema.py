"""코스피 전망 리비전의 테이블 단위 사실.

특정 리비전 ID나 전체 문자열에 고정하지 않는다 — 리비전을 다시 만들 때마다 깨진다.
offline SQL에 다음이 있으면 충분하다: 표 셋, 자연키 UNIQUE, 값 집합 CHECK, 채점 all-or-none,
CASCADE, 주석.
"""

import pytest

from tests.helpers import NO_REVISION_REASON, head_sql, revision_files

pytestmark = pytest.mark.skipif(not revision_files(), reason=NO_REVISION_REASON)


def _table_statement(sql: str, table: str) -> str:
    statement = sql[sql.index(f"CREATE TABLE {table} (") :]
    return statement[: statement.index(";")]


def test_kospi_tables_are_created_without_a_schema_prefix(capsys):
    sql = head_sql(capsys)

    # 저장소 규칙대로 연결의 search_path(기본 public)를 따른다.
    assert "CREATE TABLE kospi_forecast (" in sql
    assert "CREATE TABLE kospi_llm_run (" in sql
    assert "CREATE TABLE kospi_tool_call (" in sql
    assert "CREATE TABLE analysis." not in sql


def test_no_relation_or_memory_table_is_created(capsys):
    """**관계와 메모의 원본은 Neo4j다.** 여기 만들면 원본이 둘이 된다."""
    sql = head_sql(capsys)

    assert "CREATE TABLE kospi_relation" not in sql
    assert "CREATE TABLE kospi_memory" not in sql


def test_one_forecast_per_date_and_slot(capsys):
    statement = _table_statement(head_sql(capsys), "kospi_forecast")

    assert "CONSTRAINT uq_kospi_forecast_natural_key UNIQUE (run_date, slot)" in statement


def test_the_forecast_constrains_its_closed_value_sets(capsys):
    statement = _table_statement(head_sql(capsys), "kospi_forecast")

    # PostgreSQL native enum을 쓰지 않는 대신 CHECK로 막는다(프로젝트 규칙).
    assert "slot IN ('pre_open', 'midday', 'pre_close')" in statement
    assert "direction IN ('up', 'down')" in statement


def test_the_grade_columns_are_all_or_none(capsys):
    """셋만 채워진 행은 읽을 수 없다."""
    statement = _table_statement(head_sql(capsys), "kospi_forecast")

    assert "ck_kospi_forecast_grade_all_or_none" in statement


def test_only_intraday_slots_carry_the_so_far_column(capsys):
    statement = _table_statement(head_sql(capsys), "kospi_forecast")

    assert "ck_kospi_forecast_so_far_shape" in statement


def test_the_range_checks_only_catch_runaway_values(capsys):
    """정합성은 프롬프트와 저장 전 검증이 본다. DB로 막으면 경계값 하나에 행이 사라진다."""
    statement = _table_statement(head_sql(capsys), "kospi_forecast")

    assert "expected_change_pct BETWEEN -10 AND 10" in statement
    assert "band_pct BETWEEN 0.1 AND 5" in statement
    assert "base_price > 0" in statement


def test_a_review_conversation_has_no_slot_and_a_forecast_one_does(capsys):
    statement = _table_statement(head_sql(capsys), "kospi_llm_run")

    assert "ck_kospi_llm_run_slot_shape" in statement


def test_tool_calls_follow_their_conversation_on_delete(capsys):
    statement = _table_statement(head_sql(capsys), "kospi_tool_call")

    assert "FOREIGN KEY(llm_run_id) REFERENCES kospi_llm_run (id) ON DELETE CASCADE" in statement


def test_a_forecast_outlives_its_ledger_row(capsys):
    """원장이 지워져도 전망은 남아야 한다. 판단이 기록의 목적이다."""
    statement = _table_statement(head_sql(capsys), "kospi_forecast")

    assert "FOREIGN KEY(llm_run_id) REFERENCES kospi_llm_run (id) ON DELETE SET NULL" in statement


def test_a_tool_call_row_says_what_happened(capsys):
    """결과도 오류도 없는 행은 무슨 일이 있었는지 말하지 않는다."""
    statement = _table_statement(head_sql(capsys), "kospi_tool_call")

    assert "ck_kospi_tool_call_outcome" in statement


def test_the_tables_and_columns_carry_korean_comments(capsys):
    sql = head_sql(capsys)

    assert "COMMENT ON TABLE kospi_forecast IS" in sql
    assert "COMMENT ON COLUMN kospi_forecast.input_state IS" in sql
    assert "COMMENT ON COLUMN kospi_llm_run.truncated IS" in sql


def test_the_observation_count_has_a_denominator_column(capsys):
    """`rejected`만 있고 남은 수가 없으면 유효율을 못 읽는다.

    관찰 엣지는 Neo4j로만 가서, 이 칸이 없으면 Postgres에서 "몇 개 중 몇 개를 버렸나"에
    답할 수 없다. **분모 없는 카운터는 카운터가 아니다.**
    """
    sql = head_sql(capsys)

    assert "observations_written" in sql
    # 전망 대화에 값이 들어오면 배선이 어긋난 것이다.
    assert "ck_kospi_llm_run_observations_kind" in sql
    assert "ck_kospi_llm_run_observations_written" in sql
