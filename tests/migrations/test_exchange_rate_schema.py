import pytest

from tests.helpers import NO_REVISION_REASON, head_sql, revision_files

pytestmark = pytest.mark.skipif(not revision_files(), reason=NO_REVISION_REASON)


def test_exchange_rate_migration_creates_the_table(capsys):
    sql = head_sql(capsys)

    assert "CREATE TABLE exchange_rate" in sql


def test_exchange_rate_migration_mirrors_the_finance_ddl(capsys):
    # The column shape copies the external finance table so its rows can be loaded
    # later without translating columns. That is why it uses SERIAL and naive
    # timestamps instead of the project defaults. `currency` is an Enum in Python
    # only; a native enum or a CHECK constraint would add DDL the original lacks.
    sql = head_sql(capsys)

    assert "id SERIAL NOT NULL" in sql
    assert "created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP" in sql
    assert "currency VARCHAR(10) NOT NULL" in sql
    assert "round INTEGER NOT NULL" in sql
    assert "date DATE NOT NULL" in sql
    assert "time TIME WITHOUT TIME ZONE NOT NULL" in sql
    assert "exchange_standard_rate NUMERIC(10, 2) NOT NULL" in sql


def test_exchange_rate_migration_keeps_the_original_constraint_and_index_names(capsys):
    # The collector's upsert targets `unique_currency_date_time_round` by name.
    sql = head_sql(capsys)

    assert "CONSTRAINT unique_currency_date_time_round UNIQUE (currency, date, time, round)" in sql
    assert "CREATE INDEX idx_exchange_rate_date ON exchange_rate (date)" in sql
    assert "CREATE INDEX idx_exchange_rate_currency_date ON exchange_rate (currency, date)" in sql


def test_exchange_rate_migration_documents_the_table(capsys):
    # Comments carry no data, so they do not stand in the way of loading the
    # finance rows later. The table follows the project rule and describes itself.
    sql = head_sql(capsys)

    assert "COMMENT ON TABLE exchange_rate" in sql
    for column in ("currency", "round", "date", "time", "buy", "sell", "send", "receive"):
        assert f"COMMENT ON COLUMN exchange_rate.{column}" in sql


def test_exchange_rate_migration_adds_no_currency_check_constraint(capsys):
    # A CHECK would have to be rewritten every time the collector adds a currency.
    sql = head_sql(capsys)

    assert "currency IN (" not in sql


def test_upgrading_never_drops_exchange_rate(capsys):
    # The move to `default` must not emit a DROP: autogenerate would have proposed
    # one for the alias the table left, and that alias is a live external database.
    sql = head_sql(capsys)

    assert "DROP TABLE exchange_rate" not in sql
