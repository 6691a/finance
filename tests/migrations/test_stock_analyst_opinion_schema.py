import pytest

from tests.helpers import NO_REVISION_REASON, head_sql, revision_files

pytestmark = pytest.mark.skipif(not revision_files(), reason=NO_REVISION_REASON)

TABLE = "stock_analyst_opinion"


def _statement(sql: str) -> str:
    statement = sql[sql.index(f"CREATE TABLE {TABLE}") :]
    return statement[: statement.index(";")]


def test_the_table_is_created_with_a_restricted_lineage(capsys):
    sql = head_sql(capsys)

    assert f"CREATE TABLE {TABLE}" in sql
    assert _statement(sql).count("REFERENCES source_record (id) ON DELETE RESTRICT") == 1
    assert f"CREATE INDEX ix_{TABLE}_source_record_id" in sql


def test_the_natural_key_is_the_broker_on_the_publication_day(capsys):
    """같은 날 여러 증권사가 의견을 낸다. 증권사가 빠지면 서로를 덮어쓴다."""
    sql = head_sql(capsys)

    assert f"uq_{TABLE}_natural_key UNIQUE (provider, stock_code, business_date, broker_name)" in sql


def test_only_the_publication_day_gap_is_stored(capsys):
    """조회 시점 현재가 대비 괴리(stft_esdg, dprt)는 매일 바뀐다. 발표일 행에 두지 않는다."""
    statement = _statement(head_sql(capsys))

    assert "gap_amount NUMERIC(18, 4) NOT NULL" in statement
    assert "gap_rate NUMERIC(12, 4) NOT NULL" in statement
    assert "current" not in statement
    assert statement.count("gap_") == 2


def test_the_opinion_wording_is_free_text(capsys):
    """BUY 와 매수 가 섞여 온다. CHECK 로 막으면 첫 증권사 표기에서 죽는다."""
    statement = _statement(head_sql(capsys))

    assert "opinion TEXT NOT NULL" in statement
    assert "opinion_code TEXT NOT NULL" in statement
    assert "CHECK" not in statement


def test_the_table_carries_comments(capsys):
    sql = head_sql(capsys)

    assert f"COMMENT ON TABLE {TABLE} IS" in sql
    assert f"COMMENT ON COLUMN {TABLE}.provider IS" in sql
    assert f"COMMENT ON COLUMN {TABLE}.broker_name IS" in sql
    assert f"COMMENT ON COLUMN {TABLE}.gap_rate IS" in sql
