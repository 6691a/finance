"""빌드 산출물 제공과 SPA fallback.

**fallback이 API 404를 삼키면 안 된다.** 삼키면 프런트의 `fetch`가 HTML을 JSON으로
파싱하려다 죽고, 그 오류는 원인에서 한 단계 떨어진 자리에 뜬다.

`dist`를 인자로 받는 덕에 이 테스트는 `npm run build` 없이 돈다.
"""

from pathlib import Path

import httpx
import pytest

from apps.api.app import SPA_CSP, create_app
from tests.api.conftest import container


def build(tmp_path: Path) -> Path:
    """Vite 산출물의 최소 모양. `index.html` 하나와 `assets/` 하나다."""
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><div id=root></div>")
    (tmp_path / "assets" / "index-abc123.js").write_text("console.log(1)")
    return tmp_path


def client(dist: Path) -> httpx.AsyncClient:
    app = create_app(container(), dist=dist)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_the_api_boots_without_a_frontend_build(tmp_path: Path):
    """Vite 개발과 Python 단위 테스트가 매번 build를 선행하지 않게 한다."""
    async with client(tmp_path) as http:
        assert (await http.get("/healthz")).status_code == 200
        assert (await http.get("/runs")).status_code == 404


@pytest.mark.asyncio
async def test_a_client_route_refresh_serves_the_index(tmp_path: Path):
    """클라이언트 라우트를 새로고침해도 같은 화면이 떠야 한다."""
    async with client(build(tmp_path)) as http:
        reply = await http.get("/theses/812/graph")

    assert reply.status_code == 200
    assert "id=root" in reply.text


@pytest.mark.asyncio
async def test_the_fallback_never_turns_an_api_404_into_an_index_200(tmp_path: Path):
    async with client(build(tmp_path)) as http:
        assert (await http.get("/api/missing")).status_code == 404
        assert (await http.get("/api/llm-runs/9/nope")).status_code == 404
        assert (await http.get("/openapi.json/nope")).status_code == 404
        assert (await http.get("/healthz/nope")).status_code == 404
        assert (await http.get("/assets/missing.js")).status_code == 404


@pytest.mark.asyncio
async def test_the_built_assets_are_served_from_the_same_origin(tmp_path: Path):
    """외부 CDN을 쓰지 않는다. script·CSS·font가 전부 build 산출물이다."""
    async with client(build(tmp_path)) as http:
        reply = await http.get("/assets/index-abc123.js")

    assert reply.status_code == 200
    assert reply.text == "console.log(1)"


@pytest.mark.asyncio
async def test_the_csp_rides_on_the_index_only(tmp_path: Path):
    """CDN을 쓰는 FastAPI 기본 `/docs`에 전역 적용하면 그 화면이 빈 페이지가 된다."""
    async with client(build(tmp_path)) as http:
        index = await http.get("/runs")
        docs = await http.get("/docs")

    assert index.headers["content-security-policy"] == SPA_CSP
    assert "content-security-policy" not in docs.headers
    assert "'self'" in SPA_CSP
    assert "frame-ancestors 'none'" in SPA_CSP


def test_sentry_is_off_unless_the_environment_says_otherwise():
    """**개발 머신의 config.yaml이 운영 것의 사본이라** DSN이 거기 들어 있다.

    켜는 판단을 설정이 아니라 실행 환경에 두고 안전한 쪽을 기본으로 한다 — 반대로 두면
    잊은 사람이 조용히 운영 프로젝트를 더럽힌다(2026-08-27에 실제로 그랬다).
    """
    from apps.api.main import sentry_enabled

    assert sentry_enabled({}) is False
    assert sentry_enabled({"SENTRY_ENABLED": "0"}) is False
    assert sentry_enabled({"SENTRY_ENABLED": "true"}) is False
    assert sentry_enabled({"SENTRY_ENABLED": "1"}) is True
