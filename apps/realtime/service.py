"""한국투자증권 WebSocket 실시간 1분봉 수집기.

삼성전자·SK하이닉스의 체결 틱을 KRX(`H0STCNT0`)·NXT(`H0NXCNT0`)로 구독해 1분봉으로
집계하고 `stock_bar`에 **잠정**(`is_final=false`) 저장한다. 확정은 장 마감 뒤
`kis_stock_minute_bars_daily`(REST)가 한다. 잠정봉은 확정행을 절대 덮지 않는다
(`repository.provisional_upsert`의 가드).

Airflow가 실행하지 않는 상주 서비스라 백엔드 트리에 있다. 설정은 FastAPI와 같은
`config.yaml`(`apps.core.config`)이고 저장은 `apps.models` ORM이다. 배포는
`compose/prod/`의 별도 컨테이너다. Airflow 수집기(`modules.collectors.kis`)와
겹치는 종목·거래소 상수는 의도적으로 중복이고 테스트가 둘을 대조한다.

집계 규칙(문서 7.2~7.3):
- open=이벤트 시각이 가장 이른 체결, close=가장 늦은 체결(동시각은 수신 순서), volume=합.
- 분 경계 + `WS_FINALIZATION_DELAY_SECONDS` 뒤 타이머가 직전 분을 flush한다.
- 체결 없는 분은 행을 만들지 않는다. flush 뒤에 온 늦은 틱은 버리고 센다.
- 연결·재연결 시각이 담긴 분은 불완전하므로 저장하지 않는다(전역 체결 ID가 없어
  재연결 전후를 합칠 수 없다). 그 구간은 REST 확정이 메운다.

세션 필터는 REST 일별 수집과 같은 창(KRX 09:00~15:30, NXT 08:00~20:00)을 쓴다.
문서 4.2의 NXT 3분할 창(애프터 15:40 시작) 대신 실측(kis.py: 애프터 15:30~20:00,
세션 사이 공백은 봉이 자연히 빔)을 따른다. REST가 저장하는 범위와 정확히 일치해야
WS에만 구멍이 생기지 않는다.

파일 구성: 프레임 계약은 `frames`, 분봉 집계는 `aggregator`, 상태 파일은
`heartbeat`, 조립·진입점은 `app`이다. 이 모듈은 연결과 서비스 루프만 갖는다.
"""

import asyncio
import json
import logging
import random
import signal
import uuid
from collections.abc import Callable, Coroutine, Mapping
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any, Self
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, SecretStr, field_validator
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from apps.models.raw import SourceStatus
from apps.realtime.aggregator import MinuteAggregator, in_session, minute_of
from apps.realtime.frames import (
    KRX_TR_ID,
    KST,
    NXT_TR_ID,
    EncryptedFrameError,
    FrameContractError,
    PingPong,
    parse_control_frame,
    parse_data_frame,
)
from apps.realtime.heartbeat import Heartbeat
from apps.realtime.repository import PROVIDER, RealtimeRepository

logger = logging.getLogger(__name__)

APPROVAL_PATH = "/oauth2/Approval"
REQUEST_TIMEOUT_SECONDS = 30

# 연결당 구독 상한. 공식 helper의 한계다. 다른 KIS 문서(프로그램매매 등)가 같은 연결에
# 채널을 더 태울 예정이라 시작 시점에 총 수를 검증한다.
MAX_SUBSCRIPTIONS_PER_CONNECTION = 40

# 연결 창(KST). 밖에서는 프로세스가 살아 있되 연결하지 않는다(문서 11.1).
CONNECT_WINDOW_OPEN = time(7, 50)
CONNECT_WINDOW_CLOSE = time(20, 10)

# 어떤 프레임(PINGPONG 포함)도 이 시간 동안 없으면 죽은 소켓으로 보고 재연결한다.
# 평일 휴일에도 PINGPONG은 오므로 완전 침묵은 유휴가 아니라 장애다.
STALE_FRAME_SECONDS = 180.0

# 프레임 계약 위반이 이 횟수를 넘으면 파서가 아니라 스트림이 문제다. 재연결한다.
FRAME_ERROR_RECONNECT_THRESHOLD = 20

