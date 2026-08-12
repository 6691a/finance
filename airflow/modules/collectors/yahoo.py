"""Yahoo Finance에서 미국 지수·지수선물의 1분봉을 수집한다.

배포 Airflow와 공유되는 폴더는 `airflow/dags`와 `airflow/modules` 둘뿐이다. Airflow는
`apps/`도 `core/`도 보지 못한다. 그래서 DAG가 실행 시점에 필요한 코드는 전부 여기 있어야
한다. `dags/`에는 스케줄과 오케스트레이션만 두고 수집 규칙은 이 모듈에 둔다.

저장 대상 테이블의 정의는 백엔드의 `apps/models/market.py`가 원본이고, 여기 SQL의 컬럼
이름은 `tests/collectors/test_yahoo.py`가 그 모델 metadata와 대조한다.

`fred.py`·`ecos.py`와 목적이 다르다. 저쪽은 하루 한 값을 `indicator_observation`에 쌓는
리포트용이고, 여기는 **실시간 알림**을 위해 분 단위 봉을 `quote_bar`에 쌓는다. 한국 정규장
시간대에는 미국 현물 지수가 멈춰 있어 선물만이 살아 있는 신호이므로 수집은 24시간 돈다.
그래서 다음 네 가지가 다르다.

- **요청 1회가 봉 여러 개를 준다.** `interval=1m&range=1d`가 하루치 1분봉 배열을 통째로
  돌려준다. 그래서 5분마다 호출해도 저장 그레인은 1분이다. 폴링 주기와 데이터 주기가
  분리돼 있어 호출 수를 늘리지 않고 촘촘하게 쌓을 수 있다.
- **"새 봉 없음"이 정상 상태다.** 24시간 도는 수집이라 조용한 구간이 상시 있다. CME는
  매일 06:00~07:00 KST에 정비 휴장하고, 주말에는 토 06:00~월 07:00 KST 내내 쉰다. 미국
  현물 지수(`^SOX`, `^VIX`)는 한국 정규장 시간 내내 멈춰 있다. 그래서 `parse_bars`가
  **응답이 빈 것**과 **최근 구간에 새 봉이 없는 것**을 구분한다. 앞은 고장이고 뒤는 휴장이다.
- **폴링 1회가 `source_record` 1건이다.** 심볼마다 만들지 않는다. 5분마다 영원히 도는
  수집이라 심볼별로 남기면 계보 테이블이 수집 자체보다 빨리 커진다. CLAUDE.md가 웹소켓에
  대해 정한 "배치 단위로 남긴다"와 같은 판단이다. 원본 응답(`payload`)도 남기지 않는다.
  5분마다 5개 × 40KB면 하루 57MB다.
- **요청은 `urlopen`이 아니라 scrapling `Fetcher`다.** Yahoo가 기본 파이썬 User-Agent에
  429를 준다. `hana.py`와 같은 이유·같은 도구이고 새 의존성은 없다.

Yahoo v8 chart는 비공식 API다. 키가 없고 비용도 없지만 언제든 막힐 수 있다. 저장 계약이
`provider`로 갈라져 있어 제공처를 바꿔야 할 때 교체 범위는 이 파일 하나다.

`v7/finance/quote` 배치 엔드포인트는 401로 잠겨 있어 쓸 수 없다. 심볼마다 한 번씩 부른다.
"""

import json
import math
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Protocol, Self
from urllib.parse import quote, urlencode

from curl_cffi.curl import CurlError
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from scrapling.fetchers import Fetcher

from modules.sql import read_sql
from modules.upsert import execute_upserts

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
SOURCE = "yahoo"

# 이 수집이 남기는 `source_record.source_key`. 폴링 1회가 1건이라 심볼은 들어가지 않고
# 어떤 종류의 수집인지만 나타낸다.
SOURCE_KEY = "intraday_1m"

# 한 번에 요청하는 간격과 범위. `range`는 1m 간격이 허용하는 가장 짧은 값이고, 그래도
# 하루치(600봉 이상)가 온다. 필요한 구간은 저장할 때 잘라 낸다.
BAR_INTERVAL = "1m"
BAR_RANGE = "1d"

