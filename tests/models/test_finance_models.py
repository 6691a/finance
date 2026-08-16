"""`exchange_rate` is this project's own table, shaped after the finance one.

The table lives on the `default` alias (finance) and the Hana collector writes to
it. Its column shape copies the external finance table so those rows can be
loaded later without translating columns, which is why it keeps SERIAL, naive
timestamps and split date/time columns instead of the project defaults. Drift
there would break that copy, so the shape is pinned column by column.

Comments are the documented exception: they carry no data and a later load
ignores them, so this table follows the project rule and describes every column.
"""

from sqlalchemy import CheckConstraint
from sqlalchemy.dialects import postgresql

from apps.core.database import table_database, table_managed
from apps.models.finance import Currency, ExchangeRate
from modules.collectors.hana import HanaCurrency

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


def test_exchange_rate_describes_every_column():
    assert TABLE.comment
    assert [column.name for column in TABLE.columns if not column.comment] == []


def test_exchange_rate_currency_stays_a_plain_varchar():
    # The column is an Enum on the Python side only. A native PostgreSQL enum or
    # a CHECK constraint would add DDL the finance table does not have, and every
    # new currency would then need a migration.
    assert TABLE.c.currency.type.compile(postgresql.dialect()) == "VARCHAR(10)"
    assert [constraint for constraint in TABLE.constraints if isinstance(constraint, CheckConstraint)] == []


def test_exchange_rate_currencies_match_the_collector():
    # The collector decides which currencies get written; this Enum only mirrors
    # it because Airflow and the backend cannot import each other.
    assert {member.value for member in Currency} == {member.value for member in HanaCurrency}


def test_exchange_rate_is_migrated_by_the_default_alias():
    # The table moved off the `finance` alias; this project owns and writes its copy.
    assert table_database(TABLE) == "default"
    assert table_managed(TABLE) is True
