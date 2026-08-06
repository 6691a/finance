from pathlib import Path

from sqlalchemy import Column, MetaData, Table, Text

from migrations.routing import (
    excluded_tables,
    include_table,
    mapped_tables,
    version_table,
)

EMPTY = frozenset()


def declared(name: str, *, database: str = "default", managed: bool = True) -> Table:
    return Table(
        name,
        MetaData(),
        Column("value", Text()),
        info={"database": database, "managed": managed},
    )


def allows(name: str, *, reflected: bool = False, **kwargs) -> bool:
    options = {"partitions": EMPTY, "excluded": EMPTY, "mapped": EMPTY, **kwargs}
    return include_table(name, None, reflected=reflected, **options)


def test_excluded_tables_hides_what_another_alias_owns():
    tables = [declared("mine"), declared("theirs", database="market_migration")]

    assert excluded_tables(tables, "default") == frozenset({(None, "theirs")})
    assert excluded_tables(tables, "market_migration") == frozenset({(None, "mine")})


def test_excluded_tables_hides_unmanaged_tables_from_every_alias():
    tables = [declared("external", managed=False)]

    assert excluded_tables(tables, "default") == frozenset({(None, "external")})
    assert excluded_tables(tables, "market_migration") == frozenset({(None, "external")})


def test_project_models_route_to_the_alias_they_declare():
    import apps.models  # noqa: F401
    from core.database import Base

    tables = Base.metadata.tables.values()

    # Every model table, `exchange_rate` included, is on `default`. Nothing is
    # left for another alias to own, so a `default` autogenerate run excludes
    # nothing and any other alias excludes everything.
    assert excluded_tables(tables, "default") == EMPTY
    assert (None, "exchange_rate") in excluded_tables(tables, "finance")
    assert (None, "instrument") in excluded_tables(tables, "finance")


def test_mapped_tables_lists_every_model_table():
    assert mapped_tables([declared("a"), declared("b")]) == frozenset({(None, "a"), (None, "b")})


def test_include_table_hides_tables_owned_by_another_alias():
    excluded = frozenset({(None, "indicator_observation")})

    assert allows("indicator_observation", excluded=excluded) is False
    assert allows("source_record", excluded=excluded) is True


def test_include_table_hides_partitions():
    assert allows("price_2026", partitions=frozenset({(None, "price_2026")})) is False


def test_include_table_never_drops_a_table_no_model_maps():
    mapped = frozenset({(None, "source_record")})

    # Reflected from the database and unknown to every model: another system owns it.
    assert allows("task_instance", reflected=True, mapped=mapped) is False
    assert allows("source_record", reflected=True, mapped=mapped) is True
    # Model metadata side: a table that does not exist yet must still be created.
    assert allows("source_record", reflected=False, mapped=mapped) is True


def test_each_alias_tracks_its_own_revision_pointer():
    assert version_table("default") == "alembic_version"
    assert version_table("market_migration") == "alembic_version_market_migration"


def test_revision_files_split_into_one_function_per_alias():
    template = Path("migrations/script.py.mako").read_text(encoding="utf-8")

    assert '_run(f"upgrade_{engine_name}")' in template
    assert '_run(f"downgrade_{engine_name}")' in template
    assert 'config.get_main_option("databases")' in template
