"""진입점. `python -m apps.api.main`.

**`settings`는 함수 안에서 import한다.** `apps/core/config.py`가 모듈 본문에서
`settings = Settings()`를 불러 import 순간 `config.yaml`을 읽는다. 테스트와 도구는 설정
파일 없이 이 모듈을 import할 수 있어야 한다 — `apps/realtime/main.py`가 세운 규칙이다.

**`uvicorn apps.api.app:app`을 쓰지 않는다.** 그러려면 모듈 수준 `app = create_app()`이
있어야 하고, 그 순간 import만으로 `config.yaml`이 필요해져 위 규칙이 그 자리에서 깨진다.
"""

import logging
import os
import sys

logger = logging.getLogger(__name__)

# 컨테이너 안 바인드 주소·포트. **`config.yaml`이 아니다** — 그 파일은 컨테이너 여럿이
# 공유하는 접속 정보라 "이 컨테이너가 어디에 바인드하나"는 거기 속하지 않는다.
# `apps/realtime`의 `REALTIME_DB_ALIAS`가 같은 선례다. 밖으로 보이는 포트는 compose가 정한다.
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000

# 읽을 `config.yaml`의 DB 별칭. **`read_only: true`여야 한다** — 아니면 시작을 거부한다.
#
# 환경변수가 아니라 상수다. `read_only` 별칭이 하나뿐이라 개발·운영 어디서나 값이 같다 —
# 손잡이가 아니라 상수인 것을 환경변수로 두면 `.env` 파일 둘과 그 정합성 테스트가 딸려
# 온다. 로컬 DB를 가리키는 read_only 별칭이 생기면 그때 여기를 고치거나 환경변수를
# 다시 넣는다. 되돌리기가 싸다.
DB_ALIAS = "prod"


def resolve_alias(databases: dict, alias: str) -> None:
    """이 서비스가 붙어도 되는 별칭인지 본다. **아니면 시작을 거부한다.**

    `apps/realtime`가 대상 별칭에 `read_only: false`를 요구하는 가드의 정확한 반대다.
    쓰기 라우트를 안 만드는 것으로 그치지 않고 연결 층에서 막는다 —
    `_connect_args_for`가 그 연결에 `default_transaction_read_only = on`을 걸어 실수로
    쓰기가 들어가도 PostgreSQL이 거절한다.
    """
    config = databases.get(alias)
    if config is None:
        raise ValueError(f"database alias {alias!r} is not in config.yaml; known: {sorted(databases)}")
    if not config.runtime_enabled:
        raise ValueError(f"database alias {alias!r} is not runtime-enabled")
    if not config.read_only:
        raise ValueError(f"database alias {alias!r} must be read_only; this service never writes")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    # config.yaml이 필요한 import는 실행 시점으로 미룬다.
    import sentry_sdk
    import uvicorn

    from apps.api.app import create_app
    from apps.api.container import ApiContainer
    from apps.core.config import settings

    resolve_alias(settings.databases, DB_ALIAS)

    # 새 상주 서비스도 같은 `settings.sentry_*`로 붙인다(프로젝트 규칙). realtime과 달리
    # 여기는 HTTP 트랜잭션이 실제로 생겨 `traces_sample_rate`가 처음으로 뜻을 갖는다.
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,
        release=settings.sentry_release,
        sample_rate=settings.sentry_error_sample_rate,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=True,
        enable_logs=True,
        profile_session_sample_rate=1.0,
        profile_lifecycle="trace",
    )

    # **composition root는 여기 하나다.** 컨테이너가 설정을 스스로 읽지 않고 여기서
    # 받는다 — 그래야 `apps.api.container`가 config.yaml 없이 import된다.
    container = ApiContainer(settings=settings, db_alias=DB_ALIAS)
    app = create_app(container)
    logger.info("serving the read API from alias %s", DB_ALIAS)
    uvicorn.run(
        app,
        host=os.environ.get("API_HOST", DEFAULT_HOST),
        port=int(os.environ.get("API_PORT", DEFAULT_PORT)),
        # uvicorn 기본 dictConfig가 root 핸들러를 갈아치워 realtime과 로그 형식이 갈린다.
        log_config=None,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
