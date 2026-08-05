from dependency_injector import containers, providers

from core.config import settings as core_setting
from core.database import Database
from core.redis import Redis


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


container = Container()