SUBSCRIBE_ACK_TIMEOUT_SECONDS = 10.0
BACKOFF_CAP_SECONDS = 60.0
IDLE_POLL_SECONDS = 30.0

DEFAULT_HEARTBEAT_PATH = Path("/tmp/kis-realtime-heartbeat.json")

# 연결 하나가 세는 것. heartbeat 파일과 세션 종료 시 `source_record.metadata` 둘이 이 키
# 집합을 그대로 펼치므로 시작값을 한 곳에 둔다. `HeartbeatExtra`가 같은 이름을 필드로 갖는다.
SESSION_COUNTERS: dict[str, int] = {
    "frames": 0,
    "data_frames": 0,
    "contract_errors": 0,
    "quarantined": 0,
    "stored_bars": 0,
    "skipped_no_previous_close": 0,
    "out_of_session_ticks": 0,
}


class DomesticStock(StrEnum):
    """분봉을 받을 개별 종목. 값이 한국거래소 6자리 코드다.

    `modules.collectors.kis.DomesticStock`과 의도적 중복이다. Airflow 트리를 import하지
    않기 위해서고, 값이 어긋나면 테스트가 잡는다.
    """

    label: str

    def __new__(cls, code: str, label: str) -> Self:
        member = str.__new__(cls, code)
        member._value_ = code
        member.label = label
        return member

    SAMSUNG_ELECTRONICS = ("005930", "삼성전자")
    SK_HYNIX = ("000660", "SK하이닉스")


