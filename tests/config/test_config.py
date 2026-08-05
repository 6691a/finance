from textwrap import dedent

from core.config import Settings


def test_settings_loads_every_field_from_yaml_only(monkeypatch, tmp_path):
    (tmp_path / "config.yaml").write_text(
        dedent(
            """
            databases:
              default:
                url: postgresql+asyncpg://yaml:y@localhost/yaml
            redises:
              default:
                url: redis://localhost/7
            kis_app_key: yaml-key
            kis_app_secret: yaml-secret
            kis_rest_domain: https://yaml.example.com
            kis_websocket_domain: wss://yaml.example.com
            fred_api_key: yaml-fred
            ecos_api_key: yaml-ecos
            sentry_dsn: https://yaml.example.com/1
            sentry_environment: yaml
            sentry_release: yaml@local
            sentry_traces_sample_rate: 0.2
            sentry_error_sample_rate: 0.3
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("kis_app_key=dotenv-key\n", encoding="utf-8")
    monkeypatch.setenv("KIS_APP_KEY", "environment-key")
    monkeypatch.chdir(tmp_path)

    settings = Settings()

    assert settings.kis_app_key == "yaml-key"
    assert settings.databases["default"].url.endswith("/yaml")
    assert settings.redises["default"].url == "redis://localhost/7"
