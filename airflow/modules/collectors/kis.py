"""한국투자증권 API에서 KOSPI200 정규 선물의 1분봉을 수집한다.

배포 Airflow와 공유되는 폴더는 `airflow/dags`와 `airflow/modules` 둘뿐이다. Airflow는
`apps/`도 `core/`도 보지 못한다. 그래서 DAG가 실행 시점에 필요한 코드는 전부 여기 있어야
한다. `dags/`에는 스케줄과 오케스트레이션만 두고 수집 규칙은 이 모듈에 둔다.

저장 대상은 `yahoo.py`와 같은 `quote_bar` 테이블이고 `provider`로 갈린다. 정의의 원본은
백엔드의 `apps/models/market.py`이며 여기 SQL의 컬럼 이름은 `tests/collectors/test_kis.py`가
그 모델 metadata와 대조한다.

## 왜 Yahoo가 아니라 KIS인가

**Yahoo에 KOSPI200 선물이 없다.** 실측으로 `KS200.KS`·`101RC000.KS` 둘 다 Not Found다.
현물 지수(`^KS200`, `^KQ11`)는 있는데 KRX 파생은 취급하지 않는다. KIS는 주고 **국내 시세는
무료다**(해외선물을 막았던 `EGW00550` CME 시세료에 해당하지 않는다).

이 값이 중요한 이유는 이 수집의 가설이 "미국 신호 → 한국 종목"이기 때문이다. KOSPI200
선물은 그 한국 쪽 반응을 실시간으로 보는 가장 직접적인 값이다.

## Yahoo 수집기와 다른 점

- **응답이 최신순이다.** Yahoo는 오름차순이다. 저장 전에 뒤집지 않아도 되지만 구간을 자를
  때 방향을 헷갈리면 조용히 틀린다.
- **한 번에 102봉만 온다.** Yahoo는 하루치를 통째로 줬다. 5분 폴링에는 충분하지만(102분은
  1시간 40분치) 백필하려면 `FID_INPUT_HOUR_1`로 페이징해야 한다.
- **시각이 KST 벽시계다.** `stck_bsop_date`(YYYYMMDD) + `stck_cntg_hour`(HHMMSS)를 KST로
  해석해 UTC로 정규화한다. Yahoo는 epoch였다.
- **월물 코드가 필요하다.** 연속 심볼이 없어서 `A01609` 같은 계약을 직접 지정한다.
  분기물이고 만기가 지나면 다음 계약으로 넘어간다. 저장할 때 `contract_code`에 남겨
  나중에 가격 갭이 롤오버 때문인지 알 수 있게 한다.
- **전일종가가 정확하다.** `output1.futs_prdy_clpr`는 조회 구간과 무관하게 진짜 전일종가라,
  Yahoo 백필에서 겪은 `previous_close` 결함이 여기서는 생기지 않는다.
- **오류가 HTTP 상태로 오지 않는다.** 권한 없음도 200에 `rt_cd`로 온다. 실패를 500으로
  내면서 본문에 키 따옴표가 없는 비표준 JSON을 담기도 한다. 그래서 `KisResultError`가
  본문의 코드를 담아 올라가고 재시도 여부는 DAG가 정한다.

## 수집하지 않는 것

**야간장.** KRX 야간 파생 시세는 이 분봉 API로 오지 않는다. 야간 시각을 넣어도 정규장
마감(15:45 KST)으로 잘리고, `krx-ngt-*` REST 엔드포인트는 404다(실측). 웹소켓만 되는
것으로 보이며 상주 프로세스가 필요해 이번 범위 밖이다. 정규장 09:00~15:45만 수집한다.
"""

import json
import logging
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from os import environ
from typing import Protocol, Self
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    SecretStr,
    field_validator,
    model_validator,
)

from modules.sql import read_sql

logger = logging.getLogger(__name__)

KIS_BASE_URL = "https://openapi.koreainvestment.com:9443"
SOURCE = "kis"

TOKEN_PATH = "/oauth2/tokenP"

# 발급한 토큰을 담아 두는 키. 발급 횟수 제한이 있어 폴링마다 받을 수 없다.
TOKEN_CACHE_KEY = "kis_access_token"

