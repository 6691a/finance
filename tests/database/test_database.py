import pytest
from dependency_injector import providers
from pydantic import ValidationError

from core.container import Container
from core.database import Database, _connect_args_for
from tests.helpers import SettingsForTest as Settings

DEFAULT_URL = "postgresql+asyncpg://news2:news2@localhost:15432/news2"
ANALYTICS_URL = "postgresql+asyncpg://news2:news2@localhost:15432/analytics"


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


def test_database_aliases_are_loaded_from_explicit_configuration():
    settings = make_settings()

    assert settings.databases["default"].url == DEFAULT_URL


def test_explicit_databases_preserve_aliases():
    settings = make_settings(
        databases={
            "default": {"url": ANALYTICS_URL},
            "analytics": {"url": ANALYTICS_URL},
        }
    )

    assert settings.databases["default"].url == ANALYTICS_URL
    assert settings.databases["analytics"].url == ANALYTICS_URL


def test_legacy_database_url_is_not_converted_to_default():
    with pytest.raises(ValidationError, match="default"):
        make_settings(database_url=DEFAULT_URL, databases={})


def test_database_settings_require_default_alias():
    with pytest.raises(ValidationError, match="default"):
        make_settings(databases={"analytics": {"url": ANALYTICS_URL}})


def test_database_config_values_are_immutable():
    settings = make_settings()

    with pytest.raises(ValidationError):
        settings.databases["default"].url = ANALYTICS_URL


@pytest.mark.asyncio
async def test_database_selects_default_and_named_aliases():
    settings = make_settings(
        databases={
            "default": {"url": DEFAULT_URL},
            "analytics": {"url": ANALYTICS_URL},
        }
    )
    database = Database(settings.databases)

    try:
        assert database.get_engine() is database.get_engine("default")
        assert database.get_engine("analytics") is not database.get_engine()
        assert database.get_session_factory() is database.get_session_factory("default")
        assert database.get_session_factory("analytics") is not database.get_session_factory()
        assert database.engine is database.get_engine()
        assert database.session_factory is database.get_session_factory()
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_unknown_database_alias_raises_key_error():
    settings = make_settings()
    database = Database(settings.databases)

    try:
        with pytest.raises(KeyError, match="missing"):
            database.get_engine("missing")
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_container_keeps_database_singleton_and_injects_all_aliases():
    settings = make_settings(
        databases={
            "default": {"url": DEFAULT_URL},
            "analytics": {"url": ANALYTICS_URL},
        }
    )
    test_container = Container()
    test_container.settings.override(providers.Object(settings))

    try:
        first = test_container.database()
        second = test_container.database()

        assert first is second
        assert first.get_engine("analytics") is not first.get_engine()
    finally:
        await first.dispose()
        test_container.database.reset()
        test_container.settings.reset_override()


@pytest.mark.asyncio
async def test_migration_only_alias_is_not_available_to_runtime_database():
    settings = make_settings(
        databases={
            "default": {"url": DEFAULT_URL},
            "market_read": {"url": ANALYTICS_URL, "read_only": True},
            "market_migration": {
                "url": ANALYTICS_URL,
                "runtime_enabled": False,
                "migration": {
                    "version_path": "migrations/market/versions",
                    "model_modules": ["apps.models.raw"],
                },
            },
        }
    )
    database = Database(settings.databases)

    try:
        assert database.get_engine("market_read") is not database.get_engine()
        with pytest.raises(KeyError, match="market_migration"):
            database.get_engine("market_migration")
    finally:
        await database.dispose()


def test_migration_only_alias_requires_migration_settings():
    with pytest.raises(ValidationError, match="migration"):
        make_settings(
            databases={
                "default": {"url": DEFAULT_URL},
                "market_migration": {"url": ANALYTICS_URL, "runtime_enabled": False},
            }
        )


def test_default_alias_must_be_runtime_enabled():
    with pytest.raises(ValidationError, match="default"):
        make_settings(
            databases={
                "default": {
                    "url": DEFAULT_URL,
                    "runtime_enabled": False,
                    "migration": {"version_path": "migrations/default/versions"},
                },
            }
        )


def test_read_only_alias_sets_postgres_read_only_transaction():
    settings = make_settings(
        databases={
            "default": {"url": DEFAULT_URL},
            "market_read": {"url": ANALYTICS_URL, "read_only": True},
        }
    )

    assert _connect_args_for(settings.databases["market_read"]) == {
        "server_settings": {
            "timezone": "UTC",
            "default_transaction_read_only": "on",
        }
    }
