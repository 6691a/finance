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
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from os import environ
from time import sleep as wait_seconds
from typing import Any, Protocol, Self
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

from modules.sql import read_sql
from modules.upsert import execute_upserts

logger = logging.getLogger(__name__)

KIS_BASE_URL = "https://openapi.koreainvestment.com:9443"
SOURCE = "kis"
SOURCE_KEY = "intraday_1m"

TOKEN_PATH = "/oauth2/tokenP"

# 발급한 토큰을 담아 두는 키. 발급 횟수 제한이 있어 폴링마다 받을 수 없다.
TOKEN_CACHE_KEY = "kis_access_token"

# 만료 직전에 미리 갈아 끼운다. 폴링 도중 만료돼 401이 나는 걸 줄인다.
TOKEN_REFRESH_MARGIN = timedelta(minutes=30)

# 선물옵션 분봉. 값 컬럼이 `futs_*`다.
FUTURE_CHART_PATH = "/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice"
FUTURE_CHART_TR_ID = "FHKIF03020200"

# 업종(지수) 분봉. 값 컬럼이 `bstp_nmix_*`라 선물과 다르다.
INDEX_CHART_PATH = "/uapi/domestic-stock/v1/quotations/inquire-time-indexchartprice"
INDEX_CHART_TR_ID = "FHKUP03500200"

# 종목 분봉. **일자별 조회를 쓴다.** 당일 조회(`FHKST03010200`)와 값이 갈리는데,
# 장중 한복판 봉은 둘이 완전히 같고 마감 동시호가 구간에서만 다르다. 당일 조회는 15:30 봉에
# 동시호가 물량을 두 번 실어(실측: 2,730,280 = 2 × 1,365,140) 하루 합이 누적 거래량을 넘는다.
# 호출 수도 일자별이 4배 적다(한 번에 120봉 대 30봉).
STOCK_CHART_PATH = "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice"
STOCK_CHART_TR_ID = "FHKST03010230"
STOCK_BARS_SOURCE_KEY = "stock_minute_bars"

# 한 번에 오는 봉 수(실측). 정규장 381봉을 덮으려면 네 번이면 된다.
STOCK_BARS_PER_CALL = 120

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

# 업종 현재가. 상승·보합·하락 종목 수가 여기 들어 있어 전 종목을 순회할 필요가 없다.
INDEX_PRICE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-index-price"
INDEX_PRICE_TR_ID = "FHPUP02100000"
INDEX_PRICE_SOURCE_KEY = "inquire_index_price"

KST = ZoneInfo("Asia/Seoul")

# KIS가 한 번에 돌려주는 최대 봉 수. 문서에 명시돼 있고 실측도 같다.
MAX_BARS_PER_REQUEST = 102

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


# 상승·보합·하락 분포를 저장하는 지수. `DomesticIndex`에는 코스피200도 있지만 그것은
# 코스피의 부분집합이라 시장 전반의 분포가 아니다. 순회하지 않고 명시적으로 고른다.
MOVEMENT_INDEXES: tuple[DomesticIndex, ...] = (DomesticIndex.KOSPI, DomesticIndex.KOSDAQ)


# 업종 분봉의 기타 구분 코드. **`1`이어야 한다.**
# `0`을 쓰면 시각이 `999999`(장마감)와 `888888`(시간외)인 의사 봉이 섞여 들어와 시각 파싱이
# 깨진다. 실측으로 확인했다.
INDEX_ETC_CLS_CODE = "1"


