from collections.abc import Mapping
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import ConnectionPool
from redis.asyncio import Redis as RedisClient


class RedisConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    url: str = Field(min_length=1)
    max_connections: int | None = Field(default=None, gt=0)
    decode_responses: bool = False
    health_check_interval: int = Field(default=30, ge=0)


Resource = TypeVar("Resource")


class Redis:
    def __init__(self, redises: Mapping[str, RedisConfig]) -> None:
        if "default" not in redises:
            raise ValueError("Redis configuration must include a 'default' redis")

        self._pools = {
            alias: ConnectionPool.from_url(
                config.url,
                max_connections=config.max_connections,
                decode_responses=config.decode_responses,
                health_check_interval=config.health_check_interval,
            )
            for alias, config in redises.items()
        }
        self._clients = {alias: RedisClient(connection_pool=pool) for alias, pool in self._pools.items()}

    def _get(self, resources: Mapping[str, Resource], alias: str) -> Resource:
        try:
            return resources[alias]
        except KeyError as error:
            raise KeyError(f"Unknown redis alias: {alias!r}") from error

    def get_pool(self, alias: str = "default") -> ConnectionPool:
        return self._get(self._pools, alias)

    def get_client(self, alias: str = "default") -> RedisClient:
        return self._get(self._clients, alias)

    @property
    def pool(self) -> ConnectionPool:
        return self.get_pool()

    @property
    def client(self) -> RedisClient:
        return self.get_client()

    async def dispose(self) -> None:
        for client in self._clients.values():
            await client.aclose()
        for pool in self._pools.values():
            await pool.disconnect()
