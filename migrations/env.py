import asyncio
import importlib
import logging
from logging.config import fileConfig

import sqlalchemy as sa
from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from core.config import settings
from core.database import Base, DatabaseConfig
from migrations.routing import (
    excluded_tables,
    include_table,
    mapped_tables,
    version_table,
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

logger = logging.getLogger("alembic.env")


def _migration_databases() -> dict[str, DatabaseConfig]:
    """Aliases this run migrates, in the order `migrations.cli` recorded."""
    names = [name.strip() for name in config.get_main_option("databases", "").split(",") if name.strip()]
    if not names:
        raise RuntimeError("Alembic requires the 'databases' option listing migration aliases")

    databases: dict[str, DatabaseConfig] = {}
    for alias in names:
        try:
            database = settings.databases[alias]
        except KeyError as error:
            raise KeyError(f"Unknown database alias: {alias!r}") from error
        if database.migration is None or not database.migration.enabled:
            raise ValueError(f"Database alias {alias!r} has no enabled migration settings")
        databases[alias] = database
    return databases


_databases = _migration_databases()

# Every alias' models are imported, not only the current one. Routing is decided
# per table by `table_options(database=...)`, and a table can only be excluded
# from an alias if its model was loaded in the first place.
_model_modules = {
    module_name
    for database in _databases.values()
    if database.migration is not None
    for module_name in database.migration.model_modules
}
for module_name in sorted(_model_modules):
    importlib.import_module(module_name)

target_metadata = Base.metadata if _model_modules else None

PARTITION_QUERY = """
SELECT n.nspname, c.relname
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relispartition
"""


def _filters(database_alias: str, partitions: frozenset[tuple[str, str]]):
    """`include_name` and `include_object` hooks bound to one alias.

    `include_name` sees only what reflection found in the database, so it keeps
    another alias' tables from looking like tables that went missing.
    `include_object` also sees the model metadata, which is what stops this alias
    from emitting a CREATE for a table another alias owns. Both are needed.
    """
    tables = Base.metadata.tables.values()
    excluded = excluded_tables(tables, database_alias)
    mapped = mapped_tables(tables)

    def include_name(name: str | None, type_: str, parent_names: dict[str, str | None]) -> bool:
        if type_ == "table":
            return include_table(
                name,
                parent_names.get("schema_name"),
                reflected=True,
                partitions=partitions,
                excluded=excluded,
                mapped=mapped,
            )
        return True

    def include_object(
        object_: sa.schema.SchemaItem,
        name: str | None,
        type_: str,
        reflected: bool,
        compare_to: object,
    ) -> bool:
        if type_ == "table":
            return include_table(
                name,
                object_.schema,
                reflected=reflected,
                partitions=partitions,
                excluded=excluded,
                mapped=mapped,
            )
        return True

    return include_name, include_object


def _configure(database_alias: str, partitions: frozenset[tuple[str, str]], **kwargs: object) -> None:
    include_name, include_object = _filters(database_alias, partitions)
    context.configure(
        target_metadata=target_metadata,
        include_name=include_name,
        include_object=include_object,
        compare_type=True,
        compare_server_default=True,
        # Aliases share one PostgreSQL instance, so each needs its own pointer.
        version_table=version_table(database_alias),
        # Autogenerate writes each alias' operations into its own section of the
        # single revision file. `script.py.mako` reads these tokens back.
        upgrade_token=f"{database_alias}_upgrades",
        downgrade_token=f"{database_alias}_downgrades",
        **kwargs,
    )


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Emits the statements for every alias to the same output, in configuration
    order, so `--sql` shows one script for the whole run.
    """
    for alias, database in _databases.items():
        logger.info("Migrating database %s", alias)
        _configure(
            alias,
            frozenset(),
            url=database.url,
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
        )

        with context.begin_transaction():
            context.run_migrations(engine_name=alias)


def do_run_migrations(connection: Connection, database_alias: str) -> None:
    partitions = frozenset((schema, name) for schema, name in connection.execute(sa.text(PARTITION_QUERY)).all())
    # This read opened an implicit transaction. `context.begin_transaction()`
    # only takes ownership - and therefore only commits - when the connection
    # has no transaction of its own, so it has to be closed here. Without this
    # the whole migration is silently rolled back on connection close.
    connection.rollback()

    _configure(database_alias, partitions, connection=connection)

    with context.begin_transaction():
        context.run_migrations(engine_name=database_alias)


async def run_async_migrations() -> None:
    """Migrate each alias with its own engine and credentials, one at a time."""
    for alias, database in _databases.items():
        logger.info("Migrating database %s", alias)
        connectable = create_async_engine(
            database.url,
            poolclass=pool.NullPool,
        )

        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations, alias)

        await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
