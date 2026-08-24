"""이벤트 기대치 테이블 리비전의 테이블 단위 사실.

특정 리비전 ID나 전체 문자열에 고정하지 않는다. offline SQL에 다음이 있으면 충분하다:
테이블 셋, 멱등키 UNIQUE, 값 집합·기간 표기·출처 XOR CHECK, FK 삭제 규칙, 주석.
"""

import pytest

from tests.helpers import NO_REVISION_REASON, head_sql, revision_files

pytestmark = pytest.mark.skipif(not revision_files(), reason=NO_REVISION_REASON)


def _table_statement(sql: str, table: str) -> str:
    statement = sql[sql.index(f"CREATE TABLE {table} (") :]
    return statement[: statement.index(";")]


def test_the_event_tables_are_created_without_a_schema_prefix(capsys):
    sql = head_sql(capsys)

    # 저장소 규칙대로 연결의 search_path(기본 public)를 따른다.
    assert "CREATE TABLE stock_event_claim (" in sql
    assert "CREATE TABLE stock_event_extraction (" in sql
    assert "CREATE TABLE stock_event_outcome (" in sql
    assert "CREATE TABLE analysis." not in sql


def test_one_outcome_row_per_event_metric(capsys):
    """멱등키. 같은 이벤트 지표에 판정이 둘이면 어느 쪽이 진짜인지 알 수 없다."""
    statement = _table_statement(head_sql(capsys), "stock_event_outcome")

    assert (
        "CONSTRAINT uq_stock_event_outcome_natural_key UNIQUE (stock_code, event_type, period_key, metric)" in statement
    )


def test_one_claim_per_document_event_and_kind(capsys):
    """본문이 바뀌어 재추출해도 같은 문서의 같은 주장이 둘로 늘지 않는다."""
    statement = _table_statement(head_sql(capsys), "stock_event_claim")

    assert (
        "CONSTRAINT uq_stock_event_claim_document_claim UNIQUE"
        " (document_id, event_type, period_key, metric, claim_kind)" in statement
    )


def test_the_closed_value_sets_are_constrained(capsys):
    """PostgreSQL native enum을 쓰지 않는 대신 CHECK로 막는다(프로젝트 규칙)."""
    sql = head_sql(capsys)
    claim = _table_statement(sql, "stock_event_claim")
    outcome = _table_statement(sql, "stock_event_outcome")

    assert "event_type IN ('shareholder_return', 'earnings', 'guidance')" in claim
    assert "claim_kind IN ('expectation', 'actual')" in claim
    assert "verdict IN ('beat', 'meet', 'miss')" in outcome
    for statement in (claim, outcome):
        assert "'total_return_amount'" in statement
        assert "'operating_profit'" in statement


def test_the_period_key_shape_is_enforced_by_the_database(capsys):
    """느슨하게 받으면 기대와 실제가 다른 표기로 저장돼 조용히 매칭이 깨진다."""
    sql = head_sql(capsys)

    for table in ("stock_event_claim", "stock_event_outcome"):
        assert "period_key ~ '^[0-9]{4}(Q[1-4]|H[12])?$'" in _table_statement(sql, table)


def test_a_claim_has_exactly_one_source(capsys):
    """LLM 추출이면 문서, 컨센서스면 source_record다. 둘 다이거나 둘 다 아니면 계보가 끊긴다."""
    statement = _table_statement(head_sql(capsys), "stock_event_claim")

    assert "(document_id IS NULL) <> (source_record_id IS NULL)" in statement


def test_a_range_claim_keeps_both_bounds_in_order(capsys):
    statement = _table_statement(head_sql(capsys), "stock_event_claim")

    assert "value_low IS NULL AND value_high IS NULL" in statement
    assert "value_low <= value_high" in statement


def test_a_judgment_always_names_how_many_expectations_it_compared(capsys):
    """기대가 없던 발표는 판정하지 않는다. 0건 판정이 들어오면 그건 버그다."""
    statement = _table_statement(head_sql(capsys), "stock_event_outcome")

    assert "expectation_count > 0" in statement


def test_the_source_record_link_is_restricted_and_the_document_link_cascades(capsys):
    statement = _table_statement(head_sql(capsys), "stock_event_claim")

    assert "FOREIGN KEY(document_id) REFERENCES document (id) ON DELETE CASCADE" in statement
    assert "FOREIGN KEY(source_record_id) REFERENCES source_record (id) ON DELETE RESTRICT" in statement


def test_the_outcome_table_does_not_reference_the_instrument_master(capsys):
    """마스터에 없는 종목 하나가 판정 저장 전체를 죽이면 안 된다(`document_instrument` 선례)."""
    statement = _table_statement(head_sql(capsys), "stock_event_outcome")

    assert "REFERENCES instrument" not in statement


def test_every_event_table_and_column_carries_a_korean_comment(capsys):
    sql = head_sql(capsys)

    assert "COMMENT ON TABLE stock_event_claim IS" in sql
    assert "COMMENT ON TABLE stock_event_extraction IS" in sql
    assert "COMMENT ON TABLE stock_event_outcome IS" in sql
    assert "COMMENT ON COLUMN stock_event_outcome.surprise_pct IS" in sql
    assert "COMMENT ON COLUMN stock_event_claim.stated_at IS" in sql
    assert "COMMENT ON COLUMN stock_event_extraction.claim_count IS" in sql
