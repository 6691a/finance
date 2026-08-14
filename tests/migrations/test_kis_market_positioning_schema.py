import pytest

from tests.helpers import NO_REVISION_REASON, head_sql, revision_files

pytestmark = pytest.mark.skipif(not revision_files(), reason=NO_REVISION_REASON)

KRX_TABLES = (
    "krx_stock_credit_balance_daily",
    "krx_credit_balance_ranking_daily",
    "krx_market_funds_daily",
    "krx_stock_short_sale_daily",
    "krx_stock_securities_lending_daily",
)


@pytest.mark.parametrize("table", KRX_TABLES)
def test_every_krx_table_is_created_with_a_restricted_lineage(table, capsys):
    sql = head_sql(capsys)

    assert f"CREATE TABLE {table}" in sql
    statement = sql[sql.index(f"CREATE TABLE {table}") :]
    statement = statement[: statement.index(";")]
    assert statement.count("REFERENCES source_record (id) ON DELETE RESTRICT") == 1
    assert f"CREATE INDEX ix_{table}_source_record_id" in sql


def test_natural_keys_separate_stocks_dates_and_rank_slots(capsys):
    sql = head_sql(capsys)

    assert "uq_krx_stock_credit_balance_daily_natural_key UNIQUE (provider, stock_code, trade_date)" in sql
    assert "uq_krx_market_funds_daily_natural_key UNIQUE (provider, business_date)" in sql
    assert "uq_krx_stock_short_sale_daily_natural_key UNIQUE (provider, stock_code, business_date)" in sql
    assert "uq_krx_stock_securities_lending_daily_natural_key UNIQUE (provider, stock_code, business_date)" in sql
    assert (
        "uq_krx_credit_balance_ranking_daily_natural_key UNIQUE "
        "(provider, standard_date, universe_code, sort_code, period_days, rank)" in sql
    )


def test_credit_balance_keeps_both_dates(capsys):
    """거래일이 자연키이고 결제일은 값이다. 결제 시차가 2영업일이라 둘을 섞으면 추이가 밀린다."""
    sql = head_sql(capsys)

    statement = sql[sql.index("CREATE TABLE krx_stock_credit_balance_daily") :]
    statement = statement[: statement.index(";")]
    assert "trade_date DATE NOT NULL" in statement
    assert "settlement_date DATE NOT NULL" in statement


def test_market_funds_does_not_store_the_odd_change_rate(capsys):
    """실측에서 prdy_ctrt 가 등락률과 맞지 않았다. 의미가 확인되기 전에는 컬럼을 두지 않는다."""
    sql = head_sql(capsys)

    statement = sql[sql.index("CREATE TABLE krx_market_funds_daily") :]
    statement = statement[: statement.index(";")]
    assert "change_rate" not in statement
    assert "index_close" in statement
    assert "index_change" in statement


def test_no_nxt_table_is_created_yet(capsys):
    """쓰는 코드가 없는 스키마는 '곧 데이터가 온다'는 거짓 신호다. 계약 확인 때 함께 만든다."""
    sql = head_sql(capsys)

    assert "CREATE TABLE nxt_" not in sql


@pytest.mark.parametrize("table", KRX_TABLES)
def test_every_krx_table_carries_comments(table, capsys):
    sql = head_sql(capsys)

    assert f"COMMENT ON TABLE {table} IS" in sql
    assert f"COMMENT ON COLUMN {table}.provider IS" in sql
