"""Which tables and which revision pointer belong to one database alias.

Kept out of `env.py` because that module can only be imported inside a running
Alembic context, and these rules have to be testable on their own.
"""

from collections.abc import Container, Iterable

from sqlalchemy import Table

from core.database import DEFAULT_DATABASE_ALIAS, table_database, table_managed

TableKey = tuple[str | None, str | None]


def excluded_tables(tables: Iterable[Table], database_alias: str) -> frozenset[TableKey]:
    """Tables this alias must not autogenerate against.

    Two reasons. A table another alias owns: every alias points at the same
    PostgreSQL instance, so without hiding it the current alias would emit a
    DROP for it. A table declared `managed=False`: this project maps it but
    does not own its schema, so no alias may create, alter, or drop it.
    """
    return frozenset(
        (table.schema, table.name)
        for table in tables
        if not table_managed(table) or table_database(table) != database_alias
    )


def mapped_tables(tables: Iterable[Table]) -> frozenset[TableKey]:
    """Every table this project maps, whichever alias owns it."""
    return frozenset((table.schema, table.name) for table in tables)


def include_table(
    name: str | None,
    schema: str | None,
    *,
    reflected: bool,
    partitions: Container[TableKey],
    excluded: Container[TableKey],
    mapped: Container[TableKey],
) -> bool:
    """Whether one alias' autogenerate run may act on this table.

    Alembic asks twice with different hooks. `include_name` only sees names
    reflected from the database, so it alone would let a table another alias
    owns through from the model metadata and emit a CREATE for it. `include_object`
    sees both sides. Both hooks delegate here so the two answers cannot drift.
    """
    if (schema, name) in partitions:
        # Created by migrations and by the maintenance DAG, never by a model, so
        # autogenerate must not read them as tables that went missing.
        return False
    if (schema, name) in excluded:
        return False
    # A reflected table that no model maps is Airflow metadata, another service's
    # table, an extension. Never emit a DROP for something this project did not
    # create. On the metadata side there is nothing in the database yet, so the
    # check must not apply there.
    return not reflected or (schema, name) in mapped


def version_table(database_alias: str) -> str:
    """Aliases share one PostgreSQL instance, so each needs its own revision pointer.

    `default` keeps the plain name so already-stamped databases stay valid.
    """
    if database_alias == DEFAULT_DATABASE_ALIAS:
        return "alembic_version"
    return f"alembic_version_{database_alias}"