# 만료 직전에 미리 갈아 끼운다. 폴링 도중 만료돼 401이 나는 걸 줄인다.
TOKEN_REFRESH_MARGIN = timedelta(minutes=30)

# 한 거래일에 허용하는 최대 호출 수. 커서가 앞으로 나아가지 않을 때의 안전장치다.
MAX_STOCK_BAR_CALLS = 6
# NXT는 프리(08:00~08:50)·주간(09:00~15:20)·애프터(15:30~20:00)를 다 담아 KRX보다 창이 길다.
# 최대 약 700봉을 120봉씩 걷으므로 호출 상한도 따로 둔다.
MAX_NXT_STOCK_BAR_CALLS = 8

# 정규장 경계(KST). 이 밖의 봉은 저장하지 않는다. 15:32 같은 시간외 체결이 섞이면 한 심볼의
# 시계열에 성격이 다른 거래가 들어간다(실측: 005930 2026-08-14에 153200 봉 11,196,308주).
SESSION_FIRST_BAR = time(9, 0)
SESSION_LAST_BAR = time(15, 30)

# NXT 수집 창(KST). 세 세션을 한 창으로 담는다(실측 2026-08-18: 프리 08:00~08:50,
# 주간 09:00~15:20, 애프터 15:30~20:00 봉이 한 조회에 이어져 온다). 세션 사이 공백은
# 봉이 없어 자연히 비므로 경계를 셋으로 나눌 필요가 없다.
NXT_SESSION_FIRST_BAR = time(8, 0)
NXT_SESSION_LAST_BAR = time(20, 0)

KST = ZoneInfo("Asia/Seoul")

REQUEST_TIMEOUT_SECONDS = 30


class DomesticFuture(StrEnum):
    """수집 대상. 저장 식별자, 상품 자릿수, 한국어 이름을 한 줄에 묶는다.

    KIS 종목코드는 `A0` + **상품 자릿수** + **연도 끝자리** + **만기월** 이다. 상품 자릿수를
    직접 확인했다(KIS 종목 마스터 `fo_idx_code_mts.mst`).

    | 자릿수 | 상품 |
    | --- | --- |
    | `1` | KOSPI200 정규 선물 (`A01609` = "F 202609") |
    | `5` | 미니 KOSPI200 선물 (`A05608` = "미니F 202608") |
    | `6` | 코스닥150 선물 (`A06609`) |

    **미니가 아니라 정규를 쓴다.** 계약 크기가 1/5라 다른 상품이고, 거래량도 정규가 훨씬
    많다(실측: 같은 102봉 구간에서 정규 최근월물 16,393 대 차근월물 119).

    Enum 값은 `quote_bar.symbol`에 그대로 저장한다. 종목코드(`A01609`)를 저장하면 월물이
    바뀔 때마다 심볼이 달라져 시계열이 끊긴다. 실제 월물은 `contract_code`에 따로 남긴다.
    """

    product_digit: str
    label: str

    def __new__(cls, symbol: str, product_digit: str, label: str) -> Self:
        member = str.__new__(cls, symbol)
        member._value_ = symbol
        member.product_digit = product_digit
        member.label = label
        return member

    KOSPI200_FUT = ("KOSPI200_FUT", "1", "코스피200 선물")
    # 코스피는 현물(`KOSPI200`)과 선물이 짝인데 코스닥은 현물뿐이었다. 반쪽을 채운다.
    # `A06609`(코스닥150F 202609) 102봉으로 확인했다.
    KOSDAQ150_FUT = ("KOSDAQ150_FUT", "6", "코스닥150 선물")


# KOSPI200·코스닥150 선물은 분기물이다. 미니와 달리 월물이 없다.
# 둘 다 `A0x609`와 `A0x612`가 오고 그 사이 월물은 없는 것을 확인했다.
CONTRACT_MONTHS: tuple[int, ...] = (3, 6, 9, 12)


