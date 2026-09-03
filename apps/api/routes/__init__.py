"""HTTP 층. **HTTP만 안다.**

쿼리 파라미터와 404가 전부이고, 모양을 바꾸는 것은 `apps/api/service/`,
행을 읽는 것은 `apps/api/repository/`다.

**파일은 리소스 단위로 나눈다**(`apps/api/schemas/`와 같은 규칙). 각 파일이 자기
`router`를 갖고 경로 접두(`prefix`)와 `tags`도 자기가 정한다 — 리소스가 늘 때
`app.py`가 아니라 새 파일 하나만 는다.

`__init__.py`는 **재수출만** 한다. `routers`에 넣는 것을 빠뜨리면 라우트가 조용히
사라진다 — 리소스를 더할 때 등록된 경로를 확인하는 테스트를 함께 만든다.

**컨테이너 wiring은 이 패키지를 통째로 건다**(`WiringConfiguration(packages=[...])`).
모듈을 하나씩 적으면 새 리소스를 더할 때 `container.py`도 함께 고쳐야 하고, 빠뜨리면
`Provide` 객체가 그대로 주입되어 조용히 틀린다.
"""

from fastapi import APIRouter

from apps.api.routes.health import router as health_router

routers: tuple[APIRouter, ...] = (health_router,)

__all__ = ["health_router", "routers"]
