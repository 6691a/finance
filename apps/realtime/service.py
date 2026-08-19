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
"""

import argparse
import asyncio
import json
import logging
import os
import random
import signal
import uuid
from collections.abc import Callable, Coroutine, Mapping, Sequence
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Any, Self
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, SecretStr, field_validator
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from apps.models.market import StockExchange
from apps.models.raw import SourceStatus
from apps.realtime.repository import PROVIDER, RealtimeRepository

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")

APPROVAL_PATH = "/oauth2/Approval"
REQUEST_TIMEOUT_SECONDS = 30

KRX_TR_ID = "H0STCNT0"
NXT_TR_ID = "H0NXCNT0"
RECORD_FIELD_COUNT = 46

# 연결당 구독 상한. 공식 helper의 한계다. 다른 KIS 문서(프로그램매매 등)가 같은 연결에
# 채널을 더 태울 예정이라 시작 시점에 총 수를 검증한다.
MAX_SUBSCRIPTIONS_PER_CONNECTION = 40

# 연결 창(KST). 밖에서는 프로세스가 살아 있되 연결하지 않는다(문서 11.1).
CONNECT_WINDOW_OPEN = time(7, 50)
CONNECT_WINDOW_CLOSE = time(20, 10)

# 세션 창(KST, 분 기준 양끝 포함). REST 일별 수집(`modules.collectors.kis`)과 같은
# 값이어야 WS에만 구멍이 생기지 않는다. 테스트가 둘을 대조한다.
SESSION_WINDOWS: dict[StockExchange, tuple[time, time]] = {
    StockExchange.KRX: (time(9, 0), time(15, 30)),
    StockExchange.NXT: (time(8, 0), time(20, 0)),
}

# 어떤 프레임(PINGPONG 포함)도 이 시간 동안 없으면 죽은 소켓으로 보고 재연결한다.
# 평일 휴일에도 PINGPONG은 오므로 완전 침묵은 유휴가 아니라 장애다.
STALE_FRAME_SECONDS = 180.0

# 프레임 계약 위반이 이 횟수를 넘으면 파서가 아니라 스트림이 문제다. 재연결한다.
FRAME_ERROR_RECONNECT_THRESHOLD = 20

SUBSCRIBE_ACK_TIMEOUT_SECONDS = 10.0
BACKOFF_CAP_SECONDS = 60.0
IDLE_POLL_SECONDS = 30.0

DEFAULT_HEARTBEAT_PATH = Path("/tmp/kis-realtime-heartbeat.json")


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


# H0STCNT0(KRX 주식 체결) 46필드. 순서가 계약이다 — 열이 밀리면 값이 조용히 옆 칸으로
# 간다. 개수와 순서를 응답마다 검증한다. 실 캡처 픽스처로 대조한다.
KRX_FIELDS: tuple[str, ...] = (
    "MKSC_SHRN_ISCD",
    "STCK_CNTG_HOUR",
    "STCK_PRPR",
    "PRDY_VRSS_SIGN",
    "PRDY_VRSS",
    "PRDY_CTRT",
    "WGHN_AVRG_STCK_PRC",
    "STCK_OPRC",
    "STCK_HGPR",
    "STCK_LWPR",
    "ASKP1",
    "BIDP1",
    "CNTG_VOL",
    "ACML_VOL",
    "ACML_TR_PBMN",
    "SELN_CNTG_CSNU",
    "SHNU_CNTG_CSNU",
    "NTBY_CNTG_CSNU",
    "CTTR",
    "SELN_CNTG_SMTN",
    "SHNU_CNTG_SMTN",
    "CCLD_DVSN",
    "SHNU_RATE",
    "PRDY_VOL_VRSS_ACML_VOL_RATE",
    "OPRC_HOUR",
    "OPRC_VRSS_PRPR_SIGN",
    "OPRC_VRSS_PRPR",
    "HGPR_HOUR",
    "HGPR_VRSS_PRPR_SIGN",
    "HGPR_VRSS_PRPR",
    "LWPR_HOUR",
    "LWPR_VRSS_PRPR_SIGN",
    "LWPR_VRSS_PRPR",
    "BSOP_DATE",
    "NEW_MKOP_CLS_CODE",
    "TRHT_YN",
    "ASKP_RSQN1",
    "BIDP_RSQN1",
    "TOTAL_ASKP_RSQN",
    "TOTAL_BIDP_RSQN",
    "VOL_TNRT",
    "PRDY_SMNS_HOUR_ACML_VOL",
    "PRDY_SMNS_HOUR_ACML_VOL_RATE",
    "HOUR_CLS_CODE",
    "MRKT_TRTM_CLS_CODE",
    "VI_STND_PRC",
)

# H0NXCNT0(NXT 주식 체결)은 KRX와 필드 수가 같지만 스키마가 하나 다르다.
# KRX의 CCLD_DVSN 자리에 NXT는 CNTG_CLS_CODE가 온다(문서 3.5).
NXT_FIELDS: tuple[str, ...] = tuple("CNTG_CLS_CODE" if name == "CCLD_DVSN" else name for name in KRX_FIELDS)


class ApprovalError(RuntimeError):
    """approval key 발급이 거절됐다. 설정·인증 문제라 재시도해도 같은 답이다."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"KIS approval issue failed: {detail}")