class DomesticIndex(StrEnum):
    """수집 대상 국내 지수. 저장 식별자, KRX 업종코드, 한국어 이름을 한 줄에 묶는다.

    업종 분봉은 `FID_COND_MRKT_DIV_CODE='U'`로 조회한다. **KRX 지수다.** NXT(넥스트레이드)는
    지수를 내지 않으므로 이 조회에는 거래소 구분이 없다. 국내 주식 시세를 나중에 붙일 때는
    `FID_COND_MRKT_DIV_CODE='J'`(KRX)를 쓰고 `NX`(NXT)나 `UN`(통합)은 쓰지 않는다. 통합
    시세는 두 거래소 체결을 섞어 KRX 단독과 값이 달라진다.

    코스피를 Yahoo(`^KS11`)가 아니라 여기서 받는 이유는 둘이다. **국내에서 받을 수 있는
    것은 국내를 우선한다**는 원칙이고, 실제로 Yahoo의 `^KS11` 분봉은 일중 변동이 5~10%로
    나오는 날이 있어 신뢰할 수 없었다(문서 §8.4).
    """

    index_code: str
    label: str

    def __new__(cls, symbol: str, index_code: str, label: str) -> Self:
        member = str.__new__(cls, symbol)
        member._value_ = symbol
        member.index_code = index_code
        member.label = label
        return member

    KOSPI = ("KOSPI", "0001", "코스피")
    # 코스피200 선물의 기초지수다. 이게 있어야 선물가와 빼서 베이시스를 낼 수 있다.
    # 봉 단위 베이시스는 API가 주지 않으므로 둘을 각각 받아 조회 쪽에서 뺀다.
    KOSPI200 = ("KOSPI200", "2001", "코스피200")
    KOSDAQ = ("KOSDAQ", "1001", "코스닥")


class StockExchange(StrEnum):
    """국내 주식 거래소. 값이 `stock_bar.exchange`에 그대로 저장된다.

    `division_code`가 KIS `FID_COND_MRKT_DIV_CODE` 값이다. 통합(`UN`)은 쓰지 않는다 —
    두 거래소 체결을 섞어 어느 쪽 값도 아니게 된다. `apps/models/market.py`의
    `StockExchange`와 값이 같아야 하고 테스트가 둘을 대조한다.
    """

    division_code: str

    def __new__(cls, value: str, division_code: str) -> Self:
        member = str.__new__(cls, value)
        member._value_ = value
        member.division_code = division_code
        return member

    KRX = ("KRX", "J")
    NXT = ("NXT", "NX")

    @property
    def first_bar(self) -> time:
        return SESSION_FIRST_BAR if self is StockExchange.KRX else NXT_SESSION_FIRST_BAR

    @property
    def last_bar(self) -> time:
        return SESSION_LAST_BAR if self is StockExchange.KRX else NXT_SESSION_LAST_BAR

    @property
    def max_calls(self) -> int:
        return MAX_STOCK_BAR_CALLS if self is StockExchange.KRX else MAX_NXT_STOCK_BAR_CALLS


# NXT REST 수집을 끄는 손잡이. 값은 DAG가 아니라 여기서 읽는다 — 두 DAG가 같은 판단을 하고
# 한쪽만 고치는 일이 없어야 한다(`modules/sql.py`가 AIRFLOW_HOME을 읽는 것과 같은 자리다).
NXT_REST_FLAG = "KIS_ENABLE_NXT_REST"
FLAG_ON_VALUES = frozenset({"1", "true", "yes", "on"})
FLAG_OFF_VALUES = frozenset({"0", "false", "no", "off"})


