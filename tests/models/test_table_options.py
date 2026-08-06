import pytest
from sqlalchemy import Column, MetaData, Table, Text

from core.database import (
    DEFAULT_DATABASE_ALIAS,
    Base,
    table_database,
    table_managed,
    table_options,
)


def test_table_options_declares_comment_database_and_managed():
    options = table_options(comment="테스트 테이블", database="market_migration")

    assert options == {
        "comment": "테스트 테이블",
        "info": {"database": "market_migration", "managed": True},
    }


def test_table_options_never_declares_a_schema():
    # Tables follow the connection's search_path; nothing pins them to a schema.
    assert "schema" not in table_options(comment="테스트 테이블")


def test_table_options_defaults_to_the_default_alias_and_managed():
    assert table_options(comment="테스트 테이블")["info"] == {
        "database": DEFAULT_DATABASE_ALIAS,
        "managed": True,
    }


def test_table_options_lets_a_mirrored_table_carry_no_comment():
    # A table another system already created has no comment in the database.
    # Declaring one here would make autogenerate emit COMMENT ON forever.
    assert table_options(comment=None)["comment"] is None


def test_table_options_can_opt_a_table_out_of_migrations():
    assert table_options(comment="외부 소유 테이블", managed=False)["info"] == {
        "database": DEFAULT_DATABASE_ALIAS,
        "managed": False,
    }


def test_undeclared_tables_fall_back_to_default_and_managed():
    undeclared = Table("undeclared", MetaData(), Column("value", Text()))

    assert table_database(undeclared) == DEFAULT_DATABASE_ALIAS
    assert table_managed(undeclared) is True


def test_table_database_rejects_a_non_string_alias():
    broken = Table("broken", MetaData(), Column("value", Text()), info={"database": 1})

    with pytest.raises(TypeError, match="non-string database alias"):
        table_database(broken)


def test_table_managed_rejects_a_non_boolean_flag():
    broken = Table("broken", MetaData(), Column("value", Text()), info={"managed": "yes"})

    with pytest.raises(TypeError, match="non-boolean managed flag"):
        table_managed(broken)


def test_every_model_declares_the_database_it_migrates_with():
    import apps.models  # noqa: F401

    assert Base.metadata.tables
    for table in Base.metadata.tables.values():
        assert "database" in table.info, f"{table.fullname} must declare a migration database alias"
        assert table_database(table), f"{table.fullname} declared an empty database alias"
        assert table_managed(table) is True


def test_models_route_to_the_alias_they_declare():
    from apps.models.finance import ExchangeRate
    from apps.models.market import IndicatorObservation
    from apps.models.raw import SourceRecord
    from apps.models.reference import Instrument

    assert table_database(SourceRecord.__table__) == "default"
    assert table_database(IndicatorObservation.__table__) == "default"
    assert table_database(Instrument.__table__) == "default"
    assert table_database(ExchangeRate.__table__) == "default"
