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
import re
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
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

KIS_BASE_URL = "https://openapi.koreainvestment.com:9443"
SOURCE = "kis"
SOURCE_KEY = "intraday_1m"

TOKEN_PATH = "/oauth2/tokenP"

# 선물옵션 분봉. 값 컬럼이 `futs_*`다.
FUTURE_CHART_PATH = "/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice"
FUTURE_CHART_TR_ID = "FHKIF03020200"

# 업종(지수) 분봉. 값 컬럼이 `bstp_nmix_*`라 선물과 다르다.
INDEX_CHART_PATH = "/uapi/domestic-stock/v1/quotations/inquire-time-indexchartprice"
INDEX_CHART_TR_ID = "FHKUP03500200"

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

    **선물과 지수는 값 컬럼 이름이 다르다.** 선물은 `futs_*`, 업종지수는 `bstp_nmix_*`다.
    둘을 모두 선택으로 받고 `price()`가 있는 쪽을 고른다. 응답마다 한쪽만 오므로 둘 다
    비어 있으면 계약이 깨진 것이다.
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

    def prices(self) -> tuple[str, str, str, str]:
        """(시가, 고가, 저가, 종가). 선물이든 지수든 채워진 쪽을 돌려준다."""
        if self.futures_close.strip():
            return (self.futures_open, self.futures_high, self.futures_low, self.futures_close)
        if self.index_close.strip():
            return (self.index_open, self.index_high, self.index_low, self.index_close)
        raise KisPayloadError("KIS bar has neither futures nor index prices")


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


def _get(token: SecretStr, app_key: SecretStr, app_secret: SecretStr, path: str, tr_id: str, query: dict) -> tuple[bytes, int]:
    """분봉 조회 한 번. 선물과 지수가 같은 헤더 규약을 쓴다."""
    request = Request(
        f"{KIS_BASE_URL}{path}?{urlencode(query)}",
        headers={
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token.get_secret_value()}",
            "appkey": app_key.get_secret_value(),
            "appsecret": app_secret.get_secret_value(),
            "tr_id": tr_id,
            "custtype": "P",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return response.read(), response.status
    except HTTPError as error:
        # KIS는 실패를 500으로 내면서 본문에 사유를 담는다. 본문에는 비밀이 없으므로 읽어서
        # 메시지로 올린다. 헤더에 있는 앱키는 예외에 실리지 않는다.
        raise KisHTTPError(error.code, _extract_message(error.read())) from None
    except URLError as error:
        raise ConnectionError(f"KIS chart request failed: {error.reason}") from None


def fetch_bars(
    token: SecretStr,
    app_key: SecretStr,
    app_secret: SecretStr,
    future: DomesticFuture,
    contract_code: str,
    until: datetime,
) -> KisResponse:
    """한 선물 계약의 1분봉을 받아 온다. 파싱은 하지 않는다.

    `until`은 조회 기준 시각이고 KIS는 **그 시각 이전 102봉**을 최신순으로 돌려준다.
    """
    reference = until.astimezone(KST)
    started_at = datetime.now(UTC)
    body, status = _get(
        token,
        app_key,
        app_secret,
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
    token: SecretStr,
    app_key: SecretStr,
    app_secret: SecretStr,
    index: DomesticIndex,
) -> KisResponse:
    """한 업종지수의 1분봉을 받아 온다.

    선물과 달리 조회 기준 시각을 받지 않는다. 이 엔드포인트의 `FID_INPUT_HOUR_1`은 기준
    시각이 아니라 **봉 간격**(60 = 1분)이다. 이름이 같아 헷갈리기 쉽다.

    `FID_ETC_CLS_CODE`는 반드시 `1`이다. `0`이면 시각이 `999999`(장마감)·`888888`(시간외)인
    의사 봉이 섞여 들어와 시각 파싱이 깨진다.
    """
    started_at = datetime.now(UTC)
    body, status = _get(
        token,
        app_key,
        app_secret,
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


# 쿼리는 `sql/` 볼륨에 둔다. 배포 Airflow가 `/opt/airflow/sql`로 마운트하는 폴더다.
SOURCE_RECORD_INSERT = read_sql("postgres", "source_record", "insert.sql")
QUOTE_BAR_UPSERT = read_sql("postgres", "quote_bar", "upsert.sql")


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


def store_bars(
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
        execute_upserts(
            cursor,
            QUOTE_BAR_UPSERT,
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
                for bar in bars
            ],
        )
    return bar_count, tuple(outcomes)