def rest_exchanges() -> tuple[StockExchange, ...]:
    """REST로 종목 봉을 받을 거래소. `KIS_ENABLE_NXT_REST`가 꺼져 있으면 KRX만.

    NXT가 흔들릴 때 **코드를 고치지 않고 NXT만 떼기 위한 손잡이다**(분봉 문서 §3.3·§11.1).
    KRX는 끌 수 없다 — 그건 이 수집을 통째로 멈추는 것이고 그때는 DAG를 pause 한다.

    **기본은 켜짐이다.** REST NXT는 이미 상시 수집 중이라 기본을 끄면 손잡이를 넣는 변경만으로
    수집이 조용히 멈춘다. WebSocket 쪽 `KIS_ENABLE_NXT_WEBSOCKET`도 같은 기본값·같은 허용
    값이다(`apps/realtime/main.py`). 두 손잡이가 다르게 동작하면 한쪽을 끈 사람이 다른 쪽도
    껐다고 믿는다.

    **모르는 값은 실패시킨다.** `KIS_ENABLE_NXT_REST=fasle`가 조용히 켜짐으로 읽히면 손잡이를
    당겼다고 믿는 사람과 실제 동작이 갈린다. 재시도해도 같은 답이라 부르는 DAG가
    `AirflowFailException`으로 바꾼다.
    """
    raw = (environ.get(NXT_REST_FLAG) or "").strip().lower()
    if not raw or raw in FLAG_ON_VALUES:
        return tuple(StockExchange)
    if raw in FLAG_OFF_VALUES:
        return (StockExchange.KRX,)
    raise ValueError(f"{NXT_REST_FLAG} must be one of {sorted(FLAG_ON_VALUES | FLAG_OFF_VALUES)}, got {raw!r}")


class DomesticStock(StrEnum):
    """분봉을 받을 개별 종목. 값이 한국거래소 6자리 코드다.

    **`quote_bar.symbol`에 이 값을 그대로 넣는다.** 지수와 선물은 `KOSPI`·`KOSPI200_FUT`처럼
    이름을 쓰지만 종목은 코드를 쓴다. 공시·수급·포지션 테이블이 전부 `stock_code` 6자리를
    키로 쓰고 있어, 봉만 이름을 쓰면 화면에서 조인이 안 된다. 사람이 읽을 이름은
    `quote_symbol` 마스터와 `instrument`가 갖는다.

    `tests/collectors/test_kis.py`가 다른 수집기 Enum과 이 값들을 대조한다.
    """

    label: str

    def __new__(cls, code: str, label: str) -> Self:
        member = str.__new__(cls, code)
        member._value_ = code
        member.label = label
        return member

    SAMSUNG_ELECTRONICS = ("005930", "삼성전자")
    SK_HYNIX = ("000660", "SK하이닉스")
class TokenStore(Protocol):
    """토큰 캐시가 쓰는 저장소. 운영에서는 Airflow `Variable`이 그대로 들어맞는다.

    이 모듈이 `airflow`를 import하지 않게 하려고 구조적 타입으로 받는다. 덕분에 캐시
    판정을 Airflow 없이 테스트할 수 있다.
    """

    def get(self, key: str, default: str | None = None) -> str | None: ...

    def set(self, key: str, value: str) -> None: ...


class KisHTTPError(RuntimeError):
    """KIS가 2xx가 아닌 상태로 응답했다. 재시도 가능 여부는 호출자가 `status`로 판단한다."""

    def __init__(self, status: int, message: str = "") -> None:
        super().__init__(f"KIS request failed with HTTP {status}" + (f": {message}" if message else ""))
        self.status = status
        self.message = message


