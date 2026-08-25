"""조립과 진입점. 실행은 `python -m apps.realtime.main`이다.

설정 로드, 로깅·Sentry 초기화, DB 연결 조립만 한다. 수집 로직은 `service`에 있다.
"""

import argparse
import asyncio
import logging
import os
from collections.abc import Sequence
from pathlib import Path

from pydantic import SecretStr

from apps.realtime.heartbeat import healthcheck
from apps.realtime.repository import RealtimeRepository
from apps.realtime.service import DEFAULT_HEARTBEAT_PATH, RealtimeSettings, run_service

logger = logging.getLogger(__name__)


NXT_WEBSOCKET_FLAG = "KIS_ENABLE_NXT_WEBSOCKET"
FLAG_ON_VALUES = frozenset({"1", "true", "yes", "on"})
FLAG_OFF_VALUES = frozenset({"0", "false", "no", "off"})


def nxt_websocket_enabled() -> bool:
    """NXT WebSocket 구독 여부. 비우면 켜짐.

    **REST 쪽 `KIS_ENABLE_NXT_REST`와 기본값·허용 값이 같다.** 두 손잡이가 서로 다르게
    동작하면 한쪽을 끈 사람이 다른 쪽도 껐다고 믿는다. airflow 트리를 import하지 않으므로
    (저장소 규칙) 판정이 두 벌이고, `tests/realtime/`이 그 둘을 대조한다.

    모르는 값은 실패시킨다. `fasle`가 조용히 켜짐으로 읽히면 손잡이를 당겼다고 믿는 사람과
    실제 동작이 갈린다. 상주 서비스라 시작할 때 멈추는 것이 맞다.
    """
    raw = (os.environ.get(NXT_WEBSOCKET_FLAG) or "").strip().lower()
    if not raw or raw in FLAG_ON_VALUES:
        return True
    if raw in FLAG_OFF_VALUES:
        return False
    raise ValueError(f"{NXT_WEBSOCKET_FLAG} must be one of {sorted(FLAG_ON_VALUES | FLAG_OFF_VALUES)}, got {raw!r}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KIS 실시간 1분봉 수집기")
    parser.add_argument("--healthcheck", action="store_true", help="heartbeat 파일로 상태만 확인하고 나간다")
    arguments = parser.parse_args(argv)

    heartbeat_path = Path(os.environ.get("KIS_REALTIME_HEARTBEAT_FILE", str(DEFAULT_HEARTBEAT_PATH)))
    if arguments.healthcheck:
        # config.yaml 없이도 돌아야 한다. healthcheck는 30초마다 도는 가장 싼 경로다.
        return healthcheck(heartbeat_path)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    # config.yaml이 필요한 import는 실행 시점으로 미룬다. 테스트와 healthcheck는
    # 설정 파일 없이 이 모듈을 import한다.
    from apps.core.config import settings

    realtime = RealtimeSettings(
        app_key=SecretStr(settings.kis_app_key),
        app_secret=SecretStr(settings.kis_app_secret),
        rest_domain=settings.kis_rest_domain,
        websocket_domain=settings.kis_websocket_domain,
        enable_nxt=nxt_websocket_enabled(),
        finalization_delay_seconds=float(os.environ.get("WS_FINALIZATION_DELAY_SECONDS", "3")),
        heartbeat_path=heartbeat_path,
        db_alias=os.environ.get("REALTIME_DB_ALIAS", "default"),
    )

    alias_config = settings.databases.get(realtime.db_alias)
    if alias_config is None or not alias_config.runtime_enabled:
        raise ValueError(f"database alias {realtime.db_alias!r} is missing or disabled in config.yaml")
    if alias_config.read_only:
        # 읽기 전용 연결로 시작하면 첫 flush에서야 터진다. 지금 멈추는 편이 낫다.
        raise ValueError(f"database alias {realtime.db_alias!r} is read_only; provisional bars cannot be stored")

    # Sentry도 FastAPI와 같은 config.yaml 값을 쓴다. DSN이 비면 SDK가 비활성으로 초기화된다.
    # 기본 LoggingIntegration이 ERROR 이상을 이벤트로 보내고 warning은 breadcrumb으로 남는다.
    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.sentry_dsn or None,
        environment=settings.sentry_environment or None,
        release=settings.sentry_release or None,
        sample_rate=settings.sentry_error_sample_rate,
        # 요청·사용자 데이터가 없는 상주 수집기라 실릴 PII 자체가 없다.
        send_default_pii=True,
        # 로그를 Sentry Logs로도 보낸다. 위 이벤트/breadcrumb과 별개 채널이다.
        enable_logs=True,
        # 트레이싱 비율은 config.yaml이 정한다(운영 0.1). 이 서비스에는 HTTP 트랜잭션이
        # 없어 DB 스팬 정도만 잡히고, 프로파일러는 트랜잭션이 있을 때만 돈다.
        traces_sample_rate=settings.sentry_traces_sample_rate,
        profile_session_sample_rate=1.0,
        profile_lifecycle="trace",
    )
    logger.info("Sentry %s", "활성" if settings.sentry_dsn else "비활성")

    from apps.core.database import Database

    database = Database(databases=settings.databases)

    async def amain() -> None:
        repository = RealtimeRepository(database.get_session_factory(realtime.db_alias))
        try:
            await run_service(realtime, repository)
        finally:
            await database.dispose()

    asyncio.run(amain())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
