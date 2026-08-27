"""헬스체크. 리소스가 아니라 인프라라 `/api` 아래 두지 않는다."""

from fastapi import APIRouter

from apps.api.schemas import Health

router = APIRouter()


@router.get("/healthz", response_model=Health, tags=["health"])
async def healthz() -> Health:
    """**DB를 치지 않는다.** API 재시작으로 DB 장애가 안 고쳐지는데 healthcheck가 DB를
    보면 깜빡임이 컨테이너 재시작 루프가 된다. DB가 죽었는지는 요청이 500으로 알린다."""
    return Health()