class ApprovalError(RuntimeError):
    """approval key 발급이 거절됐다. 설정·인증 문제라 재시도해도 같은 답이다."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"KIS approval issue failed: {detail}")


class AuthRejectedError(RuntimeError):
    """인증 거절. approval key를 한 번 재발급하고 재연결한다(문서 7.4)."""


class StaleConnectionError(RuntimeError):
    """프레임이 STALE_FRAME_SECONDS 동안 없다. 죽은 소켓으로 보고 재연결한다."""


class ConnectWindowClosed(Exception):
    """연결 창(평일 07:50~20:10 KST)이 닫혔다. 오류가 아니라 계획된 종료 신호다."""


class _StreamEnded(Exception):
    """서버가 연결을 정상 종료했다. 재연결 사유일 뿐 오류가 아니다.

    recv 루프가 조용히 끝나면 TaskGroup이 flush 타이머·감시 태스크를 영원히
    기다리므로 신호를 올려 전체를 함께 접는다.
    """


class RealtimeSettings(BaseModel):
    """서비스 설정. KIS 키·도메인은 `config.yaml`(`apps.core.config`)에서, 서비스 전용
    노브(NXT 플래그, flush 지연)는 환경변수에서 온다. 조립은 `app.main()`이 한다."""

    model_config = ConfigDict(frozen=True)

    app_key: SecretStr
    app_secret: SecretStr
    rest_domain: str = "https://openapi.koreainvestment.com:9443"
    websocket_domain: str = "ws://ops.koreainvestment.com:21000"
    # 기본은 켜짐이다. REST 쪽 `KIS_ENABLE_NXT_REST`와 방향을 맞춘다 — 한쪽만 기본이
    # 꺼져 있으면 손잡이 하나를 켠 사람이 다른 쪽도 켰다고 믿는다.
    enable_nxt: bool = True
    finalization_delay_seconds: float = 3.0
    heartbeat_path: Path = DEFAULT_HEARTBEAT_PATH
    db_alias: str = "default"

    @field_validator("rest_domain", "websocket_domain")
    @classmethod
    def reject_sandbox_domains(cls, value: str) -> str:
        # 모의투자는 TR ID 체계가 달라 실전 코드가 조용히 엉뚱한 채널을 구독한다.
        # 모의 도메인 지문: openapivts 호스트, 모의 REST 29443, 모의 WS 31000 포트.
        if "openapivts" in value or value.rstrip("/").endswith((":29443", ":31000")):
            raise ValueError("sandbox KIS domain is not supported; use the production domain")
        return value

    @field_validator("finalization_delay_seconds")
    @classmethod
    def bound_delay(cls, value: float) -> float:
        if not 0.5 <= value <= 30.0:
            raise ValueError(f"WS_FINALIZATION_DELAY_SECONDS must be within 0.5~30, got {value}")
        return value

    def websocket_url(self) -> str:
        # 설정값에 경로가 없을 때만 /tryitout을 붙인다. 이미 있으면 두 번 붙이지 않는다
        # (문서 3.4).
        parsed = urlparse(self.websocket_domain)
        if parsed.path and parsed.path != "/":
            return self.websocket_domain
        return self.websocket_domain.rstrip("/") + "/tryitout"


def issue_approval_key(rest_domain: str, app_key: SecretStr, app_secret: SecretStr) -> SecretStr:
    """WebSocket 접속키를 받는다. REST access token과 서로 대체되지 않는다(문서 3.4).

    앱키가 본문에 들어가므로 예외 메시지에 본문을 싣지 않는다. 부르는 쪽이 프로세스
    메모리에 캐시하고, 인증 거절이나 24시간 만료에만 다시 부른다.
    """
    body = json.dumps(
        {
            "grant_type": "client_credentials",
            "appkey": app_key.get_secret_value(),
            "secretkey": app_secret.get_secret_value(),
        }
    ).encode()
    request = Request(
        rest_domain.rstrip("/") + APPROVAL_PATH,
        data=body,
        headers={"content-type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        # `from None`은 의도적이다. 요청 본문에 앱키가 있고 예외 체인이 그걸 실어 나를 수 있다.
        raise ApprovalError(f"HTTP {error.code}") from None
    except URLError as error:
        raise ConnectionError(f"KIS approval request failed: {error.reason}") from None

    key = payload.get("approval_key")
    if not key:
        raise ApprovalError(payload.get("error_description", "no approval_key in the response"))
    return SecretStr(key)


class HeartbeatExtra(BaseModel):
    """heartbeat 상태 파일에 실리는 연결 하나의 카운터.

    **카운터 dict를 그대로 펼치던 자리다.** 키 집합이 코드 어디에도 안 남아서, 테스트가
    두 칸짜리 dict를 넣어도 아무 일도 일어나지 않았다(실제 클로저가 한 번도 안 돌았다).

    카운터 자체는 `run_connection` 안에서 `+=`로 갱신되므로 dict로 둔다. 모양이 남아야
    하는 것은 **밖으로 나가는 값**이다.
    """

    model_config = ConfigDict(frozen=True)

    session_id: str
    frames: int
    data_frames: int
    contract_errors: int
    quarantined: int
    stored_bars: int
    skipped_no_previous_close: int
    out_of_session_ticks: int
    late_ticks: int


class Subscription(BaseModel):
    """구독 채널 하나. 같은 연결에 다른 문서의 채널이 더 실릴 수 있다."""

    model_config = ConfigDict(frozen=True)

    tr_id: str
    tr_key: str


def build_registry(settings: RealtimeSettings) -> tuple[Subscription, ...]:
    """구독 레지스트리. KRX 두 종목은 항상, NXT 두 종목은 플래그가 켜졌을 때만."""
    subscriptions = [Subscription(tr_id=KRX_TR_ID, tr_key=stock.value) for stock in DomesticStock]
    if settings.enable_nxt:
        subscriptions.extend(Subscription(tr_id=NXT_TR_ID, tr_key=stock.value) for stock in DomesticStock)
    if len(subscriptions) > MAX_SUBSCRIPTIONS_PER_CONNECTION:
        raise ValueError(f"{len(subscriptions)} subscriptions exceed the per-connection cap")
    return tuple(subscriptions)


def in_connect_window(now: datetime) -> bool:
    moment = now.astimezone(KST)
    # 주말 가드일 뿐 휴일 캘린더는 없다. 휴일의 무체결은 정상 유휴로 남는다(문서 4.2).
    if moment.weekday() >= 5:
        return False
    return CONNECT_WINDOW_OPEN <= moment.time() < CONNECT_WINDOW_CLOSE


def _first_cause(error: BaseException) -> BaseException:
    """중첩된 ExceptionGroup에서 대표 사유 하나를 꺼낸다."""
    while isinstance(error, BaseExceptionGroup):
        error = error.exceptions[0]
    return error


def _subscribe_message(approval_key: SecretStr, subscription: Subscription) -> str:
    return json.dumps(
        {
            "header": {
                "approval_key": approval_key.get_secret_value(),
                "custtype": "P",
                "tr_type": "1",
                "content-type": "utf-8",
            },
            "body": {"input": {"tr_id": subscription.tr_id, "tr_key": subscription.tr_key}},
        }
    )


async def _flush_timer(
    aggregator: MinuteAggregator,
    repository: RealtimeRepository,
    source_record_id: int,
    previous_closes: Mapping[str, Decimal],
    delay_seconds: float,
    counters: dict[str, int],
    heartbeat_extra: Callable[[], HeartbeatExtra],
    heartbeat: Heartbeat,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleeper: Callable[[float], Coroutine[Any, Any, None]] = asyncio.sleep,
) -> None:
    """분 경계 + 지연 뒤 직전 분을 flush한다. 다음 분의 첫 체결을 기다리지 않는다
    (문서 7.3). `clock`/`sleeper` 주입은 테스트용이다."""
    while True:
        now = clock()
        boundary = minute_of(now) + timedelta(minutes=1)
        await sleeper((boundary - now).total_seconds() + delay_seconds)
        bars = aggregator.flush_before(boundary)
        rows = []
        for bar in bars:
            previous_close = previous_closes.get(bar.stock_code)
            if previous_close is None:
                # 전일종가가 없으면 NOT NULL을 채울 수 없다. 그 종목만 건너뛴다.
                counters["skipped_no_previous_close"] += 1
                continue
            rows.append(
                {
                    "provider": PROVIDER,
                    "stock_code": bar.stock_code,
                    "exchange": bar.exchange,
                    "bar_at": bar.bar_at,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                    "previous_close": previous_close,
                    "ingest_method": "websocket",
                    "is_final": False,
                    "source_record_id": source_record_id,
                }
            )
        if rows:
            counters["stored_bars"] += await repository.store_bars(rows)
        heartbeat.update(heartbeat.state, **heartbeat_extra().model_dump(mode="json"))


async def _watchdog(
    last_frame_at: Callable[[], datetime | None],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleeper: Callable[[float], Coroutine[Any, Any, None]] = asyncio.sleep,
) -> None:
    """죽은 소켓과 연결 창 종료를 감시한다."""
    while True:
        await sleeper(IDLE_POLL_SECONDS)
        now = clock()
        if not in_connect_window(now):
            raise ConnectWindowClosed
        seen = last_frame_at()
        if seen is not None and (now - seen).total_seconds() > STALE_FRAME_SECONDS:
            raise StaleConnectionError(f"no frame for {STALE_FRAME_SECONDS:.0f}s")


async def run_connection(
    settings: RealtimeSettings,
    registry: tuple[Subscription, ...],
    repository: RealtimeRepository,
    approval_key: SecretStr,
    heartbeat: Heartbeat,
) -> None:
    """물리 연결 하나. 세션 레코드 하나가 이 연결의 계보다(문서 10.1).

    구독 전부가 인증 거절이면 `AuthRejectedError`를 올려 바깥 루프가 approval key를
    재발급하게 한다. 개별 NACK는 그 시계열만 비활성한다.
    """
    session_id = uuid.uuid4().hex
    started_at = datetime.now(UTC)
    counters = dict(SESSION_COUNTERS)
    heartbeat.update("connecting", session_id=session_id)

    source_record_id = await repository.open_session(started_at, {"session_id": session_id, "interval": "1m"})

    business_date = started_at.astimezone(KST).date()
    previous_closes: dict[str, Decimal] = {}
    for stock in DomesticStock:
        close = await repository.previous_close(stock.value, business_date)
        if close is None:
            logger.warning("No previous close for %s; its provisional bars are disabled", stock.value)
        else:
            previous_closes[stock.value] = close

    aggregator = MinuteAggregator()
    subscribed_codes = frozenset(subscription.tr_key for subscription in registry)
    active: set[tuple[str, str]] = set()
    ack_results: list[dict[str, str | bool]] = []
    last_frame_at: datetime | None = None
    status = SourceStatus.FAILED
    reason = "unknown"

    try:
        async with connect(settings.websocket_url(), ping_interval=None) as socket:
            for subscription in registry:
                await socket.send(_subscribe_message(approval_key, subscription))

            pending = {(subscription.tr_id, subscription.tr_key) for subscription in registry}
            async with asyncio.timeout(SUBSCRIBE_ACK_TIMEOUT_SECONDS):
                while pending:
                    raw = await socket.recv()
                    text = raw if isinstance(raw, str) else raw.decode()
                    last_frame_at = datetime.now(UTC)
                    counters["frames"] += 1
                    if not text.startswith("{"):
                        # 구독 확인 전의 데이터 프레임. 부분 분이라 버려도 잃는 게 없다.
                        continue
                    control = parse_control_frame(text)
                    if isinstance(control, PingPong):
                        await socket.send(control.raw)
                        continue
                    channel = (control.tr_id, control.tr_key)
                    pending.discard(channel)
                    ack_results.append(
                        {"tr_id": control.tr_id, "tr_key": control.tr_key, "ok": control.ok, "code": control.code}
                    )
                    if control.ok:
                        active.add(channel)
                    else:
                        logger.warning(
                            "Subscription rejected for %s %s: %s %s",
                            control.tr_id,
                            control.tr_key,
                            control.code,
                            control.message,
                        )

            if not active:
                # 전부 거절이면 채널이 아니라 인증이 문제다. 코드 체계는 픽스처로 확정될
                # 때까지 이 판정(전건 거절 = 인증)으로 간다.
                raise AuthRejectedError("every subscription was rejected")

            aggregator.mark_connected(datetime.now(UTC))
            heartbeat.update("ready" if len(active) == len(registry) else "degraded", session_id=session_id)

            def heartbeat_extra() -> HeartbeatExtra:
                return HeartbeatExtra(session_id=session_id, **counters, late_ticks=aggregator.late_tick_count)

            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(
                    _flush_timer(
                        aggregator,
                        repository,
                        source_record_id,
                        previous_closes,
                        settings.finalization_delay_seconds,
                        counters,
                        heartbeat_extra,
                        heartbeat,
                    )
                )
                tasks.create_task(_watchdog(lambda: last_frame_at))

                async for raw in socket:
                    last_frame_at = datetime.now(UTC)
                    counters["frames"] += 1
                    text = raw if isinstance(raw, str) else raw.decode()
                    if text.startswith("{"):
                        control = parse_control_frame(text)
                        if isinstance(control, PingPong):
                            # 받은 프레임을 그대로 되돌린다. 공식 helper와 같은 방식이다.
                            await socket.send(control.raw)
                        continue
                    try:
                        ticks = parse_data_frame(text, subscribed_codes)
                    except EncryptedFrameError:
                        counters["quarantined"] += 1
                        raise
                    except FrameContractError:
                        counters["contract_errors"] += 1
                        if counters["contract_errors"] >= FRAME_ERROR_RECONNECT_THRESHOLD:
                            raise
                        logger.warning("Quarantined a contract-violating frame")
                        continue
                    counters["data_frames"] += 1
                    for tick in ticks:
                        if in_session(tick.exchange, tick.occurred_at):
                            aggregator.add(tick)
                        else:
                            counters["out_of_session_ticks"] += 1
                raise _StreamEnded

        status, reason = SourceStatus.SUCCEEDED, "connection closed"
    except asyncio.CancelledError:
        status, reason = SourceStatus.SUCCEEDED, "shutdown"
        raise
    except BaseExceptionGroup as group:
        # TaskGroup을 지나며 ExceptionGroup이 된 사유를 원형으로 되돌려 바깥 루프가
        # 종류로 판단하게 한다. 첫 예외가 대표 사유다.
        cause = _first_cause(group)
        if isinstance(cause, ConnectWindowClosed | _StreamEnded):
            closed = isinstance(cause, ConnectWindowClosed)
            status, reason = SourceStatus.SUCCEEDED, "connect window closed" if closed else "connection closed"
        else:
            status, reason = SourceStatus.FAILED, str(cause) or type(cause).__name__
        raise cause from group
    except ConnectWindowClosed:
        status, reason = SourceStatus.SUCCEEDED, "connect window closed"
        raise
    except _StreamEnded:
        status, reason = SourceStatus.SUCCEEDED, "connection closed"
        raise
    except Exception as error:
        status, reason = SourceStatus.FAILED, str(error) or type(error).__name__
        raise
    finally:
        # SIGTERM·끊김 어느 쪽이든 열린 분은 저장하지 않는다(문서 7.3).
        aggregator.drop_open_minutes()
        metadata = {
            "session_id": session_id,
            "reason": reason,
            "acks": ack_results,
            "active_channels": sorted(f"{tr_id}:{tr_key}" for tr_id, tr_key in active),
            **counters,
            "late_ticks": aggregator.late_tick_count,
            "skipped_partial_bars": aggregator.skipped_partial_count,
            "dropped_open_bars": aggregator.dropped_open_count,
        }
        # 취소(SIGTERM) 경로에서도 커밋 하나는 끝까지 간다. 추가 취소는 docker의
        # stop_grace_period(30s) 안에서는 오지 않는다.
        await repository.close_session(source_record_id, datetime.now(UTC), status, counters["stored_bars"], metadata)


async def run_service(settings: RealtimeSettings, repository: RealtimeRepository) -> None:
    """바깥 루프. 연결 창 안에서 연결을 유지하고 밖에서는 유휴로 기다린다."""
    heartbeat = Heartbeat(settings.heartbeat_path)
    registry = build_registry(settings)
    logger.info(
        "kis-realtime 시작: 구독 %d건(%s), NXT=%s, 연결 창 평일 07:50~20:10 KST",
        len(registry),
        ", ".join(f"{sub.tr_id}:{sub.tr_key}" for sub in registry),
        settings.enable_nxt,
    )
    approval_key: SecretStr | None = None
    failure_streak = 0

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, shutdown.set)

    try:
        while not shutdown.is_set():
            if not in_connect_window(datetime.now(UTC)):
                heartbeat.update("idle")
                await _wait_or_shutdown(shutdown, IDLE_POLL_SECONDS)
                continue

            if approval_key is None:
                try:
                    approval_key = await asyncio.to_thread(
                        issue_approval_key, settings.rest_domain, settings.app_key, settings.app_secret
                    )
                except ConnectionError as error:
                    # 네트워크 문제는 재시도할 값어치가 있다. 발급 거절(ApprovalError)은
                    # 설정 문제라 그대로 터진다.
                    logger.warning("Approval key request failed: %s", error)
                    failure_streak += 1
                    await _wait_or_shutdown(shutdown, min(BACKOFF_CAP_SECONDS, 2.0**failure_streak))
                    continue

            connection = asyncio.create_task(run_connection(settings, registry, repository, approval_key, heartbeat))
            waiter = asyncio.create_task(shutdown.wait())
            done, _ = await asyncio.wait({connection, waiter}, return_when=asyncio.FIRST_COMPLETED)
            if waiter in done:
                connection.cancel()
                try:
                    await connection
                except asyncio.CancelledError:
                    pass
                break
            waiter.cancel()

            try:
                connection.result()
                failure_streak = 0
            except ConnectWindowClosed:
                failure_streak = 0
                continue
            except AuthRejectedError:
                # approval key를 한 번 재발급하고 재연결한다(문서 7.4).
                logger.warning("Authentication rejected; reissuing the approval key")
                approval_key = None
                failure_streak += 1
            except (
                TimeoutError,
                OSError,
                WebSocketException,
                StaleConnectionError,
                FrameContractError,
                EncryptedFrameError,
                _StreamEnded,
            ) as error:
                logger.warning("Connection ended: %s", error)
                failure_streak += 1
            # 그 밖의 예외(DB 오류 등)는 위로 터진다. 프로세스가 죽고 compose의
            # restart가 되살린다 — 조용히 도는 것보다 낫다.

            backoff = min(BACKOFF_CAP_SECONDS, 2.0**failure_streak) * random.uniform(0.5, 1.0)
            await _wait_or_shutdown(shutdown, backoff)
    finally:
        heartbeat.update("idle" if shutdown.is_set() else "failed")


async def _wait_or_shutdown(shutdown: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(shutdown.wait(), timeout=seconds)
    except TimeoutError:
        pass