# 과거 구간을 직접 지정할 때 요청 한 번이 담을 수 있는 최대 일수. Yahoo가 정한 값이고
# 넘기면 "Only 8 days worth of 1m granularity data are allowed"로 거절한다.
MAX_BACKFILL_DAYS = 8

# Yahoo가 1분봉을 보관하는 기간. 이보다 과거는 요청해도 "1m data not available"이 온다.
# 상수로 두는 건 백필 요청을 미리 막기 위해서다. 실측으로 확인한 값이라 제공처가 조용히
# 바꿀 수 있고, 그때는 `parse_bars`가 Yahoo의 오류 메시지를 그대로 올려 준다.
BAR_RETENTION_DAYS = 30

# 백필 구간을 받는 DAG param 이름. 오류 메시지가 사용자에게 이 이름으로 나가야 해서
# 파싱을 하는 이 모듈이 들고 있는다. DAG는 `params` 정의에 같은 값을 쓴다.
BACKFILL_START_PARAM = "backfill_start"
BACKFILL_END_PARAM = "backfill_end"

REQUEST_TIMEOUT_SECONDS = 30

# curl_cffi가 흉내 낼 브라우저. Yahoo가 기본 파이썬 TLS·헤더 지문에 429를 준다.
IMPERSONATE = "chrome"


