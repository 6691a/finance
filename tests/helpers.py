from pathlib import Path

from alembic import command as alembic_command
from pydantic_settings import SettingsConfigDict

from core.config import Settings
from migrations.cli import PROJECT_ROOT, VERSION_PATH, build_alembic_config


class SettingsForTest(Settings):
    model_config = SettingsConfigDict(
        yaml_file="__missing_test_config__.yaml",
        extra="ignore",
    )


def revision_files() -> list[Path]:
    return sorted((PROJECT_ROOT / VERSION_PATH).glob("*.py"))


NO_REVISION_REASON = "migrations/versions has no revision; run `just makemigrations` first"


def head_sql(capsys) -> str:
    """SQL an offline `upgrade head` emits for every migration alias."""
    alembic_command.upgrade(build_alembic_config(), "head", sql=True)
    return capsys.readouterr().out
