import importlib
import pkgutil
from pathlib import Path
from types import ModuleType

from alembic import command as alembic_command
from pydantic_settings import SettingsConfigDict

from apps.core.config import Settings
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


def models_defined_in(package: ModuleType) -> dict[str, type]:
    """모델 패키지의 하위 모듈이 정의한 테이블 모델 전부.

    **하위 모듈을 훑어서 찾는다.** `__init__.py`의 목록을 읽어 비교하면 그 목록이 틀린 것을
    못 잡는다. 등록은 클래스를 import하는 부수효과라, 하위 모듈에 모델을 넣고 `__init__.py`에
    이름을 안 더하면 `Base.metadata`에서 그 테이블이 사라지고 autogenerate가 `DROP TABLE`을 낸다.
    """
    from apps.core.database import EntityBase

    found: dict[str, type] = {}
    for info in pkgutil.iter_modules(package.__path__):
        module = importlib.import_module(f"{package.__name__}.{info.name}")
        for name, value in vars(module).items():
            if (
                isinstance(value, type)
                and issubclass(value, EntityBase)
                and value is not EntityBase
                and value.__module__ == module.__name__
            ):
                found[name] = value
    return found