class QuoteSymbol(StrEnum):
    """수집 대상. 저장 식별자, Yahoo 심볼, 한국어 이름을 한 줄에 묶는다.

    Enum 값을 `quote_bar.symbol`에 그대로 저장한다. `ES=F`나 `^SOX`를 저장하면 제공처를
    바꿀 때 저장된 식별자까지 따라 바뀌어야 하고, DB만 보고 무슨 값인지 읽기도 어렵다.
    Yahoo 좌표는 `yahoo_symbol`이 들고 있다가 요청 URL에만 쓰고
    `source_record.metadata`에 남긴다. `ecos.py`의 `MarketRateSeries`와 같은 규칙이다.

    심볼을 늘리려면 여기에만 추가한다. 저장 계약은 `symbol`로 갈라지므로 그 밖의 코드
    변경이 없다.

    저장 컬럼은 `Text`로 둔다. `symbol`은 제공처마다 값 집합이 다른 열린 식별자라 DB
    `CHECK` 제약을 걸면 심볼을 늘릴 때마다 제약을 다시 만들어야 한다. 허용 값은 이 Enum이
    막는다.

    **코스피는 여기 없다.** 국내 지수는 KIS 로 받는다(`modules.collectors.kis`). 국내에서
    받을 수 있는 것은 국내를 우선하고, 실제로 Yahoo 의 `^KS11` 분봉은 일중 변동이 5~10%로
    나오는 날이 있어 신뢰할 수 없었다(문서 §8.4).

    `SOX`는 선물이 아니라 현물 지수라 한국 정규장 시간 내내 멈춰 있다. 그래서 "한국 시간
    14시에 반도체가 빠졌다"를 잡는 값은 `SOX`가 아니라 `NASDAQ100_FUT`다. 나스닥100은
    반도체 비중이 커서 실무적으로 반도체 방향의 대리 지표로 쓰인다. `SOX`는 "어젯밤 미국
    반도체가 어떻게 끝났나"의 기록으로 남긴다.
    """

    yahoo_symbol: str
    label: str

    def __new__(cls, symbol: str, yahoo_symbol: str, label: str) -> Self:
        member = str.__new__(cls, symbol)
        member._value_ = symbol
        member.yahoo_symbol = yahoo_symbol
        member.label = label
        return member

    SP500_FUT = ("SP500_FUT", "ES=F", "S&P500 선물")
    NASDAQ100_FUT = ("NASDAQ100_FUT", "NQ=F", "나스닥100 선물")
    # 다우는 풀사이즈가 상장폐지돼 `E-mini Dow $5`가 정규 계약이다(초소형은 `MYM`).
    # 가치·경기민감 쪽이 무거워 ES·NQ와 갈리는 날이 있다.
    DOW_FUT = ("DOW_FUT", "YM=F", "다우 선물")
    VIX = ("VIX", "^VIX", "VIX 변동성 지수")
    SOX = ("SOX", "^SOX", "필라델피아 반도체 지수")
    # 한국 정규장과 시간대가 겹치는 유일한 해외 현물이다. 그 구간에 살아 있는 다른 해외
    # 값은 선물뿐이라, 이 둘이 아시아 위험선호를 같은 축에서 볼 수 있게 해 준다.
    NIKKEI225 = ("NIKKEI225", "^N225", "닛케이225")
    TAIEX = ("TAIEX", "^TWII", "대만 가권지수")
    # 미 10년물 금리. FRED는 하루 한 값이라 장중 움직임을 못 본다. 값은 퍼센트 그대로다
    # (4.66 = 4.66%). **변화율이 아니라 bp로 읽는 값이다.**
    US10Y = ("US10Y", "^TNX", "미국 10년물 금리")
    # 거의 24시간 움직여 한국 장중 구간을 채운다. 하나은행 고시환율(`exchange_rate`)과는
    # 성격이 다르다. 저쪽은 은행이 하루 몇 차례 고시하는 값이고 이쪽은 장외 시장 환율이다.
    USDKRW = ("USDKRW", "KRW=X", "원/달러")
    USDJPY = ("USDJPY", "JPY=X", "엔/달러")
    DXY = ("DXY", "DX-Y.NYB", "달러인덱스")
    # 역외 위안. 시장 스트레스를 본토(CNY)보다 빨리 반영한다.
    USDCNH = ("USDCNH", "CNH=X", "위안/달러(역외)")
    JPYKRW = ("JPYKRW", "JPYKRW=X", "원/엔")
    # 중화권·소형주 지수. 한국 장중과 겹치는 아시아 심리 지표를 넓힌다.
    HSI = ("HSI", "^HSI", "항셍")
    SSE_COMP = ("SSE_COMP", "000001.SS", "상하이종합")
    RUSSELL2000 = ("RUSSELL2000", "^RUT", "러셀2000")
    # 현물 `^RUT`는 한국 정규장에 0봉이다(실측). `SOX`↔`NASDAQ100_FUT`,
    # `US10Y`↔`US10Y_FUT`와 같은 짝을 러셀에만 안 만들어 뒀던 것을 채운다.
    RUSSELL2000_FUT = ("RUSSELL2000_FUT", "RTY=F", "러셀2000 선물")
    # 미 10년 국채선물. `US10Y`(수익률)와 달리 **가격**이고 거의 24시간 거래된다.
    # 아시아 세션에 살아 있는 유일한 미 금리 신호라 둘 다 받는다.
    US10Y_FUT = ("US10Y_FUT", "ZN=F", "미 10년 국채선물")
    # 원자재 최근월물. 금은 위험회피·실질금리, 은·구리는 경기·산업수요, 유가는 인플레.
    GOLD = ("GOLD", "GC=F", "금")
    SILVER = ("SILVER", "SI=F", "은")
    COPPER = ("COPPER", "HG=F", "구리")
    WTI = ("WTI", "CL=F", "WTI 원유")
    # 반도체 공급망 참조가. 시그널 대상이 아니라 맥락용이다.
    TSMC_ADR = ("TSMC_ADR", "TSM", "TSMC ADR")
    # **주말을 채우는 유일한 값이다.** 선물도 토 06:00 KST면 멈춰서 월요일 07:00까지
    # 48시간 동안 봉이 하나도 없다(실측). 암호화폐는 그 구간에 1,628봉이 온다.
    # 주말 뉴스에 위험선호가 꺾였는지를 월요일 개장 전에 볼 수 있는 유일한 창구다.
    #
    # `range=1d`는 67봉만 주는데 데이터가 없어서가 아니라 이 심볼의 "하루" 구간이
    # 다르기 때문이다. `range=5d`는 5,828봉이 연속이다. 폴링 구간(15분)에는 지장 없고
    # 백필할 때만 이 차이를 확인한다.
    BTC = ("BTC", "BTC-USD", "비트코인")
    ETH = ("ETH", "ETH-USD", "이더리움")


