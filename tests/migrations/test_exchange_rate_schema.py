import pytest

from tests.helpers import NO_REVISION_REASON, head_sql, revision_files

pytestmark = pytest.mark.skipif(not revision_files(), reason=NO_REVISION_REASON)


def test_exchange_rate_migration_emits_the_external_table(capsys):
    # Offline `--sql` has no connection to check, so it always renders the full
    # table: what a finance database built from scratch would get.
    sql = head_sql(capsys)

    assert "CREATE TABLE exchange_rate" in sql
    assert "id SERIAL NOT NULL" in sql
    assert "created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP" in sql
    assert "currency VARCHAR(10) NOT NULL" in sql
    assert "round INTEGER NOT NULL" in sql
    assert "date DATE NOT NULL" in sql
    assert "time TIME WITHOUT TIME ZONE NOT NULL" in sql
    assert "exchange_standard_rate NUMERIC(10, 2) NOT NULL" in sql


def test_exchange_rate_migration_keeps_the_external_constraint_and_index_names(capsys):
    sql = head_sql(capsys)

    assert "CONSTRAINT unique_currency_date_time_round UNIQUE (currency, date, time, round)" in sql
    assert "CREATE INDEX idx_exchange_rate_date ON exchange_rate (date)" in sql
    assert "CREATE INDEX idx_exchange_rate_currency_date ON exchange_rate (currency, date)" in sql


def test_exchange_rate_migration_never_drops_the_external_table(capsys):
    # The finance database owns the data. Downgrading only moves the pointer.
    sql = head_sql(capsys)

    assert "DROP TABLE exchange_rate" not in sql


def test_exchange_rate_migration_documents_nothing(capsys):
    # The live table carries no comments; emitting them would be an ALTER.
    sql = head_sql(capsys)

    assert "COMMENT ON TABLE exchange_rate" not in sql
    assert "COMMENT ON COLUMN exchange_rate" not in sql
