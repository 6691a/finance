import pytest
from dependency_injector import providers
from pydantic import ValidationError

from apps.core.container import Container
from apps.core.redis import Redis
from tests.helpers import SettingsForTest as Settings

DEFAULT_URL = "redis://localhost:16379/0"
STREAM_URL = "redis://localhost:16379/1"


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "databases": {
            "default": {"url": "postgresql+asyncpg://finance:finance@localhost:15432/finance"},
        },
        "redises": {
            "default": {"url": DEFAULT_URL},
            "stream": {"url": STREAM_URL},
        },
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


def test_redis_aliases_are_loaded_from_explicit_configuration():
    settings = make_settings()

    assert settings.redises["default"].url == DEFAULT_URL


def test_explicit_redises_preserve_aliases():
    settings = make_settings(
        redises={
            "default": {"url": STREAM_URL},
            "stream": {"url": STREAM_URL, "decode_responses": False},
        }
    )

    assert settings.redises["default"].url == STREAM_URL
    assert settings.redises["stream"].decode_responses is False


def test_legacy_redis_url_is_not_converted_to_default():
    with pytest.raises(ValidationError, match="default"):
        make_settings(redis_url=DEFAULT_URL, redises={})


def test_redis_settings_require_default_alias():
    with pytest.raises(ValidationError, match="default"):
        make_settings(redises={"stream": {"url": STREAM_URL}})


def test_redis_config_values_are_immutable():
    settings = make_settings()

    with pytest.raises(ValidationError):
        settings.redises["default"].url = STREAM_URL


@pytest.mark.asyncio
async def test_redis_selects_default_and_named_aliases():
    settings = make_settings(
        redises={
            "default": {"url": DEFAULT_URL},
            "stream": {"url": STREAM_URL},
        }
    )
    redis = Redis(settings.redises)

    try:
        assert redis.get_client() is redis.get_client("default")
        assert redis.get_client("stream") is not redis.get_client()
        assert redis.get_pool() is redis.get_pool("default")
        assert redis.get_pool("stream") is not redis.get_pool()
        assert redis.client is redis.get_client()
        assert redis.pool is redis.get_pool()
    finally:
        await redis.dispose()


@pytest.mark.asyncio
async def test_unknown_redis_alias_raises_key_error():
    settings = make_settings()
    redis = Redis(settings.redises)

    try:
        with pytest.raises(KeyError, match="missing"):
            redis.get_client("missing")
    finally:
        await redis.dispose()


@pytest.mark.asyncio
async def test_container_keeps_redis_singleton_and_injects_all_aliases():
    settings = make_settings(
        redises={
            "default": {"url": DEFAULT_URL},
            "stream": {"url": STREAM_URL},
        }
    )
    test_container = Container()
    test_container.settings.override(providers.Object(settings))

    try:
        first = test_container.redis()
        second = test_container.redis()

        assert first is second
        assert first.get_client("stream") is not first.get_client()
    finally:
        await first.dispose()
        test_container.redis.reset()
        test_container.settings.reset_override()
