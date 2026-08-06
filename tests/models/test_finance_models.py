"""`exchange_rate` is this project's own table, shaped after the finance one.

The table lives on the `default` alias (news2) and the Hana collector writes to
it. Its DDL is a byte-for-byte copy of the external finance table so those rows
can be loaded later without translating columns, which is why it keeps SERIAL,
naive timestamps and no comments instead of the project defaults. Drift here
would break that copy, so the shape is pinned column by column.
"""

from sqlalchemy.dialects import postgresql

from apps.models.finance import ExchangeRate
from core.database import table_database, table_managed

TABLE = ExchangeRate.__table__

EXTERNAL_DDL = {
    "id": "INTEGER",
    "created_at": "TIMESTAMP WITHOUT TIME ZONE",
    "updated_at": "TIMESTAMP WITHOUT TIME ZONE",
    "currency": "VARCHAR(10)",
    "round": "INTEGER",
    "date": "DATE",
    "time": "TIME WITHOUT TIME ZONE",
    "buy": "NUMERIC(10, 2)",
    "sell": "NUMERIC(10, 2)",
    "send": "NUMERIC(10, 2)",
    "receive": "NUMERIC(10, 2)",
    "exchange_standard_rate": "NUMERIC(10, 2)",
}

NULLABLE = {"created_at", "updated_at"}


def test_exchange_rate_mirrors_the_external_column_types():
    dialect = postgresql.dialect()

    assert {column.name: column.type.compile(dialect) for column in TABLE.columns} == EXTERNAL_DDL


def test_exchange_rate_mirrors_the_external_nullability():
    assert {column.name for column in TABLE.columns if column.nullable} == NULLABLE


def test_exchange_rate_keeps_the_external_serial_primary_key():
    assert [column.name for column in TABLE.primary_key] == ["id"]
    assert TABLE.c.id.autoincrement is True


def test_exchange_rate_keeps_the_external_timestamp_defaults():
    for name in ("created_at", "updated_at"):
        server_default = TABLE.c[name].server_default
        assert server_default is not None
        assert str(server_default.arg) == "CURRENT_TIMESTAMP"


def test_exchange_rate_keeps_the_external_constraint_and_index_names():
    assert {constraint.name for constraint in TABLE.constraints} >= {"unique_currency_date_time_round"}
    assert {index.name for index in TABLE.indexes} == {
        "idx_exchange_rate_date",
        "idx_exchange_rate_currency_date",
    }


def test_exchange_rate_declares_no_comments():
    # The finance table it copies has none. Adding them here would make the two
    # differ and turn a later data load into a schema comparison.
    assert TABLE.comment is None
    assert all(column.comment is None for column in TABLE.columns)


def test_exchange_rate_is_migrated_by_the_default_alias():
    # The table moved off the `finance` alias; this project owns and writes its copy.
    assert table_database(TABLE) == "default"
    assert table_managed(TABLE) is True
