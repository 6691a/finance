"""HTTP 층. **HTTP만 안다.**

쿼리 파라미터와 404가 전부이고, 모양을 바꾸는 것은 `apps/api/service/`,
행을 읽는 것은 `apps/api/repository/`다.

**파일은 리소스 단위로 나눈다**(`apps/api/schemas/`와 같은 규칙). 각 파일이 자기
`router`를 갖고 경로 접두(`prefix`)와 `tags`도 자기가 정한다 — 리소스가 늘 때
`app.py`가 아니라 새 파일 하나만 는다.

`__init__.py`는 **재수출만** 한다. `routers`에 넣는 것을 빠뜨리면 라우트가 조용히
사라지므로 `tests/api/test_routes.py`가 등록된 경로를 확인한다.

**튜플의 순서가 계약이다.** `quality_router`가 `forecast_router`보다 앞에 있어야
`/api/forecasts/quality`가 뒤엣것의 동적 경로에 먹히지 않는다 — FastAPI는 먼저 등록된
라우트를 먼저 맞춰 본다. 같은 테스트가 실제 요청을 보내 그것을 확인한다.

**컨테이너 wiring은 이 패키지를 통째로 건다**(`WiringConfiguration(packages=[...])`).
모듈을 하나씩 적으면 새 리소스를 더할 때 `container.py`도 함께 고쳐야 하고, 빠뜨리면
`Provide` 객체가 그대로 주입되어 조용히 틀린다.
"""

from fastapi import APIRouter

from apps.api.routes.collection import router as collection_router
from apps.api.routes.document import router as document_router
from apps.api.routes.event import router as event_router
from apps.api.routes.forecast import router as forecast_router
from apps.api.routes.health import router as health_router
from apps.api.routes.indicator import router as indicator_router
from apps.api.routes.llm_run import router as llm_run_router
from apps.api.routes.positioning import router as positioning_router
from apps.api.routes.quality import router as quality_router
from apps.api.routes.quote import router as quote_router
from apps.api.routes.relation import router as relation_router

routers: tuple[APIRouter, ...] = (
    health_router,
    quote_router,
    indicator_router,
    document_router,
    positioning_router,
    event_router,
    collection_router,
    relation_router,
    llm_run_router,
    quality_router,
    forecast_router,
)

__all__ = [
    "collection_router",
    "document_router",
    "event_router",
    "forecast_router",
    "health_router",
    "indicator_router",
    "llm_run_router",
    "positioning_router",
    "quality_router",
    "quote_router",
    "relation_router",
    "routers",
]
