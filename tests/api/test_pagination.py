"""쪽을 빠뜨린 라우트가 들어오는 것을 막는다.

**행을 주는 라우트는 전부 `limit`·`offset`을 받는다.** 하나라도 빠지면 그 라우트만
조용히 전부를 주고, 부르는 쪽은 어느 것이 쪽이고 어느 것이 전부인지 외워야 한다.
면제는 여기 적고 이유를 남긴다 — 새 라우트가 말없이 면제되는 자리를 없애는 것이 이
테스트의 요점이다.
"""

from typing import Any

from apps.api.routes import routers

# 쪽이 없는 라우트와 그 이유. **행 목록이 아닌 것만 들어온다.**
EXEMPT: dict[str, str] = {
    "/healthz": "행이 아니다",
    "/api/quotes/bars": "한 심볼의 열 묶음이다. 상한은 MAX_POINTS이고 넘으면 400이다",
    "/api/quotes/daily": "같은 이유",
    "/api/indicators/observations": "한 계열의 열 묶음이다. 상한은 MAX_POINTS다",
    "/api/indicators/curve": "나라별로 묶은 곡선이라 행 목록이 아니다",
    "/api/theses/quality": "집계 배열 둘이다",
    "/api/documents/{document_id}": "단건",
    "/api/llm-runs/{llm_run_id}": "단건",
    "/api/llm-runs/{llm_run_id}/tool-calls/{seq}": "단건",
    "/api/theses/{thesis_id}": "단건",
    "/api/theses/{thesis_id}/graph": "한 추론의 그래프 하나다",
}


def _routes() -> list[tuple[str, set[str]]]:
    found: list[tuple[str, set[str]]] = []
    for router in routers:
        for route in router.routes:
            dependant: Any = getattr(route, "dependant", None)
            if dependant is None:
                continue
            found.append((route.path, {param.name for param in dependant.query_params}))
    return found


def test_every_row_route_takes_limit_and_offset():
    missing = [
        path
        for path, params in _routes()
        if path not in EXEMPT and not {"limit", "offset"} <= params
    ]
    assert missing == []


def test_the_exemption_list_has_no_stale_entries():
    """면제 목록에 없는 경로가 남아 있으면 그 이유도 이미 사라진 것이다."""
    paths = {path for path, _ in _routes()}
    assert set(EXEMPT) <= paths
