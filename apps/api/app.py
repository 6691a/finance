"""FastAPI 앱 조립.

**`apps.core.config`를 import하지 않는다.** 그 모듈은 본문에서 `settings = Settings()`를
불러 import만으로 `config.yaml`을 요구한다. 설정을 읽는 자리는 `main.py` 하나이고, 이
파일은 이미 채워진 컨테이너를 받는다 — 그래야 테스트가 접속 없이 앱을 통째로 세울 수
있다(`create_async_engine`은 연결하지 않는다).

## 정적 자산과 SPA fallback

화면은 클라이언트 렌더링 React SPA이고, 운영에는 Node 프로세스가 없다 — 이 앱이 Vite가
구운 `frontend/dist`를 같은 origin에서 제공한다. 그래서 CORS 설정이 필요 없다.

**API route를 먼저 등록하고 fallback을 마지막에 붙인다.** 순서가 뒤집히면 catch-all이
`/api/*`를 먼저 물어 모든 오류가 `index.html` 200이 된다.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from apps.api.container import ApiContainer
from apps.api.routes import routers

# 빌드 산출물. 컨테이너는 `/app/frontend/dist`, 체크아웃은 저장소 루트의 같은 자리다 —
# 둘 다 `apps/api/app.py`에서 두 단계 위가 뿌리라 경로 하나로 맞는다. 작업 디렉터리에
# 기대지 않는 이유는 `python -m apps.api.main`을 어디서 부르든 같아야 하기 때문이다.
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

# **`index.html` 응답에만 단다.** FastAPI 기본 `/docs`는 CDN에서 Swagger 자산을 받으므로
# 전역으로 걸면 그 화면이 빈 페이지가 된다.
SPA_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
)

# fallback이 삼키면 안 되는 접두. 이 아래의 404는 404로 남아야 한다 —
# `index.html` 200으로 바꾸면 프런트의 fetch가 HTML을 JSON으로 파싱하려다 죽는다.
API_PREFIXES = ("/api", "/healthz", "/docs", "/redoc", "/openapi.json")


def create_app(container: ApiContainer, dist: Path | None = None) -> FastAPI:
    """앱 하나. 컨테이너를 `app.container`에 붙여 두는 것은 공식 예제 형태다 —
    테스트가 `app.container.<provider>.override(...)`로 갈아끼울 수 있다.

    `dist`는 테스트가 빌드 없이 fallback을 검사할 수 있게 열어 둔 인자다. 운영은 기본값이다.
    """

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
    mount_spa(app, FRONTEND_DIST if dist is None else dist)
    return app


def mount_spa(app: FastAPI, dist: Path) -> None:
    """빌드 산출물을 붙인다. **없으면 아무 것도 붙이지 않는다.**

    Vite 개발과 Python 단위 테스트가 매번 frontend build를 선행하지 않게 하기 위해서다 —
    그때 UI 경로는 404이고 API는 정상 기동한다. 운영 이미지에 산출물이 실제로 들어갔는지는
    이미지 검사가 따로 실패시킨다.
    """
    index = dist / "index.html"
    if not index.is_file():
        return

    # Vite는 해시가 붙은 자산을 `assets/` 하나에 모은다. 없는 파일은 이 mount가 404를
    # 내고 아래 fallback까지 내려가지 않는다.
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> Response:
        """클라이언트 라우트를 새로고침해도 같은 화면이 뜨게 한다.

        **`/api` 아래는 그대로 404다.** catch-all이 여기까지 왔다는 것은 위의 어떤 라우트도
        맞지 않았다는 뜻이라, 접두를 보고 갈라야 API 오류가 HTML 200으로 위장되지 않는다.
        """
        path = "/" + full_path
        if path.startswith(API_PREFIXES):
            return JSONResponse({"detail": f"{path} not found"}, status_code=404)
        return FileResponse(index, headers={"Content-Security-Policy": SPA_CSP})