class KisResultError(RuntimeError):
    """KIS가 본문 `rt_cd`로 실패를 알렸다. 재시도 여부는 DAG가 `code`로 정한다."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"KIS returned {code}: {message}")
        self.code = code
        self.message = message


class KisPayloadError(ValueError):
    """응답이 분봉 계약을 지키지 않았다. 재시도해도 같은 결과다.

    **최근 구간에 새 봉이 없는 것은 여기 해당하지 않는다.** 그건 휴장이지 고장이 아니다.
    """


def expiry_date(year: int, month: int) -> date:
    """KOSPI200 선물 만기일. 만기월의 **두 번째 목요일**이다."""
    first = date(year, month, 1)
    # 첫 목요일까지의 일수. `weekday()`는 월요일이 0이고 목요일이 3이다.
    first_thursday = 1 + (3 - first.weekday()) % 7
    return date(year, month, first_thursday + 7)


def front_contract(future: DomesticFuture, today: date) -> str:
    """그날 거래되는 최근월물 코드.

    분기물(3·6·9·12월)이고 만기일을 지나면 다음 분기로 넘어간다. 만기 당일은 아직 거래되므로
    포함한다.

    코드를 하드코딩하지 않고 규칙으로 만든다. 규칙이 틀리면 조회가 0봉으로 끝나는데,
    `parse_bars`가 빈 응답을 실패로 만들기 때문에 조용히 넘어가지 않는다.
    """
    for offset in range(5):
        month_index = (today.month - 1) + offset
        year = today.year + month_index // 12
        month = month_index % 12 + 1
        if month not in CONTRACT_MONTHS:
            continue
        if expiry_date(year, month) >= today:
            return f"A0{future.product_digit}{year % 10}{month:02d}"
    raise ValueError(f"No contract month found for {today}")


class QuoteBar(BaseModel):
    """정규화한 1분봉 1건."""

    model_config = ConfigDict(frozen=True)

    bar_at: AwareDatetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int | None
    previous_close: Decimal

    @field_validator("bar_at")
    @classmethod
    def normalize_to_utc(cls, moment: datetime) -> datetime:
        return moment.astimezone(UTC)

    @field_validator("open", "high", "low", "close", "previous_close")
    @classmethod
    def require_finite(cls, value: Decimal) -> Decimal:
        # Decimal은 "NaN"과 "Infinity"도 받아들인다. 시세로 저장하면 이후 집계가 전부 오염된다.
        if not value.is_finite():
            raise ValueError("quote value must be a finite number")
        return value


class KisResponse(BaseModel):
    """한 번의 호출 결과와 그 호출을 재현하는 데 필요한 메타데이터.

    `symbol`은 저장 식별자이고 `contract_code`는 선물의 실제 월물이다. 지수는 월물이 없어
    `None`이다.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    contract_code: str | None = None
    body: bytes
    status: int
    started_at: AwareDatetime
    completed_at: AwareDatetime

    @field_validator("started_at", "completed_at")
    @classmethod
    def normalize_to_utc(cls, moment: datetime) -> datetime:
        return moment.astimezone(UTC)

    @model_validator(mode="after")
    def require_ordered_timestamps(self) -> Self:
        if self.started_at > self.completed_at:
            raise ValueError("started_at must not be after completed_at")
        return self


