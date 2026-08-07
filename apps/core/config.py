from pydantic import field_validator
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from apps.core.database import DatabaseConfig
from apps.core.redis import RedisConfig


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        yaml_file="config.yaml",
        yaml_file_encoding="utf-8",
        extra="ignore",
    )

    databases: dict[str, DatabaseConfig]
    redises: dict[str, RedisConfig]

    kis_app_key: str
    kis_app_secret: str
    kis_rest_domain: str
    kis_websocket_domain: str

    fred_api_key: str

    ecos_api_key: str

    sentry_dsn: str
    sentry_environment: str
    sentry_release: str
    sentry_traces_sample_rate: float
    sentry_error_sample_rate: float

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        return (
            init_settings,
            YamlConfigSettingsSource(settings_cls),
        )

    @field_validator("databases")
    @classmethod
    def require_default_database(cls, databases: dict[str, DatabaseConfig]) -> dict[str, DatabaseConfig]:
        if "default" not in databases:
            raise ValueError("DATABASES must include a 'default' database")
        if not databases["default"].runtime_enabled:
            raise ValueError("DATABASES 'default' must be runtime-enabled")
        return databases

    @field_validator("redises")
    @classmethod
    def require_default_redis(cls, redises: dict[str, RedisConfig]) -> dict[str, RedisConfig]:
        if "default" not in redises:
            raise ValueError("REDISES must include a 'default' redis")
        return redises


settings = Settings()
