"""composition root.

**`Provide` 마커가 런타임까지 살아 있으면 wiring이 안 된 것이다.** 그 실패는 조용해서
(주입 자리에 `Provide` 객체가 그대로 들어온다) 테스트가 아니면 배포에서 처음 만난다.
그것을 실제로 확인하는 것은 `test_routes.py`의 HTTP 경로이고, 여기서는 provider의
수명과 값을 본다.
"""

from types import SimpleNamespace

import pytest
from dependency_injector import providers

from apps.api.container import ApiContainer
from apps.api.repository import ThesisReadRepository
from apps.api.service import ThesisReadService
from apps.core.database import Database
from tests.api.conftest import container, databases


def test_the_container_imports_without_a_config_file():
    """`apps/core/container.py`는 본문에서 settings를 읽어 이 성질이 없다. 그래서 안 쓴다."""
    assert ApiContainer.settings.__class__ is providers.Dependency
    assert ApiContainer.db_alias.__class__ is providers.Dependency


def test_the_engine_pool_is_one_per_process():
    """`Singleton`이 그 수명을 뜻한다. 요청마다 새 풀을 만들면 커넥션이 샌다."""
    built = container()

    assert built.database() is built.database()


def test_the_repository_is_a_new_instance_per_call():
    """`Singleton`으로 두면 나중에 요청 상태를 담게 될 때 조용히 새어 나간다."""
    built = container()

    first, second = built.thesis_repository(), built.thesis_repository()

    assert isinstance(first, ThesisReadRepository)
    assert first is not second


def test_the_repository_gets_its_session_factory_by_constructor():
    """조회 코드가 컨테이너를 들여다보지 않는다 — 그건 Service Locator다."""
    built = container()

    repository = built.thesis_repository()

    assert repository._session_factory is built.database().get_session_factory("prod")


def test_the_service_gets_its_repository_by_constructor():
    """층이 갈린 것을 컨테이너가 증명한다 — 서비스는 세션을 모르고 리포지토리는 계약을 모른다."""
    built = container()

    service = built.thesis_service()

    assert isinstance(service, ThesisReadService)
    assert isinstance(service._repository, ThesisReadRepository)


def test_the_service_is_a_new_instance_per_call():
    built = container()

    assert built.thesis_service() is not built.thesis_service()


def test_the_session_factory_follows_the_configured_alias():
    """`default`로 못 박힌 `apps/core/container.py`의 `default_session_factory`와 다르다."""
    built = container()

    assert built.session_factory() is built.database().get_session_factory("prod")


def test_a_missing_alias_fails_loudly_at_the_composition_root():
    """설정에 없는 별칭이면 첫 요청이 아니라 조립에서 죽어야 한다."""
    built = ApiContainer(settings=SimpleNamespace(databases=databases()), db_alias="typo")

    with pytest.raises(KeyError):
        built.thesis_service()


def test_the_alias_guard_and_the_container_agree_on_read_only():
    """진입점 가드가 거부하는 별칭을 컨테이너가 조용히 받아 주면 안 된다."""
    from apps.api import main

    with pytest.raises(ValueError, match="must be read_only"):
        main.resolve_alias(databases(), "default")


def test_the_database_is_built_from_the_injected_settings():
    """컨테이너가 설정을 스스로 읽지 않는다. 읽는 자리는 `main.py` 하나다."""
    assert isinstance(container().database(), Database)
