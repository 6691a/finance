"""FastAPI 앱 조립.

**`apps.core.config`를 import하지 않는다.** 그 모듈은 본문에서 `settings = Settings()`를
불러 import만으로 `config.yaml`을 요구한다. 설정을 읽는 자리는 `main.py` 하나이고, 이
파일은 이미 채워진 컨테이너를 받는다 — 그래야 테스트가 접속 없이 앱을 통째로 세울 수
있다(`create_async_engine`은 연결하지 않는다).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.api.container import ApiContainer
from apps.api.routes import routers


def create_app(container: ApiContainer) -> FastAPI:
    """앱 하나. 컨테이너를 `app.container`에 붙여 두는 것은 공식 예제 형태다 —
    테스트가 `app.container.<provider>.override(...)`로 갈아끼울 수 있다."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            # 엔진 풀을 닫는다. `Singleton`이라 프로세스에 한 벌이다.
            await container.database().dispose()

    app = FastAPI(
        title="조회 API",
        description="기록을 읽는 읽기 전용 API. 쓰기 경로는 없다. 지금 있는 리소스는 시장 추론이다.",
        lifespan=lifespan,
    )
    app.container = container  # type: ignore[attr-defined]
    for router in routers:
        app.include_router(router)
    return app