class FrameContractError(ValueError):
    """프레임이 46필드 파이프·캐럿 계약을 지키지 않았다. 재시도해도 같은 답이다."""


class EncryptedFrameError(RuntimeError):
    """암호화(encrypt=Y) 프레임. 평문 파싱은 금지고 격리 후 재연결한다(문서 3.5)."""


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
    노브(NXT 플래그, flush 지연)는 환경변수에서 온다. 조립은 `main()`이 한다."""

    model_config = ConfigDict(frozen=True)

    app_key: SecretStr
    app_secret: SecretStr
    rest_domain: str = "https://openapi.koreainvestment.com:9443"
    websocket_domain: str = "ws://ops.koreainvestment.com:21000"
    enable_nxt: bool = False
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


class FrameSpec(BaseModel):
    """TR ID 하나의 프레임 계약. 필드 목록을 고정해 열 밀림을 잡는다."""

    model_config = ConfigDict(frozen=True)

    exchange: StockExchange
    fields: tuple[str, ...]

    @property
    def field_count(self) -> int:
        return len(self.fields)

    def index(self, name: str) -> int:
        return self.fields.index(name)


FRAME_SPECS: dict[str, FrameSpec] = {
    KRX_TR_ID: FrameSpec(exchange=StockExchange.KRX, fields=KRX_FIELDS),
    NXT_TR_ID: FrameSpec(exchange=StockExchange.NXT, fields=NXT_FIELDS),
}


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


class Tick(BaseModel):
    """체결 하나에서 집계에 쓰는 다섯 값만 추린 것."""

    model_config = ConfigDict(frozen=True)

    exchange: StockExchange
    stock_code: str
    occurred_at: AwareDatetime
    price: Decimal
    volume: int

    @field_validator("occurred_at")
    @classmethod
    def normalize_to_utc(cls, moment: datetime) -> datetime:
        return moment.astimezone(UTC)

    @field_validator("price")
    @classmethod
    def require_positive_finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= 0:
            raise ValueError(f"price must be positive and finite, got {value}")
        return value

    @field_validator("volume")
    @classmethod
    def require_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError(f"volume must not be negative, got {value}")
        return value


class PingPong(BaseModel):
    model_config = ConfigDict(frozen=True)

    raw: str


class SubscribeResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    tr_id: str
    tr_key: str
    ok: bool
    code: str
    message: str


def parse_data_frame(raw: str, subscribed_codes: frozenset[str]) -> tuple[Tick, ...]:
    """`암호화|TR_ID|건수|필드^...` 데이터 프레임을 틱으로 바꾼다.

    검증에 실패하면 `FrameContractError`다. 열이 밀린 채 조용히 저장되는 것보다
    멈추는 편이 낫다.
    """
    head = raw.split("|", 3)
    if len(head) != 4:
        raise FrameContractError(f"data frame needs 4 pipe sections, got {len(head)}")
    encrypted, tr_id, count_raw, body = head

    if encrypted == "1":
        raise EncryptedFrameError(f"encrypted frame for {tr_id}")
    if encrypted != "0":
        raise FrameContractError(f"unknown encrypt flag {encrypted!r}")

    spec = FRAME_SPECS.get(tr_id)
    if spec is None:
        raise FrameContractError(f"unsupported TR ID {tr_id!r}")

    try:
        count = int(count_raw)
    except ValueError:
        raise FrameContractError(f"record count is not a number: {count_raw!r}") from None
    if count < 1:
        raise FrameContractError(f"record count must be >= 1, got {count}")

    fields = body.split("^")
    if len(fields) != count * spec.field_count:
        raise FrameContractError(
            f"{tr_id} expects {count * spec.field_count} fields for {count} records, got {len(fields)}"
        )

    code_index = spec.index("MKSC_SHRN_ISCD")
    date_index = spec.index("BSOP_DATE")
    hour_index = spec.index("STCK_CNTG_HOUR")
    price_index = spec.index("STCK_PRPR")
    volume_index = spec.index("CNTG_VOL")

    ticks = []
    for start in range(0, len(fields), spec.field_count):
        record = fields[start : start + spec.field_count]
        stock_code = record[code_index]
        if stock_code not in subscribed_codes:
            raise FrameContractError(f"unsubscribed stock code {stock_code!r} in {tr_id}")
        try:
            occurred_at = datetime.strptime(record[date_index] + record[hour_index], "%Y%m%d%H%M%S").replace(
                tzinfo=KST
            )
            price = Decimal(record[price_index])
            volume = int(record[volume_index])
        except (ValueError, InvalidOperation):
            raise FrameContractError(
                f"invalid tick fields for {stock_code}: date={record[date_index]!r} hour={record[hour_index]!r}"
            ) from None
        ticks.append(
            Tick(
                exchange=spec.exchange,
                stock_code=stock_code,
                occurred_at=occurred_at,
                price=price,
                volume=volume,
            )
        )
    return tuple(ticks)


def parse_control_frame(raw: str) -> PingPong | SubscribeResult:
    """JSON 제어 프레임을 PINGPONG과 구독 ACK/NACK으로 가른다."""
    try:
        payload = json.loads(raw)
        header = payload["header"]
        tr_id = header["tr_id"]
    except (ValueError, KeyError, TypeError):
        raise FrameContractError("control frame is not the expected JSON shape") from None

    if tr_id == "PINGPONG":
        return PingPong(raw=raw)

    body = payload.get("body") or {}
    return SubscribeResult(
        tr_id=tr_id,
        tr_key=header.get("tr_key", ""),
        ok=body.get("rt_cd") == "0",
        code=body.get("msg_cd", ""),
        message=body.get("msg1", ""),
    )


class AggregatedBar(BaseModel):
    """닫힌 1분봉 하나. `bar_at`은 분 시작(UTC)이다."""

    model_config = ConfigDict(frozen=True)

    exchange: StockExchange
    stock_code: str
    bar_at: AwareDatetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


class _OpenBar:
    """집계 중인 분 하나. 순수 가변 상태라 Pydantic을 쓰지 않는다."""

    __slots__ = ("close", "close_key", "high", "low", "open", "open_key", "volume")

    def __init__(self, tick: Tick, sequence: int) -> None:
        self.open = tick.price
        self.open_key = (tick.occurred_at, sequence)
        self.close = tick.price
        self.close_key = (tick.occurred_at, sequence)
        self.high = tick.price
        self.low = tick.price
        self.volume = tick.volume

    def absorb(self, tick: Tick, sequence: int) -> None:
        key = (tick.occurred_at, sequence)
        # 이벤트 시각이 기준이고 동시각은 수신 순서다(문서 7.2). sequence가 그 순서다.
        if key < self.open_key:
            self.open = tick.price
            self.open_key = key
        if key >= self.close_key:
            self.close = tick.price
            self.close_key = key
        self.high = max(self.high, tick.price)
        self.low = min(self.low, tick.price)
        self.volume += tick.volume


def _minute_of(moment: datetime) -> datetime:
    return moment.replace(second=0, microsecond=0)


class MinuteAggregator:
    """틱을 (거래소, 종목, 분) 단위 봉으로 모은다. I/O가 없는 순수 상태 기계다."""

    def __init__(self) -> None:
        self._bars: dict[tuple[StockExchange, str, datetime], _OpenBar] = {}
        self._sequence = 0
        self._watermark: datetime | None = None
        self._flushed_until: datetime | None = None
        self.late_tick_count = 0
        self.skipped_partial_count = 0
        self.dropped_open_count = 0

    def mark_connected(self, now: datetime) -> None:
        """연결(재연결) 시각이 담긴 분을 저장 금지 수위선으로 기록한다.

        체결 프레임에 전역 체결 ID가 없어 분 중간에 붙은 연결의 첫 분은 앞부분이
        비었는지 알 수 없다. 그 분은 버리고 REST 확정에 맡긴다(문서 7.2).
        """
        watermark = _minute_of(now.astimezone(UTC))
        if self._watermark is None or watermark > self._watermark:
            self._watermark = watermark

    def add(self, tick: Tick) -> None:
        bar_at = _minute_of(tick.occurred_at)
        if self._flushed_until is not None and bar_at < self._flushed_until:
            # 이미 닫은 분의 늦은 틱. 병합하면 flush된 값과 어긋나므로 버리고 센다.
            self.late_tick_count += 1
            return
        self._sequence += 1
        key = (tick.exchange, tick.stock_code, bar_at)
        open_bar = self._bars.get(key)
        if open_bar is None:
            self._bars[key] = _OpenBar(tick, self._sequence)
        else:
            open_bar.absorb(tick, self._sequence)

    def flush_before(self, boundary: datetime) -> tuple[AggregatedBar, ...]:
        """`boundary`(분 시작, UTC) 이전의 분을 전부 닫아 돌려준다."""
        boundary = _minute_of(boundary.astimezone(UTC))
        closed = []
        for key in sorted(key for key in self._bars if key[2] < boundary):
            exchange, stock_code, bar_at = key
            open_bar = self._bars.pop(key)
            if self._watermark is not None and bar_at <= self._watermark:
                # 연결 시각이 담긴 분은 불완전하다. 저장하지 않는다.
                self.skipped_partial_count += 1
                continue
            closed.append(
                AggregatedBar(
                    exchange=exchange,
                    stock_code=stock_code,
                    bar_at=bar_at,
                    open=open_bar.open,
                    high=open_bar.high,
                    low=open_bar.low,
                    close=open_bar.close,
                    volume=open_bar.volume,
                )
            )
        if self._flushed_until is None or boundary > self._flushed_until:
            self._flushed_until = boundary
        return tuple(closed)

    def drop_open_minutes(self) -> None:
        """끊김·종료 시 열린 분을 폐기한다. 불완전한 봉을 완전한 것처럼 저장하지 않는다."""
        self.dropped_open_count += len(self._bars)
        self._bars.clear()


def in_session(exchange: StockExchange, occurred_at: datetime) -> bool:
    """틱이 담길 분이 REST 일별 수집과 같은 창 안인지 본다.

    KRX 09:00~15:30, NXT 08:00~20:00(분 기준, 양끝 포함). NXT 세션 사이 공백은 체결이
    없어 자연히 봉이 비므로 창을 셋으로 나누지 않는다(kis.py 실측).
    """
    first_bar, last_bar = SESSION_WINDOWS[exchange]
    minute = _minute_of(occurred_at.astimezone(KST)).time()
    return first_bar <= minute <= last_bar


def in_connect_window(now: datetime) -> bool:
    moment = now.astimezone(KST)
    # 주말 가드일 뿐 휴일 캘린더는 없다. 휴일의 무체결은 정상 유휴로 남는다(문서 4.2).
    if moment.weekday() >= 5:
        return False
    return CONNECT_WINDOW_OPEN <= moment.time() < CONNECT_WINDOW_CLOSE


HEARTBEAT_STATES = ("idle", "connecting", "ready", "degraded", "failed")
HEARTBEAT_STALE_SECONDS = 120.0


def write_heartbeat(path: Path, state: str, **extra: Any) -> None:
    """healthcheck가 읽는 상태 파일. 임시 파일에 쓰고 바꿔치기해 찢긴 읽기를 막는다."""
    if state not in HEARTBEAT_STATES:
        raise ValueError(f"unknown heartbeat state {state!r}")
    payload = {"state": state, "written_at": datetime.now(UTC).isoformat(), **extra}
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False))
    temporary.replace(path)


def healthcheck(path: Path, now: datetime | None = None) -> int:
    """docker healthcheck 진입점. 0=건강, 1=이상."""
    now = now or datetime.now(UTC)
    try:
        payload = json.loads(path.read_text())
        state = payload["state"]
        written_at = datetime.fromisoformat(payload["written_at"])
    except (OSError, ValueError, KeyError):
        return 1
    if state not in HEARTBEAT_STATES or state == "failed":
        return 1
    if (now - written_at).total_seconds() > HEARTBEAT_STALE_SECONDS:
        return 1
    return 0


class _Heartbeat:
    """상태 파일 쓰기를 카운터와 함께 감싼 것. 파일 쓰기 실패는 수집을 멈출 이유가
    아니므로 경고만 남긴다 — 상태 파일은 관측용이지 데이터가 아니다."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self.state = "idle"

    def update(self, state: str, **extra: Any) -> None:
        if state != self.state:
            # 전이만 남긴다. idle 유지 30초마다 한 줄씩 쌓이면 로그가 소음이 된다.
            logger.info("State %s -> %s", self.state, state)
        self.state = state
        try:
            write_heartbeat(self._path, state, **extra)
        except OSError:
            logger.warning("Heartbeat write failed at %s", self._path)


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
    heartbeat_extra: Callable[[], dict[str, Any]],
    heartbeat: _Heartbeat,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleeper: Callable[[float], Coroutine[Any, Any, None]] = asyncio.sleep,
) -> None:
    """분 경계 + 지연 뒤 직전 분을 flush한다. 다음 분의 첫 체결을 기다리지 않는다
    (문서 7.3). `clock`/`sleeper` 주입은 테스트용이다."""
    while True:
        now = clock()
        boundary = _minute_of(now) + timedelta(minutes=1)
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
        heartbeat.update(heartbeat.state, **heartbeat_extra())


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
    heartbeat: _Heartbeat,
) -> None:
    """물리 연결 하나. 세션 레코드 하나가 이 연결의 계보다(문서 10.1).

    구독 전부가 인증 거절이면 `AuthRejectedError`를 올려 바깥 루프가 approval key를
    재발급하게 한다. 개별 NACK는 그 시계열만 비활성한다.
    """
    session_id = uuid.uuid4().hex
    started_at = datetime.now(UTC)
    counters: dict[str, int] = {
        "frames": 0,
        "data_frames": 0,
        "contract_errors": 0,
        "quarantined": 0,
        "stored_bars": 0,
        "skipped_no_previous_close": 0,
        "out_of_session_ticks": 0,
    }
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

            def heartbeat_extra() -> dict[str, Any]:
                return {"session_id": session_id, **counters, "late_ticks": aggregator.late_tick_count}

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
    heartbeat = _Heartbeat(settings.heartbeat_path)
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


def _env_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes"}


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
        enable_nxt=_env_bool(os.environ.get("KIS_ENABLE_NXT_WEBSOCKET")),
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
