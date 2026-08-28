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

# Sentry로 내보낼지. **기본은 안 보낸다** — 켜는 자리는 운영 compose 하나다.
#
# 이것만 환경변수인 이유는 **개발 머신의 config.yaml이 운영 것의 사본**이기 때문이다.
# 워크트리에 config.yaml을 복사해 쓰는 것이 이 저장소의 관례라(`read_only` 별칭을 그대로
# 보려고), DSN과 `sentry_environment: production`이 함께 딸려 온다. 그 상태로 로컬에서
# 진입점을 돌리면 **개발 트래픽이 운영 프로젝트에 production으로 찍힌다** — 에러뿐 아니라
# 트레이스와 INFO 로그까지, `send_default_pii=True`로.
#
# 그래서 판단을 설정 파일이 아니라 **실행 환경**에 둔다. 설정은 "어디로 보낼 수 있나"이고
# 이 변수는 "지금 보낼 것인가"다. 안전한 쪽이 기본이어야 해서 opt-in이다 —
# 반대로 두면 잊은 사람이 조용히 운영 데이터를 더럽힌다.
#
# 운영에서 이 값이 빠지면 관측이 조용히 꺼지므로, 시작 로그가 어느 쪽인지 밝히고
# `tests/config/test_api_stack.py`가 운영 compose에 이 값이 있는지 검사한다.
SENTRY_ENABLED_ENV = "SENTRY_ENABLED"


def sentry_enabled(environ: dict[str, str] | None = None) -> bool:
    """`SENTRY_ENABLED=1`일 때만 참. 그 밖의 값과 미설정은 전부 거짓이다."""
    values = os.environ if environ is None else environ
    return values.get(SENTRY_ENABLED_ENV, "") == "1"


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
    #
    # **`SENTRY_ENABLED=1`이 아니면 아예 init하지 않는다.** DSN이 config.yaml에 있어도
    # 그렇다 — 그 파일은 개발 머신에도 사본으로 있고, 켜는 판단은 실행 환경이 한다.
    if sentry_enabled():
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
        logger.info("sentry is on (environment=%s)", settings.sentry_environment)
    else:
        # **조용히 끄지 않는다.** 운영에서 이 줄이 보이면 compose에 값이 빠진 것이다.
        logger.warning("sentry is off — set %s=1 to report", SENTRY_ENABLED_ENV)

    # **composition root는 여기 하나다.** 컨테이너가 설정을 스스로 읽지 않고 여기서
    # 받는다 — 그래야 `apps.api.container`가 config.yaml 없이 import된다.
    container = ApiContainer(settings=settings, db_alias=DB_ALIAS)
    app = create_app(container)
    logger.info("serving the thesis read API from alias %s", DB_ALIAS)
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
