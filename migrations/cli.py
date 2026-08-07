import argparse
from collections.abc import Sequence
from pathlib import Path

from alembic import command as alembic_command
from alembic.config import Config

from apps.core.config import Settings, settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Every alias shares one revision chain. A revision file splits into
# `upgrade_<alias>()` functions, so there is nothing to keep in separate
# directories and no way for the aliases to drift apart.
VERSION_PATH = Path("migrations/versions")


def migration_aliases(settings: Settings = settings) -> tuple[str, ...]:
    """Aliases a migration run touches, in configuration order."""
    aliases = tuple(
        alias
        for alias, database in settings.databases.items()
        if database.migration is not None and database.migration.enabled
    )
    if not aliases:
        raise ValueError("No database alias has enabled migration settings")
    return aliases


def build_alembic_config(
    settings: Settings = settings,
    project_root: Path = PROJECT_ROOT,
) -> Config:
    aliases = migration_aliases(settings)

    project_root = project_root.resolve()
    version_path = (project_root / VERSION_PATH).resolve()
    migrations_root = (project_root / "migrations").resolve()
    if not version_path.is_relative_to(migrations_root):
        raise ValueError("Migration version_path must be inside the migrations directory")

    alembic_config = Config(str(project_root / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(migrations_root))
    alembic_config.set_main_option("version_locations", str(version_path))
    # Read back by `script.py.mako` to emit one section per alias, and by
    # `env.py` to know which aliases to migrate.
    alembic_config.set_main_option("databases", ", ".join(aliases))
    return alembic_config


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a migration across every configured database alias")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("upgrade", "downgrade"):
        subparser = subparsers.add_parser(name)
        subparser.add_argument("revision")

    revision = subparsers.add_parser("revision")
    revision.add_argument("-m", "--message", nargs="+")
    revision.add_argument("--autogenerate", action="store_true")

    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = _parser()
    args = parser.parse_args(argv)
    config = build_alembic_config()
    version_path = Path(config.get_main_option("version_locations"))
    version_path.mkdir(parents=True, exist_ok=True)

    if args.command == "upgrade":
        alembic_command.upgrade(config, args.revision)
    elif args.command == "downgrade":
        alembic_command.downgrade(config, args.revision)
    else:
        alembic_command.revision(
            config,
            message=" ".join(args.message) if args.message else None,
            autogenerate=args.autogenerate,
            version_path=str(version_path),
        )


if __name__ == "__main__":
    main()