class Cursor(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(self, *args: object) -> bool | None: ...

    def execute(self, statement: str, parameters: Sequence[Any]) -> object: ...

    def executemany(self, statement: str, parameters: Sequence[Sequence[Any]]) -> object: ...

    def fetchone(self) -> Any: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...


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


class ParsedBars(BaseModel):
    """한 번의 조회 결과.

    `bars`는 요청한 구간으로 걸러진 봉이고 비어 있을 수 있다(휴장). `latest_bar_at`은
    거르기 **전** 응답 전체의 마지막 봉 시각이다. 둘을 나눠 두면 "휴장이라 조용한 것"과
    "제공처가 며칠째 안 갱신되는 것"을 나중에 구분할 수 있다.
    """

    model_config = ConfigDict(frozen=True)

    bars: tuple[QuoteBar, ...]
    latest_bar_at: AwareDatetime | None
    contract_name: str


class KisRawBar(BaseModel):
    """`output2` 한 건. 값은 전부 문자열로 오고 공백 패딩이 붙기도 한다.

    **대상마다 값 컬럼 이름이 다르다.** 선물은 `futs_*`, 업종지수는 `bstp_nmix_*`, 개별
    종목은 `stck_*`다. 셋을 모두 선택으로 받고 `prices()`가 채워진 쪽을 고른다. 응답마다
    한 종류만 오므로 셋 다 비어 있으면 계약이 깨진 것이다.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    business_date: str = Field(alias="stck_bsop_date")
    contract_hour: str = Field(alias="stck_cntg_hour")
    volume: str = Field(default="", alias="cntg_vol")

    futures_open: str = Field(default="", alias="futs_oprc")
    futures_high: str = Field(default="", alias="futs_hgpr")
    futures_low: str = Field(default="", alias="futs_lwpr")
    futures_close: str = Field(default="", alias="futs_prpr")

    index_open: str = Field(default="", alias="bstp_nmix_oprc")
    index_high: str = Field(default="", alias="bstp_nmix_hgpr")
    index_low: str = Field(default="", alias="bstp_nmix_lwpr")
    index_close: str = Field(default="", alias="bstp_nmix_prpr")

    stock_open: str = Field(default="", alias="stck_oprc")
    stock_high: str = Field(default="", alias="stck_hgpr")
    stock_low: str = Field(default="", alias="stck_lwpr")
    stock_close: str = Field(default="", alias="stck_prpr")

    def prices(self) -> tuple[str, str, str, str]:
        """(시가, 고가, 저가, 종가). 선물·지수·종목 중 채워진 쪽을 돌려준다."""
        if self.futures_close.strip():
            return (self.futures_open, self.futures_high, self.futures_low, self.futures_close)
        if self.index_close.strip():
            return (self.index_open, self.index_high, self.index_low, self.index_close)
        if self.stock_close.strip():
            return (self.stock_open, self.stock_high, self.stock_low, self.stock_close)
        raise KisPayloadError("KIS bar has no futures, index, or stock prices")


class KisChartHead(BaseModel):
    """`output1`. 계약 정보와 전일종가가 여기 있다.

    전일종가도 선물(`futs_prdy_clpr`)과 지수(`prdy_nmix`)가 이름이 다르다. 어느 쪽이든
    **조회 구간과 무관하게 진짜 전일종가**라, Yahoo 의 `chartPreviousClose` 와 달리 백필해도
    맞다(문서 §8.3).
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    name: str = Field(default="", alias="hts_kor_isnm")
    futures_previous_close: str = Field(default="", alias="futs_prdy_clpr")
    index_previous_close: str = Field(default="", alias="prdy_nmix")

    def previous_close(self) -> str:
        return self.futures_previous_close.strip() or self.index_previous_close.strip()


class KisChartPayload(BaseModel):
    """분봉 응답 본문. 검증에 필요한 필드만 읽고 나머지는 버린다."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    rt_cd: str = ""
    msg_cd: str = ""
    msg1: str = ""
    output1: KisChartHead = KisChartHead()
    output2: tuple[KisRawBar, ...] = ()


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


def parse_bars(body: bytes, since: datetime | None = None) -> ParsedBars:
    """유효한 1분봉을 뽑는다.

    **판정을 두 단계로 나눈다.** `yahoo.py`와 같은 이유다.

    1. **본문 오류나 빈 배열은 실패시킨다.** 권한이 없거나 종목코드가 틀리면 KIS는 200에
       `rt_cd != "0"`으로 답한다. 월물 규칙이 어긋나도 빈 배열이 온다. 성공으로 넘기면
       조회 구간에 조용히 구멍이 남는다.
    2. **`since` 필터 결과가 0건인 것은 정상이다.** 봉은 있는데 최근 구간에 없다는 뜻이고
       그건 휴장이다(정규장 09:00~15:45 KST 밖).

    상한(`until`)은 받지 않는다. KIS는 한 번에 102봉만 주고 백필 경로가 없어서 폴링이
    항상 "지금 기준 최근 N분"만 저장하기 때문이다. Yahoo 쪽은 백필이 8일 창을 이어 붙이므로
    경계 봉이 두 창에 겹치지 않게 상한이 필요하다.

    응답은 **최신순**이라 저장 전에 오름차순으로 뒤집는다.
    """
    try:
        payload = KisChartPayload.model_validate_json(body)
    except ValidationError as error:
        raise KisPayloadError("KIS response is not a valid chart payload") from error

    if payload.rt_cd and payload.rt_cd != "0":
        raise KisResultError(payload.msg_cd, payload.msg1.strip())
    if not payload.output2:
        raise KisPayloadError("KIS returned an empty chart")

    previous_close = _decimal(payload.output1.previous_close() or "0", "previous close")
    if not previous_close:
        raise KisPayloadError("KIS response has no previous close")

    bars: list[QuoteBar] = []
    latest_bar_at: datetime | None = None
    for raw in payload.output2:
        stamp = f"{raw.business_date.strip()}{raw.contract_hour.strip()}"
        try:
            # KIS가 주는 시각은 KST 벽시계다. 저장은 UTC다.
            bar_at = datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=KST).astimezone(UTC)
        except ValueError as error:
            raise KisPayloadError(f"KIS returned an unparsable timestamp: {stamp!r}") from error

        if latest_bar_at is None or bar_at > latest_bar_at:
            latest_bar_at = bar_at
        if since is not None and bar_at < since:
            continue

        volume = raw.volume.strip()
        open_, high, low, close = raw.prices()
        try:
            bar = QuoteBar(
                bar_at=bar_at,
                open=_decimal(open_, "open"),
                high=_decimal(high, "high"),
                low=_decimal(low, "low"),
                close=_decimal(close, "close"),
                volume=int(volume) if volume else None,
                previous_close=previous_close,
            )
        except ValidationError as error:
            raise KisPayloadError("KIS returned an invalid bar") from error
        bars.append(bar)

    # 응답은 최신순이다. 저장 순서를 Yahoo 쪽과 맞춘다.
    bars.sort(key=lambda bar: bar.bar_at)
    return ParsedBars(bars=tuple(bars), latest_bar_at=latest_bar_at, contract_name=payload.output1.name.strip())


class MarketMovement(BaseModel):
    """한 지수의 상승·보합·하락 종목 분포.

    다섯 값을 날것으로 보존한다. **상한가는 상승에 포함된다**(실측). 그래서 전체 종목 수는
    상승+보합+하락이고 다섯 값을 더하면 상·하한가가 이중 계산된다. 그 계산은 조회 쪽이
    하고 여기서는 비율이나 3분류를 만들지 않는다.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    observed_at: AwareDatetime
    upper_limit_count: int = Field(ge=0)
    rising_count: int = Field(ge=0)
    unchanged_count: int = Field(ge=0)
    falling_count: int = Field(ge=0)
    lower_limit_count: int = Field(ge=0)

    @field_validator("observed_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @property
    def closed(self) -> bool:
        """장 밖의 리셋 상태.

        **장중에는 상승·보합·하락의 합이 전 종목이라 all-zero가 나올 수 없다.** 그래서
        다섯 값이 모두 0이면 분포가 아니라 "장이 안 열려 있다"는 뜻이다(실측: 마감 뒤 응답이
        종목 수와 거래량을 0으로 돌려주고 지수 값만 전일 종가로 남는다).
        """
        return not any(
            (
                self.upper_limit_count,
                self.rising_count,
                self.unchanged_count,
                self.falling_count,
                self.lower_limit_count,
            )
        )


def _count(value: str | None, field: str) -> int:
    """종목 수 한 칸. 값이 공백으로 패딩돼 오고 쉼표가 붙는 경우도 있다."""
    text = (value or "").strip().replace(",", "")
    if not text:
        return 0
    try:
        count = int(text)
    except ValueError:
        raise KisPayloadError(f"KIS returned a non-numeric {field}: {value!r}") from None
    if count < 0:
        raise KisPayloadError(f"KIS returned a negative {field}: {value!r}")
    return count


class StockBarFetch(BaseModel):
    """한 종목·한 거래일·한 거래소의 분봉 수집 결과."""

    model_config = ConfigDict(frozen=True)

    stock_code: str
    exchange: StockExchange
    business_date: date
    bars: tuple[QuoteBar, ...]
    call_count: int
    started_at: AwareDatetime
    completed_at: AwareDatetime


def _stock_bar_rows(body: bytes) -> tuple[KisRawBar, ...]:
    try:
        payload = KisChartPayload.model_validate_json(body)
    except ValidationError as error:
        raise KisPayloadError("KIS response is not a valid chart payload") from error
    if payload.rt_cd and payload.rt_cd != "0":
        raise KisResultError(payload.msg_cd, payload.msg1.strip())
    return payload.output2


def _bar_time(row: KisRawBar) -> time:
    stamp = row.contract_hour.strip()
    # 벽시계 시각만 온다. 날짜와 시간대는 `_stock_bar`가 붙인다.
    if len(stamp) != 6 or not stamp.isdigit():
        raise KisPayloadError(f"KIS returned an unparsable bar time: {stamp!r}")
    try:
        return time(int(stamp[:2]), int(stamp[2:4]), int(stamp[4:]))
    except ValueError as error:
        raise KisPayloadError(f"KIS returned an unparsable bar time: {stamp!r}") from error


def _stock_bar(row: KisRawBar, business_date: date, moment: time, previous_close: Decimal) -> QuoteBar:
    open_, high, low, close = row.prices()
    volume = row.volume.strip()
    try:
        return QuoteBar(
            bar_at=datetime.combine(business_date, moment, tzinfo=KST).astimezone(UTC),
            open=_decimal(open_, "open"),
            high=_decimal(high, "high"),
            low=_decimal(low, "low"),
            close=_decimal(close, "close"),
            volume=int(volume) if volume else None,
            previous_close=previous_close,
        )
    except ValidationError as error:
        raise KisPayloadError("KIS returned an invalid stock bar") from error


def parse_market_movement(response: KisResponse, observed_at: datetime) -> MarketMovement:
    """지수 현재가 응답에서 다섯 종목 수를 읽는다.

    `observed_at`은 제공처가 준 시각이 아니라 **응답을 받은 시각**이다. 이 조회에는 원천
    체결 시각이 없어서 호출자가 분 단위로 절삭해 넘긴다.
    """
    try:
        payload = json.loads(response.body)
    except json.JSONDecodeError as error:
        raise KisPayloadError(f"KIS returned a non-JSON index price body: {error}") from None
    if not isinstance(payload, dict):
        raise KisPayloadError("KIS returned an index price body that is not an object")

    code = str(payload.get("rt_cd", ""))
    if code != "0":
        raise KisResultError(code, str(payload.get("msg1", "")).strip())

    output = payload.get("output")
    if not isinstance(output, dict):
        raise KisPayloadError("KIS index price response has no output object")

    try:
        return MarketMovement(
            symbol=response.symbol,
            observed_at=observed_at,
            upper_limit_count=_count(output.get("uplm_issu_cnt"), "uplm_issu_cnt"),
            rising_count=_count(output.get("ascn_issu_cnt"), "ascn_issu_cnt"),
            unchanged_count=_count(output.get("stnr_issu_cnt"), "stnr_issu_cnt"),
            falling_count=_count(output.get("down_issu_cnt"), "down_issu_cnt"),
            lower_limit_count=_count(output.get("lslm_issu_cnt"), "lslm_issu_cnt"),
        )
    except ValidationError as error:
        raise KisPayloadError("KIS returned an invalid movement distribution") from error


# 쿼리는 `sql/` 볼륨에 둔다. 배포 Airflow가 `/opt/airflow/sql`로 마운트하는 폴더다.
SOURCE_RECORD_INSERT = read_sql("postgres", "source_record", "insert.sql")
# 봉은 kind별 테이블로 갈라 저장한다. 지수는 index_bar, 선물은 index_future_bar(월물 포함),
# 종목은 stock_bar(거래소 축 포함)다. 기존 quote_bar는 이들을 합쳐 보여 주는 읽기 전용 뷰다.
INDEX_BAR_UPSERT = read_sql("postgres", "index_bar", "upsert.sql")
INDEX_FUTURE_BAR_UPSERT = read_sql("postgres", "index_future_bar", "upsert.sql")
STOCK_BAR_UPSERT = read_sql("postgres", "stock_bar", "upsert.sql")
MARKET_MOVEMENT_UPSERT = read_sql("postgres", "market_movement_snapshot", "upsert.sql")
INDEX_DAILY_UPSERT = read_sql("postgres", "index_daily", "upsert.sql")

# 지수 일봉(국내주식업종기간별시세). 기술지표 계산의 원천이다
# (docs/market-technical-indicators.md 4절).
INDEX_DAILY_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice"
INDEX_DAILY_TR_ID = "FHKUP03500100"
INDEX_DAILY_SOURCE_KEY = "inquire_daily_indexchartprice"
# 연속조회 여부는 응답 헤더 `tr_cont`로 온다. 달력 수집기와 같은 값이다.
INDEX_DAILY_CONTINUE_FLAGS = frozenset({"M", "F"})
# 한 심볼에 허용하는 최대 장 수. 200달력일 구간이 이 안에 들어오지 않으면 계약이 깨진 것이다.
INDEX_DAILY_MAX_PAGES = 10
# 페이지 사이 대기. 달력 수집기의 PAGE_DELAY_SECONDS와 같은 이유다.
INDEX_DAILY_PAGE_DELAY_SECONDS = 0.5


class KisDailyIndexRow(BaseModel):
    """지수 일봉 `output2` 한 건. 값은 전부 문자열이고 공백 패딩이 붙는다."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    business_date: str = Field(alias="stck_bsop_date")
    open: str = Field(alias="bstp_nmix_oprc")
    high: str = Field(alias="bstp_nmix_hgpr")
    low: str = Field(alias="bstp_nmix_lwpr")
    close: str = Field(alias="bstp_nmix_prpr")
    volume: str = Field(alias="acml_vol")


class DailyIndexBar(BaseModel):
    """정규화한 지수 일봉 1건."""

    model_config = ConfigDict(frozen=True)

    business_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int = Field(ge=0)

    @field_validator("open", "high", "low", "close")
    @classmethod
    def require_positive_and_finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= 0:
            raise ValueError("index daily price must be a finite positive number")
        return value

    @model_validator(mode="after")
    def require_a_consistent_range(self) -> Self:
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("high must be at least open, close, and low")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("low must be at most open, close, and high")
        return self


class DailyIndexFetch(BaseModel):
    """한 지수·한 구간의 일봉 수집 결과. `bars`는 거래일 오름차순이다."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    start_date: date
    end_date: date
    bars: tuple[DailyIndexBar, ...]
    page_count: int
    started_at: AwareDatetime
    completed_at: AwareDatetime


def _daily_index_rows(body: bytes) -> tuple[KisDailyIndexRow, ...]:
    """일봉 응답 본문을 검증해 원시 행을 꺼낸다. `rt_cd`가 0이 아니면 `KisResultError`다."""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as error:
        raise KisPayloadError(f"KIS returned a non-JSON index daily body: {error}") from None
    if not isinstance(payload, dict):
        raise KisPayloadError("KIS returned an index daily body that is not an object")

    code = str(payload.get("rt_cd", ""))
    if code != "0":
        raise KisResultError(code, str(payload.get("msg1", "")).strip())

    output = payload.get("output2")
    if not isinstance(output, list):
        raise KisPayloadError("KIS index daily response has no output2 list")
    try:
        return tuple(KisDailyIndexRow.model_validate(row) for row in output)
    except ValidationError as error:
        raise KisPayloadError("KIS index daily row is malformed") from error


def _daily_index_bar(row: KisDailyIndexRow) -> DailyIndexBar:
    raw_date = row.business_date.strip()
    if not re.fullmatch(r"\d{8}", raw_date):
        raise KisPayloadError(f"KIS index daily date is malformed: {raw_date!r}")
    try:
        return DailyIndexBar(
            business_date=date(int(raw_date[:4]), int(raw_date[4:6]), int(raw_date[6:])),
            open=Decimal(row.open.strip()),
            high=Decimal(row.high.strip()),
            low=Decimal(row.low.strip()),
            close=Decimal(row.close.strip()),
            volume=int(row.volume.strip()),
        )
    except (InvalidOperation, ValueError, ValidationError) as error:
        raise KisPayloadError(f"KIS index daily bar is malformed: {error}") from None


class SymbolOutcome(BaseModel):
    """한 심볼의 수집 결과. 성공이든 실패든 `source_record.metadata`에 그대로 실린다."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    contract_code: str | None = None
    contract_name: str = ""
    status: int | None = None
    bar_count: int = 0
    latest_bar_at: str | None = None
    error: str | None = None


class KisQuoteCollector:
    """KIS 시세 수집기. 자격 증명과 토큰을 들고 지수·지수선물·종목 분봉과 종목 분포를 조회·저장한다.

    한 실행이 객체 하나다. 토큰은 발급 횟수 제한이 있어 DAG이 한 번 받아 넘긴다. 토큰은 이
    객체가 사는 동안 안 변하는 값이라 갈아 끼우지 않는다 — 401로 다시 받았으면 DAG이 새
    토큰으로 객체를 다시 만든다.

    전송(`send_get`·`issue_token`·`access_token`)은 다른 KIS 수집기도 쓰는 공용 층이라 모듈
    함수로 남는다. 파싱(`parse_bars`·`parse_market_movement`)과 월물 계산(`front_contract`)도
    자격 증명을 보지 않아 밖에 둔다.
    """

    def __init__(self, token: SecretStr, app_key: SecretStr, app_secret: SecretStr) -> None:
        self._token = token
        self._app_key = app_key
        self._app_secret = app_secret

    def fetch_bars(
        self,
        future: DomesticFuture,
        contract_code: str,
        until: datetime,
    ) -> KisResponse:
        """한 선물 계약의 1분봉을 받아 온다. 파싱은 하지 않는다.

        `until`은 조회 기준 시각이고 KIS는 **그 시각 이전 102봉**을 최신순으로 돌려준다.
        """
        reference = until.astimezone(KST)
        started_at = datetime.now(UTC)
        body, status, _ = send_get(
            self._token,
            self._app_key,
            self._app_secret,
            FUTURE_CHART_PATH,
            FUTURE_CHART_TR_ID,
            {
                "FID_COND_MRKT_DIV_CODE": "F",  # F = 지수선물
                "FID_INPUT_ISCD": contract_code,
                "FID_HOUR_CLS_CODE": "60",  # 60 = 1분봉
                "FID_PW_DATA_INCU_YN": "Y",  # 과거 데이터 포함
                "FID_FAKE_TICK_INCU_YN": "N",  # 허봉 제외
                "FID_INPUT_DATE_1": reference.strftime("%Y%m%d"),
                "FID_INPUT_HOUR_1": reference.strftime("%H%M%S"),
            },
        )
        return KisResponse(
            symbol=future.value,
            contract_code=contract_code,
            body=body,
            status=status,
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )

    def fetch_index_bars(
        self,
        index: DomesticIndex,
    ) -> KisResponse:
        """한 업종지수의 1분봉을 받아 온다.

        선물과 달리 조회 기준 시각을 받지 않는다. 이 엔드포인트의 `FID_INPUT_HOUR_1`은 기준
        시각이 아니라 **봉 간격**(60 = 1분)이다. 이름이 같아 헷갈리기 쉽다.

        `FID_ETC_CLS_CODE`는 반드시 `1`이다. `0`이면 시각이 `999999`(장마감)·`888888`(시간외)인
        의사 봉이 섞여 들어와 시각 파싱이 깨진다.
        """
        started_at = datetime.now(UTC)
        body, status, _ = send_get(
            self._token,
            self._app_key,
            self._app_secret,
            INDEX_CHART_PATH,
            INDEX_CHART_TR_ID,
            {
                "FID_COND_MRKT_DIV_CODE": "U",  # U = 업종. KRX 지수다
                "FID_ETC_CLS_CODE": INDEX_ETC_CLS_CODE,
                "FID_INPUT_ISCD": index.index_code,
                "FID_INPUT_HOUR_1": "60",  # 여기서는 봉 간격이다
                "FID_PW_DATA_INCU_YN": "Y",
            },
        )
        return KisResponse(
            symbol=index.value,
            contract_code=None,
            body=body,
            status=status,
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )

    def fetch_index_price(
        self,
        index: DomesticIndex,
    ) -> KisResponse:
        """지수 현재가를 받아 온다. 여기서 쓰는 것은 상승·보합·하락 종목 수뿐이다.

        지수 값과 거래량은 이미 `quote_bar`가 갖고 있어 다시 저장하지 않는다.
        """
        started_at = datetime.now(UTC)
        body, status, _ = send_get(
            self._token,
            self._app_key,
            self._app_secret,
            INDEX_PRICE_PATH,
            INDEX_PRICE_TR_ID,
            {
                "FID_COND_MRKT_DIV_CODE": "U",  # U = 업종. 분봉 조회와 같은 구분이다
                "FID_INPUT_ISCD": index.index_code,
            },
        )
        return KisResponse(
            symbol=index.value,
            contract_code=None,
            body=body,
            status=status,
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )

    def fetch_stock_bars(
        self,
        stock: DomesticStock,
        business_date: date,
        previous_close: Decimal,
        exchange: StockExchange = StockExchange.KRX,
    ) -> StockBarFetch:
        """한 종목의 하루치 정규장 1분봉을 받는다.

        한 응답이 120봉이라 정규장 381봉을 덮으려면 커서를 뒤로 걸며 네 번 부른다. 다음 커서는
        이번 응답에서 **그 거래일에 속한** 가장 이른 봉의 1분 전이다.

        **응답에 전 거래일 봉이 섞여 온다.** 09:00 이전을 요청하면 직전 세션의 뒷부분이 딸려
        온다(실측: 2026-08-14를 훑으면 2026-08-13의 13:42~15:32가 99봉 들어온다). 날짜로 거르지
        않고 시각만 키로 쓰면 전날 값이 그날 봉을 덮어써서 하루 합이 누적 거래량과 어긋난다.

        `previous_close`를 호출자가 넘긴다. 응답의 `output1`은 요청한 날짜와 무관하게 **지금
        시세**를 담고 있어(실측: 2026-07-03을 요청해도 `acml_vol`이 오늘 값이다) 백필에서 쓰면
        모든 봉에 오늘의 전일종가가 박힌다.

        **마감 동시호가가 15:30 봉에 없는 날이 있다.** 보통은 하루 봉 거래량 합이 그날 누적
        거래량과 0.05% 안에서 맞는데, 2026-08-13 005930은 15:19가 마지막 봉이고 동시호가가
        15:32에 찍혀 31%가 빈다. 그 15:32 행은 같은 값(11,196,308주)이 다른 날짜 응답에도
        나와서 믿을 수 없다. 그래서 정규장 밖은 그대로 버리고 **봉 합이 누적 거래량과 맞는다고
        약속하지 않는다.** 지어낸 봉을 넣는 것보다 빈 쪽이 낫다.
        """
        started_at = datetime.now(UTC)
        stamp = business_date.strftime("%Y%m%d")
        cursor = exchange.last_bar
        seen: dict[time, QuoteBar] = {}
        calls = 0

        while calls < exchange.max_calls:
            body, _, _ = send_get(
                self._token,
                self._app_key,
                self._app_secret,
                STOCK_CHART_PATH,
                STOCK_CHART_TR_ID,
                {
                    # J = KRX, NX = NXT. UN(통합)은 쓰지 않는다 — 두 거래소 체결이 섞인다.
                    "FID_COND_MRKT_DIV_CODE": exchange.division_code,
                    "FID_INPUT_ISCD": stock.value,
                    "FID_INPUT_DATE_1": stamp,
                    "FID_INPUT_HOUR_1": cursor.strftime("%H%M%S"),
                    "FID_PW_DATA_INCU_YN": "Y",
                    "FID_FAKE_TICK_INCU_YN": "N",  # 허봉 제외
                },
            )
            calls += 1
            rows = _stock_bar_rows(body)
            if not rows:
                break

            same_day = [row for row in rows if row.business_date.strip() == stamp]
            if not same_day:
                break

            earliest = min(_bar_time(row) for row in same_day)
            for row in same_day:
                moment = _bar_time(row)
                if not (exchange.first_bar <= moment <= exchange.last_bar):
                    continue
                seen[moment] = _stock_bar(row, business_date, moment, previous_close)

            if earliest <= exchange.first_bar:
                break
            cursor = (datetime.combine(business_date, earliest) - timedelta(minutes=1)).time()

        bars = tuple(seen[moment] for moment in sorted(seen))
        return StockBarFetch(
            stock_code=stock.value,
            exchange=exchange,
            business_date=business_date,
            bars=bars,
            call_count=calls,
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )

    def store_stock_bars(self, connection: Connection, fetch: StockBarFetch) -> int:
        """한 종목·한 거래일의 봉을 저장한다. 겹치는 봉은 갱신된다."""
        with connection.cursor() as cursor:
            cursor.execute(
                SOURCE_RECORD_INSERT,
                (
                    "api",
                    SOURCE,
                    STOCK_BARS_SOURCE_KEY,
                    fetch.started_at,
                    fetch.completed_at,
                    "succeeded",
                    len(fetch.bars),
                    None,
                    json.dumps(
                        {
                            "stock_code": fetch.stock_code,
                            "exchange": fetch.exchange.value,
                            "business_date": fetch.business_date.isoformat(),
                            "interval": "1m",
                            "call_count": fetch.call_count,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            source_record_id = cursor.fetchone()[0]
            execute_upserts(
                cursor,
                STOCK_BAR_UPSERT,
                [
                    (
                        SOURCE,
                        fetch.stock_code,
                        fetch.exchange.value,
                        bar.bar_at,
                        bar.open,
                        bar.high,
                        bar.low,
                        bar.close,
                        bar.volume,
                        bar.previous_close,
                        source_record_id,
                    )
                    for bar in fetch.bars
                ],
            )
        return len(fetch.bars)

    def store_bars(
        self,
        connection: Connection,
        responses: Sequence[KisResponse],
        since: datetime,
        failures: Sequence[SymbolOutcome] = (),
    ) -> tuple[int, tuple[SymbolOutcome, ...]]:
        """폴링 1회분을 저장하고 (저장한 봉 수, 심볼별 결과)를 돌려준다.

        구조는 `yahoo.store_bars`와 같다. 폴링 1회가 `source_record` 1건이고, 원본은 남기지
        않으며, 봉이 0건이어도 계보 레코드는 남긴다. 다른 점은 월물 코드를 함께 저장한다는 것뿐이다.
        """
        started_at = min((response.started_at for response in responses), default=datetime.now(UTC))
        completed_at = max((response.completed_at for response in responses), default=started_at)

        parsed: list[tuple[KisResponse, tuple[QuoteBar, ...]]] = []
        outcomes: list[SymbolOutcome] = list(failures)
        for response in responses:
            try:
                result = parse_bars(response.body, since=since)
            except (KisPayloadError, KisResultError) as error:
                outcomes.append(
                    SymbolOutcome(
                        symbol=response.symbol,
                        contract_code=response.contract_code,
                        status=response.status,
                        error=str(error),
                    )
                )
                continue

            parsed.append((response, result.bars))
            outcomes.append(
                SymbolOutcome(
                    symbol=response.symbol,
                    contract_code=response.contract_code,
                    contract_name=result.contract_name,
                    status=response.status,
                    bar_count=len(result.bars),
                    latest_bar_at=result.latest_bar_at.isoformat() if result.latest_bar_at else None,
                )
            )

        bar_count = sum(len(bars) for _, bars in parsed)
        metadata = json.dumps(
            {
                "interval": "1m",
                "window_start": since.isoformat(),
                "symbols": [outcome.model_dump() for outcome in outcomes],
            },
            ensure_ascii=False,
        )

        with connection.cursor() as cursor:
            cursor.execute(
                SOURCE_RECORD_INSERT,
                (
                    "api",
                    SOURCE,
                    SOURCE_KEY,
                    started_at,
                    completed_at,
                    "succeeded" if parsed else "failed",
                    bar_count,
                    # 원본은 남기지 않는다. 폴링마다 쌓으면 계보 테이블이 수집보다 빨리 커진다.
                    None,
                    metadata,
                ),
            )
            source_record_id = cursor.fetchone()[0]
            # 봉마다 왕복하지 않고 묶어 보낸다. 폴링 1회가 계약·지수 합쳐 수백 행이다.
            # 선물(월물 있음)과 지수는 저장 테이블이 다르다.
            execute_upserts(
                cursor,
                INDEX_FUTURE_BAR_UPSERT,
                [
                    (
                        SOURCE,
                        response.symbol,
                        bar.bar_at,
                        bar.open,
                        bar.high,
                        bar.low,
                        bar.close,
                        bar.volume,
                        bar.previous_close,
                        response.contract_code,
                        source_record_id,
                    )
                    for response, bars in parsed
                    if response.contract_code is not None
                    for bar in bars
                ],
            )
            execute_upserts(
                cursor,
                INDEX_BAR_UPSERT,
                [
                    (
                        SOURCE,
                        response.symbol,
                        bar.bar_at,
                        bar.open,
                        bar.high,
                        bar.low,
                        bar.close,
                        bar.volume,
                        bar.previous_close,
                        source_record_id,
                    )
                    for response, bars in parsed
                    if response.contract_code is None
                    for bar in bars
                ],
            )
        return bar_count, tuple(outcomes)

    def store_market_movement(
        self,
        connection: Connection,
        responses: Sequence[KisResponse],
        observed_at: datetime,
        failures: Sequence[SymbolOutcome] = (),
    ) -> tuple[int, tuple[SymbolOutcome, ...]]:
        """분포 조회 1회분을 저장하고 (저장한 행 수, 지수별 결과)를 돌려준다.

        **장 밖의 all-zero 응답은 행을 만들지 않는다.** 계보 레코드는 남겨서 "조회했지만 장이
        닫혀 있었다"와 "아직 조회하지 않았다"를 구분한다.

        한 지수가 실패해도 다른 지수는 저장한다. 판정은 `source_record.metadata`에 남는다.
        """
        started_at = min((response.started_at for response in responses), default=observed_at)
        completed_at = max((response.completed_at for response in responses), default=started_at)

        movements: list[MarketMovement] = []
        outcomes: list[SymbolOutcome] = list(failures)
        closed: list[str] = []
        for response in responses:
            try:
                movement = parse_market_movement(response, observed_at)
            except (KisPayloadError, KisResultError) as error:
                outcomes.append(SymbolOutcome(symbol=response.symbol, status=response.status, error=str(error)))
                continue

            if movement.closed:
                closed.append(response.symbol)
            else:
                movements.append(movement)
            outcomes.append(
                SymbolOutcome(
                    symbol=response.symbol,
                    status=response.status,
                    bar_count=0 if movement.closed else 1,
                )
            )

        metadata = json.dumps(
            {
                "observed_at": observed_at.isoformat(),
                # 장 밖이라 저장하지 않은 지수. 실패와 구분해서 남긴다.
                "closed_symbols": closed,
                "symbols": [outcome.model_dump() for outcome in outcomes],
            },
            ensure_ascii=False,
        )

        with connection.cursor() as cursor:
            cursor.execute(
                SOURCE_RECORD_INSERT,
                (
                    "api",
                    SOURCE,
                    INDEX_PRICE_SOURCE_KEY,
                    started_at,
                    completed_at,
                    "succeeded" if len(outcomes) > len(failures) else "failed",
                    len(movements),
                    # 원본은 남기지 않는다. 5분마다 도는 조회라 계보가 분포보다 빨리 커진다.
                    None,
                    metadata,
                ),
            )
            source_record_id = cursor.fetchone()[0]
            execute_upserts(
                cursor,
                MARKET_MOVEMENT_UPSERT,
                [
                    (
                        movement.symbol,
                        movement.observed_at,
                        movement.upper_limit_count,
                        movement.rising_count,
                        movement.unchanged_count,
                        movement.falling_count,
                        movement.lower_limit_count,
                        source_record_id,
                    )
                    for movement in movements
                ],
            )
        return len(movements), tuple(outcomes)

    def fetch_index_daily(
        self,
        index: DomesticIndex,
        start_date: date,
        end_date: date,
        *,
        sleep: float = INDEX_DAILY_PAGE_DELAY_SECONDS,
    ) -> DailyIndexFetch:
        """한 지수의 확정 일봉을 구간으로 받는다.

        페이지 이어받기는 두 행태를 다 다룬다(문서 4.1절). 응답 헤더 `tr_cont`가 `M`/`F`면
        같은 구간을 `N`으로 다시 묻고, 헤더 없이 **구간의 시작에 못 닿은** 응답이 오면 가장
        오래된 날짜 하루 전으로 창을 옮긴다. 마지막 장까지 받고도 남았으면 부분 저장 대신
        실패시킨다 — 잘린 구간은 지표 계산 창에 구멍을 남긴다.

        **잘림 판정에 행 수를 세지 않는다.** 확정 수급 일별 API처럼 KIS는 연속조회 표식
        없이 응답을 자르는데, 한 장의 상한은 문서에 없고 제공처가 바꿔도 알려 주지 않는다.
        그 상한을 상수로 들고 있다가 실제보다 크게 적어 두면 잘린 응답을 "구간을 다 줬다"로
        읽는다(2026-08-24: 100봉으로 가정, 실제 50봉, 지수 일봉이 50봉에 묶임). 요청한
        구간의 시작에 닿았는지로 판정하면 상한을 몰라도 된다. 이력이 구간보다 짧은
        심볼에서 빈 응답 한 번을 더 받는 것이 그 대가다.
        """
        started_at = datetime.now(UTC)
        seen: dict[date, DailyIndexBar] = {}
        window_end = end_date
        tr_cont = ""
        page_count = 0

        for page_count in range(1, INDEX_DAILY_MAX_PAGES + 1):
            body, _, headers = send_get(
                self._token,
                self._app_key,
                self._app_secret,
                INDEX_DAILY_PATH,
                INDEX_DAILY_TR_ID,
                {
                    "FID_COND_MRKT_DIV_CODE": "U",  # U = 업종. 분봉 조회와 같은 구분이다
                    "FID_INPUT_ISCD": index.index_code,
                    "FID_INPUT_DATE_1": start_date.strftime("%Y%m%d"),
                    "FID_INPUT_DATE_2": window_end.strftime("%Y%m%d"),
                    "FID_PERIOD_DIV_CODE": "D",
                },
                tr_cont,
            )
            rows = _daily_index_rows(body)
            for row in rows:
                bar = _daily_index_bar(row)
                if not start_date <= bar.business_date <= end_date:
                    raise KisPayloadError(
                        f"KIS gave a bar outside the requested span: {bar.business_date} for {index.value}"
                    )
                if bar.business_date in seen:
                    raise KisPayloadError(f"KIS gave a duplicate date {bar.business_date} for {index.value}")
                seen[bar.business_date] = bar

            if headers.get("tr_cont", "") in INDEX_DAILY_CONTINUE_FLAGS:
                tr_cont = "N"
                wait_seconds(sleep)
                continue
            if not rows:
                break
            oldest = min(seen)
            if oldest <= start_date:
                break
            # 구간의 시작에 못 닿았는데 이어받기 표식이 없다 — 조용히 잘린 응답이다.
            # 창을 뒤로 옮긴다. 요청 구간의 시작은 그대로 두므로 제공처가 거기서 멈춰 준다.
            window_end = oldest - timedelta(days=1)
            tr_cont = ""
            wait_seconds(sleep)
        else:
            raise KisPayloadError(f"KIS still had more to give after {INDEX_DAILY_MAX_PAGES} pages for {index.value}")

        if not seen:
            raise KisPayloadError(f"KIS returned no daily bars for {index.value} between {start_date} and {end_date}")

        return DailyIndexFetch(
            symbol=index.value,
            start_date=start_date,
            end_date=end_date,
            bars=tuple(seen[day] for day in sorted(seen)),
            page_count=page_count,
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )

    def store_index_daily(self, connection: Connection, fetch: DailyIndexFetch) -> int:
        """한 지수·한 구간의 일봉을 저장한다. 겹치는 날짜는 확정값으로 갱신된다."""
        with connection.cursor() as cursor:
            cursor.execute(
                SOURCE_RECORD_INSERT,
                (
                    "api",
                    SOURCE,
                    INDEX_DAILY_SOURCE_KEY,
                    fetch.started_at,
                    fetch.completed_at,
                    "succeeded",
                    len(fetch.bars),
                    # 원본은 남기지 않는다. 어느 구간을 몇 장으로 받았는지면 재현에 충분하다.
                    None,
                    json.dumps(
                        {
                            "symbol": fetch.symbol,
                            "start_date": fetch.start_date.isoformat(),
                            "end_date": fetch.end_date.isoformat(),
                            "page_count": fetch.page_count,
                            "bar_count": len(fetch.bars),
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            source_record_id = cursor.fetchone()[0]
            execute_upserts(
                cursor,
                INDEX_DAILY_UPSERT,
                [
                    (
                        SOURCE,
                        fetch.symbol,
                        bar.business_date,
                        bar.open,
                        bar.high,
                        bar.low,
                        bar.close,
                        bar.volume,
                        source_record_id,
                    )
                    for bar in fetch.bars
                ],
            )
        return len(fetch.bars)
