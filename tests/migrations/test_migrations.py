from pathlib import Path

import pytest
from pydantic import ValidationError

from migrations.cli import PROJECT_ROOT, VERSION_PATH, _parser, build_alembic_config, migration_aliases
from tests.helpers import SettingsForTest as Settings

DEFAULT_URL = "postgresql+asyncpg://finance:finance@localhost:15432/finance"
ANALYTICS_URL = "postgresql+asyncpg://finance:finance@localhost:15432/analytics"


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "databases": {
            "default": {"url": DEFAULT_URL},
            "analytics": {"url": ANALYTICS_URL},
        },
        "redises": {"default": {"url": "redis://localhost:16379/0"}},
        "kis_app_key": "key",
        "kis_app_secret": "secret",
        "kis_rest_domain": "https://example.com",
        "kis_websocket_domain": "ws://example.com",
        "fred_api_key": "fred",
        "ecos_api_key": "ecos",
        "sentry_dsn": "",
        "sentry_environment": "test",
        "sentry_release": "test",
        "sentry_traces_sample_rate": 0.0,
        "sentry_error_sample_rate": 0.0,
    }
    values.update(overrides)
    return Settings.model_validate(values)


def with_market_migration() -> Settings:
    return make_settings(
        databases={
            "default": {
                "url": DEFAULT_URL,
                "migration": {"model_modules": ["apps.models"]},
            },
            "market_read": {"url": ANALYTICS_URL, "read_only": True},
            "market_migration": {
                "url": ANALYTICS_URL,
                "runtime_enabled": False,
                "migration": {"model_modules": ["apps.models"]},
            },
        }
    )


def test_migration_aliases_lists_only_migration_enabled_databases():
    assert migration_aliases(with_market_migration()) == ("default", "market_migration")


def test_migration_aliases_rejects_a_project_with_no_migration_database():
    with pytest.raises(ValueError, match="No database alias has enabled migration settings"):
        migration_aliases(make_settings())


def test_build_alembic_config_shares_one_version_path_across_aliases():
    config = build_alembic_config(with_market_migration())

    assert config.get_main_option("databases") == "default, market_migration"
    assert Path(config.get_main_option("version_locations")).resolve() == (PROJECT_ROOT / VERSION_PATH).resolve()


def test_build_alembic_config_rejects_a_project_with_no_migration_database():
    with pytest.raises(ValueError, match="No database alias has enabled migration settings"):
        build_alembic_config(make_settings())


def test_migration_only_alias_requires_migration_settings():
    with pytest.raises(ValidationError, match="migration"):
        make_settings(
            databases={
                "default": {"url": DEFAULT_URL},
                "market_migration": {"url": ANALYTICS_URL, "runtime_enabled": False},
            }
        )


def test_just_recipes_do_not_take_an_alias():
    justfile = Path("justfile").read_text(encoding="utf-8")

    assert "migrate +args:" in justfile
    assert "migrations.cli {{args}}" in justfile
    assert "makemigrations message:" in justfile
    assert 'revision --autogenerate -m "{{message}}"' in justfile


def test_revision_message_accepts_unquoted_just_arguments():
    args = _parser().parse_args(["revision", "--autogenerate", "-m", "create", "market", "tables"])

    assert args.message == ["create", "market", "tables"]
