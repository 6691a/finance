from collections.abc import Mapping
from datetime import datetime
from typing import TypeVar

from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field, model_validator
from sqlalchemy import BigInteger, DateTime, Table, func
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class MigrationConfig(PydanticBaseModel):
    """마이그레이션 대상 여부와 모델 모듈.

    리비전 파일은 별칭마다 나뉘지 않고 `migrations/versions` 하나를 공유한다.
    한 리비전 파일 안에서 별칭별 `upgrade_<alias>()` 함수로 갈라진다.
    """

    model_config = ConfigDict(frozen=True)

    model_modules: tuple[str, ...] = ()
    enabled: bool = True


class DatabaseConfig(PydanticBaseModel):
    model_config = ConfigDict(frozen=True)

    url: str = Field(min_length=1)
    runtime_enabled: bool = True
    read_only: bool = False
    migration: MigrationConfig | None = None

    @model_validator(mode="after")
    def require_migration_for_non_runtime(self) -> "DatabaseConfig":
        if not self.runtime_enabled and (self.migration is None or not self.migration.enabled):
            raise ValueError("Runtime-disabled database must have enabled migration settings")
        return self


def _connect_args_for(config: DatabaseConfig) -> dict[str, dict[str, str]]:
    server_settings = {"timezone": "UTC"}
    if config.read_only:
        server_settings["default_transaction_read_only"] = "on"
    return {"server_settings": server_settings}


DEFAULT_DATABASE_ALIAS = "default"


def table_options(
    *,
    comment: str | None,
    database: str = DEFAULT_DATABASE_ALIAS,
    managed: bool = True,
) -> dict[str, object]:
    """`__table_args__`의 마지막 dict를 만든다.

    스키마는 지정하지 않는다. 연결의 `search_path`(PostgreSQL 기본 `public`)를 따른다.

    `comment`는 이 프로젝트가 만든 테이블이면 항상 채운다. `None`은 다른 시스템이 이미
    만들어 둔 테이블을 그대로 미러링할 때만 쓴다. 실제 DB에 주석이 없는데 모델에만 주석을
    달면 autogenerate가 매번 `COMMENT ON` 차이를 만들어 낸다.

    테이블이 어느 마이그레이션 데이터베이스 별칭에 속하는지 모델에서 직접 선언한다.
    Alembic은 이 값을 보고 현재 별칭이 소유하지 않는 테이블을 autogenerate 대상에서 제외한다.

    `managed=False`는 스키마를 이 프로젝트가 만들지 않는 테이블이다. 매핑은 유지해서
    읽고 쓸 수 있지만 어떤 별칭의 autogenerate에도 나오지 않는다. Django의
    `Meta.managed = False`와 같은 뜻이며, 런타임 쓰기 금지와는 관계가 없다.
    """
    return {
        "comment": comment,
        "info": {"database": database, "managed": managed},
    }


def table_database(table: Table) -> str:
    """테이블이 선언한 마이그레이션 데이터베이스 별칭. 선언이 없으면 `default`."""
    database = table.info.get("database", DEFAULT_DATABASE_ALIAS)
    if not isinstance(database, str):
        raise TypeError(f"Table {table.fullname!r} declared a non-string database alias")
    return database


def table_managed(table: Table) -> bool:
    """이 프로젝트의 마이그레이션이 스키마를 소유하는 테이블인지. 선언이 없으면 소유한다."""
    managed = table.info.get("managed", True)
    if not isinstance(managed, bool):
        raise TypeError(f"Table {table.fullname!r} declared a non-boolean managed flag")
    return managed


class Base(DeclarativeBase):
    """ORM 모델의 공용 declarative base."""


class EntityBase(Base):
    """모든 애플리케이션 테이블이 공유하는 식별자와 UTC 시각.

    기본키는 데이터베이스가 채우는 `BIGSERIAL`이다. 값은 INSERT 이후에만 알 수 있으므로
    쓰기 쪽에서는 `RETURNING id`나 `flush()`로 받아야 한다.
    """

    __abstract__ = True

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="레코드 고유 식별자",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="레코드 생성 시각(UTC)",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="레코드 최종 수정 시각(UTC)",
    )


Resource = TypeVar("Resource")


class Database:
    def __init__(self, databases: Mapping[str, DatabaseConfig]) -> None:
        runtime_databases = {alias: config for alias, config in databases.items() if config.runtime_enabled}
        if "default" not in runtime_databases:
            raise ValueError("Database configuration must include a runtime-enabled 'default' database")

        self._engines = {
            alias: create_async_engine(
                config.url,
                pool_pre_ping=True,
                connect_args=_connect_args_for(config),
            )
            for alias, config in runtime_databases.items()
        }
        self._session_factories = {
            alias: async_sessionmaker(
                bind=engine,
                expire_on_commit=False,
            )
            for alias, engine in self._engines.items()
        }

    def _get(self, resources: Mapping[str, Resource], alias: str) -> Resource:
        try:
            return resources[alias]
        except KeyError as error:
            raise KeyError(f"Unknown database alias: {alias!r}") from error

    def get_engine(self, alias: str = "default") -> AsyncEngine:
        return self._get(self._engines, alias)

    def get_session_factory(self, alias: str = "default") -> async_sessionmaker[AsyncSession]:
        return self._get(self._session_factories, alias)

    @property
    def engine(self) -> AsyncEngine:
        return self.get_engine()

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        return self.get_session_factory()

    async def dispose(self) -> None:
        for engine in self._engines.values():
            await engine.dispose()