def issue_token(app_key: SecretStr, app_secret: SecretStr) -> tuple[SecretStr, datetime]:
    """접근 토큰과 만료 시각을 받는다.

    **발급 횟수에 제한이 있다.** 폴링마다 부르면 안 되고 호출자가 캐시해야 한다. 토큰은
    24시간짜리다.

    앱키가 본문에 들어가므로 예외 메시지에 본문을 싣지 않는다.
    """
    body = json.dumps(
        {
            "grant_type": "client_credentials",
            "appkey": app_key.get_secret_value(),
            "appsecret": app_secret.get_secret_value(),
        }
    ).encode()
    request = Request(
        KIS_BASE_URL + TOKEN_PATH,
        data=body,
        headers={"content-type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        # `from None`은 의도적이다. 요청 본문에 앱키가 있고 예외 체인이 그걸 실어 나를 수 있다.
        raise KisHTTPError(error.code, "token issue failed") from None
    except URLError as error:
        raise ConnectionError(f"KIS token request failed: {error.reason}") from None

    token = payload.get("access_token")
    if not token:
        raise KisResultError(payload.get("error_code", ""), payload.get("error_description", "no access_token"))

    # `access_token_token_expired`는 "YYYY-MM-DD HH:MM:SS" KST다. 없으면 24시간으로 둔다.
    expires_raw = payload.get("access_token_token_expired", "")
    try:
        expires_at = datetime.strptime(expires_raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
    except ValueError:
        expires_at = datetime.now(UTC) + timedelta(seconds=int(payload.get("expires_in", 86400)))
    return SecretStr(token), expires_at.astimezone(UTC)


def access_token(store: TokenStore, app_key: SecretStr, app_secret: SecretStr, force: bool = False) -> SecretStr:
    """캐시된 토큰. 없거나 만료가 가까우면 새로 받아 `store`에 넣는다.

    `force=True`는 401을 만났을 때 쓴다. 캐시가 살아 있어도 다시 받는다.
    """
    if not force:
        cached = store.get(TOKEN_CACHE_KEY, default=None)
        if cached:
            try:
                stored = json.loads(cached)
                expires_at = datetime.fromisoformat(stored["expires_at"])
                if expires_at - TOKEN_REFRESH_MARGIN > datetime.now(UTC):
                    return SecretStr(stored["token"])
            except (ValueError, KeyError, TypeError):
                # 캐시가 깨졌으면 그냥 새로 받는다. 값을 로그에 찍지 않는다.
                logger.warning("Cached KIS token is unreadable; issuing a new one")

    token, expires_at = issue_token(app_key, app_secret)
    store.set(
        TOKEN_CACHE_KEY,
        json.dumps({"token": token.get_secret_value(), "expires_at": expires_at.isoformat()}),
    )
    logger.info("Issued a new KIS token (expires %s)", expires_at.isoformat())
    return token


def send_get(
    token: SecretStr,
    app_key: SecretStr,
    app_secret: SecretStr,
    path: str,
    tr_id: str,
    query: dict,
    tr_cont: str = "",
) -> tuple[bytes, int, Mapping[str, str]]:
    """KIS GET 조회 한 번. 모든 엔드포인트가 같은 헤더 규약을 쓴다.

    **응답 헤더를 함께 돌려준다.** 연속조회 여부(`tr_cont`)가 본문이 아니라 헤더에 오기
    때문이다. 분봉 조회는 헤더를 보지 않지만 휴장일·결제일 조회는 이 값으로 다음 장을 받는다.

    `tr_cont`는 요청에도 들어간다. 첫 장은 빈 문자열이고 다음 장부터 `N`이다.
    """
    request = Request(
        f"{KIS_BASE_URL}{path}?{urlencode(query)}",
        headers={
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token.get_secret_value()}",
            "appkey": app_key.get_secret_value(),
            "appsecret": app_secret.get_secret_value(),
            "tr_id": tr_id,
            "custtype": "P",
            "tr_cont": tr_cont,
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return response.read(), response.status, dict(response.headers)
    except HTTPError as error:
        # KIS는 실패를 500으로 내면서 본문에 사유를 담는다. 본문에는 비밀이 없으므로 읽어서
        # 메시지로 올린다. 헤더에 있는 앱키는 예외에 실리지 않는다.
        raise KisHTTPError(error.code, _extract_message(error.read())) from None
    except URLError as error:
        raise ConnectionError(f"KIS request failed: {error.reason}") from None


def _extract_message(raw: bytes) -> str:
    """오류 본문에서 `msg_cd`/`msg1`을 긁는다.

    **표준 JSON 파서를 쓰지 않는다.** KIS는 실패 응답에 `{rt_cd:"1","msg1":"..."}`처럼 키에
    따옴표가 없는 비표준 JSON을 담는다(실측). 파싱에 실패하면 사유를 통째로 잃는다.
    """
    text = raw.decode("utf-8", "replace")
    code = re.search(r'msg_cd"?\s*:\s*"([^"]*)"', text)
    message = re.search(r'msg1"?\s*:\s*"([^"]*)"', text)
    parts = [part.group(1).strip() for part in (code, message) if part]
    return " ".join(parts) or text[:200]


def _decimal(value: str, field: str) -> Decimal:
    # 값이 `"         976.16"`처럼 공백으로 패딩돼 온다.
    try:
        return Decimal(value.strip())
    except (InvalidOperation, ValueError) as error:
        raise KisPayloadError(f"KIS returned a non-numeric {field}: {value!r}") from error


# 쿼리는 `sql/` 볼륨에 둔다. 배포 Airflow가 `/opt/airflow/sql`로 마운트하는 폴더다.
SOURCE_RECORD_INSERT = read_sql("postgres", "source_record", "insert.sql")
# 봉은 kind별 테이블로 갈라 저장한다. 지수는 index_bar, 선물은 index_future_bar(월물 포함),
# 종목은 stock_bar(거래소 축 포함)다. 기존 quote_bar는 이들을 합쳐 보여 주는 읽기 전용 뷰다.
INDEX_BAR_UPSERT = read_sql("postgres", "index_bar", "upsert.sql")
