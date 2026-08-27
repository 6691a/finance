"""헬스체크 응답."""

from typing import Literal

from pydantic import Field

from apps.api.schemas.common import ApiModel


class Health(ApiModel):
    """`/healthz`. **DB를 치지 않는다** — API 재시작으로 DB 장애가 안 고쳐지는데
    healthcheck가 DB를 보면 깜빡임이 컨테이너 재시작 루프가 된다."""

    status: Literal["ok"] = Field(
        default="ok",
        description="프로세스가 응답할 수 있으면 언제나 `ok`다. **DB 상태를 뜻하지 않는다.**",
    )
