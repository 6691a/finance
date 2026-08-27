"""진입점. **`config.yaml` 없이 import돼야 한다**(`apps/realtime/main.py`가 세운 규칙)."""

import pytest

from apps.api import main
from apps.core.database import DatabaseConfig


def test_the_module_imports_without_a_config_file():
    """`settings`를 모듈 본문에서 읽으면 테스트와 도구가 이 파일을 열지도 못한다."""
    assert main.DB_ALIAS == "prod"
    assert main.DEFAULT_PORT == 8000


def test_the_alias_is_a_constant_not_an_env_knob():
    """`read_only` 별칭이 하나뿐이라 개발·운영 어디서나 값이 같다.

    손잡이가 아닌 것을 환경변수로 두면 `.env` 파일 둘과 그 정합성 검사가 딸려 온다.
    """
    import pathlib

    source = pathlib.Path(main.__file__).read_text()
    assert "DB_ALIAS\", " not in source  # os.environ.get("...DB_ALIAS", ...)
    assert "environ" not in source.split("DEFAULT_HOST")[0]


def test_a_writable_alias_is_refused():
    """쓰기 라우트를 안 만드는 것으로 그치지 않는다. 연결 층에서 막는다."""
    databases = {"default": DatabaseConfig(url="postgresql+asyncpg://x/y", read_only=False)}

    with pytest.raises(ValueError, match="must be read_only"):
        main.resolve_alias(databases, "default")


def test_a_read_only_alias_passes():
    databases = {"prod": DatabaseConfig(url="postgresql+asyncpg://x/y", read_only=True)}

    main.resolve_alias(databases, "prod")


def test_an_unknown_alias_names_what_is_available():
    databases = {"prod": DatabaseConfig(url="postgresql+asyncpg://x/y", read_only=True)}

    with pytest.raises(ValueError, match="not in config.yaml"):
        main.resolve_alias(databases, "typo")


def test_a_runtime_disabled_alias_is_refused():
    """마이그레이션 전용 별칭에 붙으면 조회가 매번 죽는다."""
    databases = {
        "archive": DatabaseConfig(
            url="postgresql+asyncpg://x/y",
            read_only=True,
            runtime_enabled=False,
            migration={"enabled": True},
        )
    }

    with pytest.raises(ValueError, match="not runtime-enabled"):
        main.resolve_alias(databases, "archive")
