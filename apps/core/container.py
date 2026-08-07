from dependency_injector import containers, providers

from apps.core.config import settings as core_setting
from apps.core.database import Database
from apps.core.redis import Redis


class Container(containers.DeclarativeContainer):
    settings = providers.Object(core_setting)
    database = providers.Singleton(
        Database,
        databases=settings.provided.databases,
    )
    redis = providers.Singleton(
        Redis,
        redises=settings.provided.redises,
    )

    # `default` 별칭의 세션 팩토리. 리포지토리는 어느 데이터베이스를 쓰는지 모르고
    # 여기서 정한다. 다른 별칭으로 붙이려면 `get_session_factory.call("<별칭>")`으로 바꾼다.
    default_session_factory = database.provided.session_factory


container = Container()
