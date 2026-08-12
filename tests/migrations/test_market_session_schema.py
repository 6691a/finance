import pytest

from tests.helpers import NO_REVISION_REASON, head_sql, revision_files

pytestmark = pytest.mark.skipif(not revision_files(), reason=NO_REVISION_REASON)


def test_market_session_table_is_created_with_its_natural_key(capsys):
    sql = head_sql(capsys)

    assert "CREATE TABLE market_session" in sql
    assert "CONSTRAINT uq_market_session_natural_key UNIQUE (market_code, session_date)" in sql
    assert "market_code VARCHAR(20) NOT NULL" in sql
    assert "verified_by VARCHAR(20)" in sql


def test_market_session_constrains_the_closed_value_sets(capsys):
    sql = head_sql(capsys)

    # PostgreSQL native enum을 쓰지 않는 대신 CHECK로 막는다. 값을 늘릴 때 트랜잭션 안에서 끝난다.
    assert "market_code IN ('KRX', 'US_EQUITY')" in sql
    assert "verified_by IN ('kis', 'nyse')" in sql


def test_market_session_restricts_both_lineage_deletes(capsys):
    sql = head_sql(capsys)

    statement = sql[sql.index("CREATE TABLE market_session") :]
    statement = statement[: statement.index(";")]
    # 판정 근거와 보강 근거 둘 다 계보를 지우지 못하게 막는다.
    assert statement.count("REFERENCES source_record (id) ON DELETE RESTRICT") == 2


def test_market_session_indexes_lookup_and_lineage_columns(capsys):
    sql = head_sql(capsys)

    assert "CREATE INDEX ix_market_session_session_date ON market_session (session_date)" in sql
    assert "CREATE INDEX ix_market_session_source_record_id ON market_session (source_record_id)" in sql
    assert (
        "CREATE INDEX ix_market_session_verification_source_record_id "
        "ON market_session (verification_source_record_id)" in sql
    )


def test_market_session_columns_carry_comments(capsys):
    sql = head_sql(capsys)

    assert "COMMENT ON TABLE market_session IS" in sql
    for column in ("market_code", "session_date", "effective_open_day", "verified_by", "local_settlement_date"):
        assert f"COMMENT ON COLUMN market_session.{column} IS" in sql