# 미국 현물장 달력을 따르는 심볼. 미국 확정 휴장일에는 이것만 요청에서 뺀다.
#
# 선물·환율·원자재는 휴장일에도 거의 24시간 거래되고, 아시아 지수와 암호화폐는 미국 달력과
# 무관하다. 그래서 DAG 전체를 멈추지 않고 이 목록만 거른다. `^VIX`와 `^SOX`는 CBOE·나스닥
# 산출이지만 미국 현물장과 같은 날 쉬고, `TSM`은 뉴욕 상장 ADR이다.
US_EQUITY_SYMBOLS: frozenset[str] = frozenset(
    {
        QuoteSymbol.VIX.value,
        QuoteSymbol.SOX.value,
        QuoteSymbol.US10Y.value,
        QuoteSymbol.RUSSELL2000.value,
        QuoteSymbol.TSMC_ADR.value,
    }
)


class Cursor(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(self, *args: object) -> bool | None: ...

    def execute(self, statement: str, parameters: Sequence[Any]) -> object: ...

    def executemany(self, statement: str, parameters: Sequence[Sequence[Any]]) -> object: ...

    def fetchone(self) -> Any: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...


class YahooHTTPError(RuntimeError):
    """Yahoo가 2xx가 아닌 상태로 응답했다. 재시도 가능 여부는 호출자가 `status`로 판단한다."""

    def __init__(self, status: int) -> None:
        super().__init__(f"Yahoo request failed with HTTP {status}")
        self.status = status


class YahooPayloadError(ValueError):
    """응답이 chart 계약을 지키지 않았다. 재시도해도 같은 결과다.

    **최근 구간에 새 봉이 없는 것은 여기 해당하지 않는다.** 그건 휴장이지 고장이 아니다.
    이 예외는 응답 자체가 깨졌거나 봉 배열이 통째로 빈 경우에만 올라온다.
    """


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
    """한 심볼의 파싱 결과.

    `bars`는 요청한 구간으로 걸러진 봉이고 비어 있을 수 있다(휴장). `latest_bar_at`은
    거르기 **전** 응답 전체의 마지막 봉 시각이다. 둘을 나눠 두면 "휴장이라 조용한 것"과
    "제공처가 며칠째 안 갱신되는 것"을 나중에 구분할 수 있다. 후자는 조용한 0건으로만
    나타나서 이 값이 없으면 알아챌 방법이 없다.
    """

    model_config = ConfigDict(frozen=True)

    bars: tuple[QuoteBar, ...]
    latest_bar_at: AwareDatetime | None


class YahooQuoteArrays(BaseModel):
    """`indicators.quote[0]`. 각 배열이 `timestamp`와 같은 길이로 나란히 온다."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    open: tuple[float | None, ...]
    high: tuple[float | None, ...]
    low: tuple[float | None, ...]
    close: tuple[float | None, ...]
    volume: tuple[float | None, ...] = ()


class YahooIndicators(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    quote: tuple[YahooQuoteArrays, ...] = Field(min_length=1)


class YahooMeta(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    symbol: str
    # 직전 정규장 종가. 알림이 쓰는 변동률의 분모다. 이름이 둘인데 1분봉 응답에는
    # `chartPreviousClose`가 항상 있고 `previousClose`는 상품에 따라 빠진다.
    chart_previous_close: float = Field(alias="chartPreviousClose")


class YahooChartResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    meta: YahooMeta
    timestamp: tuple[int, ...] = ()
    indicators: YahooIndicators


class YahooChartError(BaseModel):
    """Yahoo가 요청을 거절한 이유. 과거 구간을 요청했을 때 여기로 온다."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    code: str = ""
    description: str = ""


class YahooChart(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    # 거절당하면 `result`가 **null**이고 `error`가 채워진다. 빈 배열이 아니라 null이므로
    # `| None`이 있어야 검증을 통과하고, 그래야 `parse_bars`가 Yahoo의 사유를 그대로 올린다.
    result: tuple[YahooChartResult, ...] | None = None
    error: YahooChartError | None = None


class YahooChartPayload(BaseModel):
    """chart 응답 본문. 검증에 필요한 필드만 읽고 나머지는 버린다."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    chart: YahooChart


class YahooResponse(BaseModel):
    """한 심볼의 한 번의 호출 결과와 그 호출을 재현하는 데 필요한 메타데이터."""

    model_config = ConfigDict(frozen=True)

    symbol: QuoteSymbol
    body: bytes
    status: int
    started_at: AwareDatetime
    completed_at: AwareDatetime

    @field_validator("started_at", "completed_at")
    @classmethod
    def normalize_to_utc(cls, moment: datetime) -> datetime:
        # 저장·비교용 시각은 UTC로 정규화한다. naive datetime은 AwareDatetime이 이미 막는다.
        return moment.astimezone(UTC)

    @model_validator(mode="after")
    def require_ordered_timestamps(self) -> Self:
        if self.started_at > self.completed_at:
            raise ValueError("started_at must not be after completed_at")
        return self


class SymbolOutcome(BaseModel):
    """한 심볼의 수집 결과. 성공이든 실패든 `source_record.metadata`에 그대로 실린다."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    yahoo_symbol: str
    status: int | None = None
    bar_count: int = 0
    # 제공처가 준 마지막 봉 시각. 며칠씩 안 움직이면 조용히 끊긴 것이므로 사람이 보고 안다.
    latest_bar_at: str | None = None
    error: str | None = None


def backfill_windows(start: datetime, end: datetime) -> tuple[tuple[datetime, datetime], ...]:
    """백필 구간을 Yahoo가 받아 주는 크기로 쪼갠다.

    요청 하나가 담을 수 있는 건 `MAX_BACKFILL_DAYS`일까지다. 더 넓게 요청하면 부분 응답이
    아니라 거절이 온다. 순수 함수라 테스트로 고정한다.
    """
    if start >= end:
        raise ValueError("start must be before end")

    windows: list[tuple[datetime, datetime]] = []
    span = timedelta(days=MAX_BACKFILL_DAYS)
    cursor = start
    while cursor < end:
        windows.append((cursor, min(cursor + span, end)))
        cursor += span
    return tuple(windows)


def _param_date(name: str, value: object) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as error:
        raise ValueError(f"{name} must be an ISO date (YYYY-MM-DD)") from error


def resolve_backfill_period(params: dict[str, Any]) -> tuple[datetime, datetime] | None:
    """백필 구간. 파라미터가 없으면 `None`이고 그때는 평소대로 폴링한다.

    종료일은 **포함**이다. 사용자가 준 날짜의 하루 끝까지 받는다. 저장 쪽 경계는 열려
    있으므로(`bar_at < until`) 다음 날 00:00을 상한으로 넘긴다.

    잘못된 입력은 `ValueError`로 올린다. 재시도해도 같은 값이라는 판단과 그걸
    `AirflowFailException`으로 바꾸는 일은 DAG가 한다.
    """
    start_value = params.get(BACKFILL_START_PARAM)
    end_value = params.get(BACKFILL_END_PARAM)
    if not start_value and not end_value:
        return None
    if not start_value or not end_value:
        raise ValueError(f"{BACKFILL_START_PARAM} and {BACKFILL_END_PARAM} must be given together")

    start_date = _param_date(BACKFILL_START_PARAM, start_value)
    end_date = _param_date(BACKFILL_END_PARAM, end_value)
    if start_date > end_date:
        raise ValueError(f"{BACKFILL_START_PARAM} ({start_date}) is after {BACKFILL_END_PARAM} ({end_date})")

    start = datetime.combine(start_date, time.min, tzinfo=UTC)
    end = datetime.combine(end_date, time.min, tzinfo=UTC) + timedelta(days=1)

    # Yahoo는 1분봉을 약 30일만 보관한다. 그보다 과거는 요청해도 데이터가 없으므로 미리
    # 막는다. 조용한 0건으로 끝나면 백필이 됐는지 안 됐는지 알 수 없다.
    earliest = datetime.now(UTC) - timedelta(days=BAR_RETENTION_DAYS)
    if start < earliest:
        raise ValueError(
            f"Yahoo keeps only {BAR_RETENTION_DAYS} days of 1m bars; "
            f"{BACKFILL_START_PARAM} must not be earlier than {earliest.date()}"
        )
    return start, end


def build_url(symbol: QuoteSymbol, window: tuple[datetime, datetime] | None = None) -> str:
    """호출 URL. 비밀이 없으므로 로그와 예외 메시지에 남겨도 된다.

    `window`를 주면 `range` 대신 `period1`/`period2`로 과거 구간을 지정한다. 폴링은
    `range=1d`로 "지금 기준 하루치"를 받고, 백필은 구간을 직접 찍는다.
    """
    base = YAHOO_CHART_URL.format(symbol=quote(symbol.yahoo_symbol, safe=""))
    if window is None:
        query = {"interval": BAR_INTERVAL, "range": BAR_RANGE}
    else:
        start, end = window
        query = {
            "interval": BAR_INTERVAL,
            "period1": int(start.timestamp()),
            "period2": int(end.timestamp()),
        }
    return f"{base}?{urlencode(query)}"


def fetch_bars(symbol: QuoteSymbol, window: tuple[datetime, datetime] | None = None) -> YahooResponse:
    """한 심볼의 1분봉 응답을 받아 온다. 파싱은 하지 않는다."""
    started_at = datetime.now(UTC)
    try:
        response = Fetcher.get(
            build_url(symbol, window),
            impersonate=IMPERSONATE,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except CurlError as error:
        # 타임아웃, DNS 실패, TLS 실패는 재시도 가능한 오류로 올린다. 원인을 체인으로 남긴다.
        # URL에 비밀이 없어서 `hana.py`처럼 체인을 유지한다(`fred.py`는 키가 URL에 있어 끊는다).
        raise ConnectionError(f"Yahoo request failed: {error}") from error

    if not 200 <= response.status < 300:
        raise YahooHTTPError(response.status)

    return YahooResponse(
        symbol=symbol,
        body=response.body,
        status=response.status,
        started_at=started_at,
        completed_at=datetime.now(UTC),
    )


def _decimal(value: float) -> Decimal:
    # float을 그대로 Decimal에 넣으면 이진 부동소수 오차가 그대로 실린다. 문자열을 거쳐
    # Yahoo가 보낸 십진 표기를 유지한다.
    try:
        return Decimal(repr(value))
    except (InvalidOperation, ValueError) as error:
        raise YahooPayloadError(f"Yahoo returned a non-numeric value: {value!r}") from error


def parse_bars(body: bytes, since: datetime | None = None, until: datetime | None = None) -> ParsedBars:
    """유효한 1분봉을 뽑는다.

    `since`/`until`이 저장할 구간을 자른다. 폴링은 `since`만 주고(최근 N분), 백필은 둘 다
    준다. Yahoo가 요청한 구간 밖의 봉을 함께 돌려주기 때문에 여기서 한 번 더 거른다.

    **판정을 두 단계로 나눈다.** 24시간 도는 수집이라 "새 봉이 없는" 상황이 상시 발생하므로
    그걸 고장과 구분해야 한다. 안 하면 매일 CME 정비 시간(06:00~07:00 KST)과 주말 내내,
    그리고 한국 정규장 시간의 `^SOX`마다 DAG가 실패한다.

    1. **응답 자체가 비면 실패시킨다.** `chart.result`가 없거나 `timestamp`가 통째로 비어
       있는 경우다. Yahoo가 막히거나 심볼이 폐지되면 200에 빈 배열이 온다. 성공으로 넘기면
       조회 구간에 조용히 구멍이 남는다. `ecos.py`가 `INFO-200`을 다루는 것과 같은 취지다.
    2. **`since` 필터 결과가 0건인 것은 정상이다.** 봉은 있는데 최근 구간에 없다는 뜻이고
       그건 휴장이다. 빈 `bars`를 돌려주고 예외를 올리지 않는다.

    값이 `None`이거나 `NaN`인 봉(거래가 없던 분)은 건너뛴다. FRED의 결측 표시(`.`)와 같은
    취급이다.
    """
    try:
        payload = YahooChartPayload.model_validate_json(body)
    except ValidationError as error:
        raise YahooPayloadError("Yahoo response is not a valid chart payload") from error

    if payload.chart.error is not None:
        # 보관 기간보다 과거를 요청하면 여기로 온다("1m data not available"). Yahoo가 준
        # 설명을 그대로 올려야 왜 비었는지 로그만 보고 알 수 있다.
        detail = payload.chart.error.description or payload.chart.error.code
        raise YahooPayloadError(f"Yahoo rejected the chart request: {detail}")
    if not payload.chart.result:
        raise YahooPayloadError("Yahoo response has no chart result")

    result = payload.chart.result[0]
    arrays = result.indicators.quote[0]
    previous_close = _decimal(result.meta.chart_previous_close)

    if not result.timestamp:
        # 봉이 하나도 없는 응답. 휴장이면 과거 봉이라도 실려 오므로 이건 고장이다.
        raise YahooPayloadError(f"Yahoo returned an empty chart for {result.meta.symbol}")

    # 봉은 위치로 읽는다. 배열 길이가 어긋나면 값이 조용히 옆 칸으로 밀리므로 먼저 막는다.
    # `hana.py`가 표의 칸 수를 검증하는 것과 같은 이유다. `volume`은 빠질 수 있어 뺀다.
    expected = len(result.timestamp)
    lengths = {
        "open": len(arrays.open),
        "high": len(arrays.high),
        "low": len(arrays.low),
        "close": len(arrays.close),
    }
    mismatched = {name: length for name, length in lengths.items() if length != expected}
    if mismatched:
        raise YahooPayloadError(f"Yahoo quote arrays do not line up with {expected} timestamps: {mismatched}")
    if arrays.volume and len(arrays.volume) != expected:
        raise YahooPayloadError(f"Yahoo volume array does not line up with {expected} timestamps: {len(arrays.volume)}")

    bars: list[QuoteBar] = []
    latest_bar_at: datetime | None = None
    for index, epoch in enumerate(result.timestamp):
        values = (arrays.open[index], arrays.high[index], arrays.low[index], arrays.close[index])
        if any(value is None or math.isnan(value) for value in values):
            # 거래가 없던 분. 결측이지 오류가 아니다.
            continue

        bar_at = datetime.fromtimestamp(epoch, UTC)
        if latest_bar_at is None or bar_at > latest_bar_at:
            latest_bar_at = bar_at
        if since is not None and bar_at < since:
            continue
        if until is not None and bar_at >= until:
            # 구간 끝은 열린 경계다. 백필 창이 이어질 때 경계 봉이 두 번 들어가지 않는다.
            continue

        raw_volume = arrays.volume[index] if arrays.volume else None
        try:
            bar = QuoteBar(
                bar_at=bar_at,
                open=_decimal(values[0]),
                high=_decimal(values[1]),
                low=_decimal(values[2]),
                close=_decimal(values[3]),
                volume=None if raw_volume is None or math.isnan(raw_volume) else int(raw_volume),
                previous_close=previous_close,
            )
        except ValidationError as error:
            # 무한대 같은 값이 여기서 걸린다. 호출자는 `YahooPayloadError`만 잡아 해당 심볼을
            # 실패로 넘기므로, 다른 예외로 새어 나가면 폴링 전체가 죽는다.
            raise YahooPayloadError(f"Yahoo returned an invalid bar for {result.meta.symbol}") from error
        bars.append(bar)

    if latest_bar_at is None:
        # 봉 배열은 있는데 값이 전부 결측이다. 정상 응답으로 볼 수 없다.
        raise YahooPayloadError(f"Yahoo returned no usable bars for {result.meta.symbol}")

    return ParsedBars(bars=tuple(bars), latest_bar_at=latest_bar_at)


# 쿼리는 `sql/` 볼륨에 둔다. 배포 Airflow가 `/opt/airflow/sql`로 마운트하는 폴더다.
SOURCE_RECORD_INSERT = read_sql("postgres", "source_record", "insert.sql")
QUOTE_BAR_UPSERT = read_sql("postgres", "quote_bar", "upsert.sql")


def store_bars(
    connection: Connection,
    responses: Sequence[YahooResponse],
    since: datetime,
    until: datetime | None = None,
    failures: Sequence[SymbolOutcome] = (),
) -> tuple[int, tuple[SymbolOutcome, ...]]:
    """폴링 1회분을 저장하고 (저장한 봉 수, 심볼별 결과)를 돌려준다.

    파싱을 먼저 끝내서 형식 오류면 그 심볼은 아무 것도 쓰지 않는다. 심볼 하나가 깨져도
    나머지는 저장하고 사유를 `source_record.metadata`에 남긴다. 전부 실패했는지는 호출자가
    반환값으로 판단한다.

    **봉이 0건이어도 `source_record`는 남긴다.** 조회했지만 값이 없는 구간과 아직 조회하지
    않은 구간이 구분돼야 한다. 휴장이 상시인 수집이라 이게 특히 중요하다.

    `source_record`는 **폴링 1회에 1건**이다. 심볼마다 만들면 5분 주기로 영원히 도는
    수집에서 계보 테이블이 수집 자체보다 빨리 커진다.

    ORM 대신 문자열 SQL을 쓴다. Airflow 이미지에는 SQLAlchemy와 이 프로젝트의 DB 설정이
    없기 때문이다. 컬럼 이름은 `tests/collectors/test_yahoo.py`가 모델 metadata와 맞춰 둔다.
    """
    if until is not None and since >= until:
        raise ValueError("since must be before until")

    started_at = min((response.started_at for response in responses), default=datetime.now(UTC))
    completed_at = max((response.completed_at for response in responses), default=started_at)

    parsed: list[tuple[YahooResponse, tuple[QuoteBar, ...]]] = []
    outcomes: list[SymbolOutcome] = list(failures)
    for response in responses:
        try:
            result = parse_bars(response.body, since=since, until=until)
        except YahooPayloadError as error:
            outcomes.append(
                SymbolOutcome(
                    symbol=response.symbol.value,
                    yahoo_symbol=response.symbol.yahoo_symbol,
                    status=response.status,
                    error=str(error),
                )
            )
            continue

        parsed.append((response, result.bars))
        outcomes.append(
            SymbolOutcome(
                symbol=response.symbol.value,
                yahoo_symbol=response.symbol.yahoo_symbol,
                status=response.status,
                bar_count=len(result.bars),
                latest_bar_at=result.latest_bar_at.isoformat() if result.latest_bar_at else None,
            )
        )

    bar_count = sum(len(bars) for _, bars in parsed)
    metadata = json.dumps(
        {
            "interval": BAR_INTERVAL,
            "window_start": since.isoformat(),
            "window_end": until.isoformat() if until else None,
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
                # 봉이 0건이어도 파싱에 성공했으면 성공이다. 휴장 구간이 그렇다.
                "succeeded" if parsed else "failed",
                bar_count,
                # 원본은 남기지 않는다. 5분마다 심볼당 40KB면 하루 수십 MB다.
                None,
                metadata,
            ),
        )
        source_record_id = cursor.fetchone()[0]
        # 봉마다 왕복하지 않고 묶어 보낸다. 백필은 한 번에 수만 행이라 차이가 크다.
        execute_upserts(
            cursor,
            QUOTE_BAR_UPSERT,
            [
                (
                    SOURCE,
                    response.symbol.value,
                    bar.bar_at,
                    bar.open,
                    bar.high,
                    bar.low,
                    bar.close,
                    bar.volume,
                    bar.previous_close,
                    # Yahoo 는 연속 심볼(`ES=F`)을 주므로 월물 코드가 없다. KIS 선물만 채운다.
                    None,
                    source_record_id,
                )
                for response, bars in parsed
                for bar in bars
            ],
        )
    return bar_count, tuple(outcomes)
