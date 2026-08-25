"""유럽중앙은행(ECB)에서 유로 지역 국채 수익률 곡선을 수집한다.

배포 Airflow와 공유되는 폴더는 `airflow/dags`와 `airflow/modules` 둘뿐이다. Airflow는
`apps/`도 `core/`도 보지 못한다. 그래서 DAG가 실행 시점에 필요한 코드는 전부 여기 있어야
한다. `dags/`에는 스케줄과 오케스트레이션만 두고 수집 규칙은 이 모듈에 둔다.

의존성은 표준 라이브러리와 Pydantic, PEP 249 연결로 제한한다. SQLAlchemy 모델과
`core.config`는 import하지 않는다. 저장 대상 테이블의 정의는 백엔드의 `apps/models`가
원본이고, 여기 SQL의 컬럼 이름은 `tests/collectors/test_ecb.py`가 그 모델 metadata와
대조한다.

받는 곳은 ECB Data Portal의 SDMX REST API(`data-api.ecb.europa.eu`)다. 인증이 없고 URL에
비밀이 실리지 않으므로 `mof.py`, `boe.py`와 마찬가지로 예외 메시지와 로그에 URL을 그대로
남긴다.

## 나라가 아니라 통화권이다

이 시계열은 유로 지역(`REF_AREA=U2`) 전체의 **AAA 등급 국채 스팟 곡선** 하나다. 독일이나
프랑스 같은 개별 회원국 곡선이 아니다. ECB가 Svensson 모형으로 추정해 일별로 고시하고
`indicator_series.country`에는 통화권을 뜻하는 `XM`이 들어간다.

저장 식별자는 `EA10Y`처럼 ECB 자신의 표기(euro area)를 쓴다. `country`가 `XM`인 것과
어긋나 보이지만, `XM`은 ISO에서 통화권에 배정된 코드이고 `EA`는 사람이 읽을 표기라
쓰임이 다르다. ECB의 원본 좌표(`SR_10Y` 같은 `DATA_TYPE_FM`)는 이 모듈의 Enum이 들고
있다가 요청에 쓰고 `source_record.metadata`에 남긴다.

## 한 번 요청하면 곡선 전체가 온다

SDMX 키의 마지막 차원에 `+`를 넣어 만기를 한꺼번에 물을 수 있다. `fred`, `ecos`처럼
시계열마다 요청하지 않고 `mof`, `boe`처럼 한 응답이 곡선 전체를 담는다. 그래서
`source_record`도 시계열이 아니라 조회 단위로 한 행만 남기고 `source_key`에 SDMX 키
접두사를 넣는다.

응답 형식은 다음과 같다. `format=csvdata&detail=dataonly`가 차원과 값만 남긴 CSV를 준다.
`detail`을 빼면 제목과 각주까지 딸려 와 한 행이 1KB를 넘는다.

    KEY,FREQ,REF_AREA,CURRENCY,PROVIDER_FM,INSTRUMENT_FM,PROVIDER_FM_ID,DATA_TYPE_FM,TIME_PERIOD,OBS_VALUE
    YC.B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y,B,U2,EUR,4F,G_N_A,SV_C_YM,SR_10Y,2026-08-05,3.1466241785

## 값이 없는 구간과 잘못된 키가 갈린다

- 요청 구간에 데이터가 없으면 **HTTP 200에 빈 본문**이 온다. 헤더 줄조차 없다. 이건
  오류가 아니라 휴장이다. 관측값 0건으로 저장하고 `source_record`는 남긴다.
- 존재하지 않는 시계열 키를 물으면 **HTTP 404**에 SDMX 오류 JSON이 온다. 이건 설정
  오류라 재시도해도 같다. 판단은 DAG가 `EcbHTTPError.status`로 한다.

`boe.py`와 달리 둘이 갈리므로 조회 구간에 패딩을 붙이지 않는다.

고시 기준일은 유로 지역 영업일(TARGET 결제일)이며 제공처 기준을 그대로 보존한다.
곡선은 유럽 시간 정오 무렵에 갱신되고 최근 1~2 영업일은 아직 없을 수 있다. 되돌아보는
구간이 그걸 흡수한다. 저장하는 시각(`started_at`, `completed_at`)은 UTC다.

원본 응답은 `source_record`에, 유효 관측값은 `indicator_observation`에 저장한다. 두 쓰기는
호출자가 연 하나의 트랜잭션 안에서 실행되며 커밋과 롤백은 호출자가 결정한다.
"""

import csv
import io
import json
import re
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Self
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    field_validator,
    model_validator,
)

from modules.db import Connection
from modules.sql import read_sql

ECB_URL = "https://data-api.ecb.europa.eu/service/data"
SOURCE = "ecb"

# SDMX dataflow. `YC`가 유로 지역 수익률 곡선이다.
DATAFLOW = "YC"

# 시계열 키에서 만기를 뺀 앞부분. 차례로 FREQ(일별), REF_AREA(유로 지역),
# CURRENCY, PROVIDER_FM, INSTRUMENT_FM(국채·명목·AAA 등급), PROVIDER_FM_ID(Svensson 모형,
# 연속복리, 수익률 오차 최소화)다. 마지막 차원 DATA_TYPE_FM만 만기별로 갈린다.
KEY_PREFIX = "B.U2.EUR.4F.G_N_A.SV_C_YM"

# 수집 단위는 시계열이 아니라 조회 한 번이다. `source_record.source_key`에 이 값을 넣는다.
SOURCE_KEY = f"{DATAFLOW}.{KEY_PREFIX}"

ENCODING = "utf-8"

USER_AGENT = "news-collector/1.0 (+https://data.ecb.europa.eu/)"

REQUEST_TIMEOUT_SECONDS = 30

# 제공처 표기. `PCPA`는 percent per annum이다. 저장 표기는 fred, ecos, mof, boe와 맞춘다.
# 여러 나라 금리를 한 쿼리로 비교하려면 단위 문자열이 같아야 한다.
SOURCE_UNIT_NAME = "PCPA"
SERIES_UNIT = "Percent"


class EuroYieldSeries(StrEnum):
    """수집 대상 시계열. 저장 식별자, SDMX 만기 차원, 만기, 한국어 이름을 한 줄에 묶는다.

    ECB는 3개월부터 30년까지 스무 개 넘는 만기를 고시한다. 그중 다른 나라 곡선과 견줄 수
    있는 표준 만기만 저장한다. 나머지는 같은 Svensson 곡선 위의 보간값이라 따로 쌓아도
    비교에 쓰이지 않는다.

    Enum 값은 `indicator_observation.series_id`에 그대로 저장한다. 시계열을 늘리려면
    여기와 `reference.indicator_series` 시드를 같은 커밋에서 함께 늘린다. 관측값에서
    마스터로 외래키를 걸지 않으므로 어긋나면 DAG가 아니라 대시보드가 조용히 빈다.
    `tests/migrations/test_indicator_series_catalog.py`가 둘을 대조한다.
    """

    data_type: str
    maturity_months: int
    label: str

    def __new__(cls, series_id: str, data_type: str, maturity_months: int, label: str) -> Self:
        member = str.__new__(cls, series_id)
        member._value_ = series_id
        member.data_type = data_type
        member.maturity_months = maturity_months
        member.label = label
        return member

    EA_3M = ("EA3M", "SR_3M", 3, "유로 지역 3개월물")
    EA_6M = ("EA6M", "SR_6M", 6, "유로 지역 6개월물")
    EA_1Y = ("EA1Y", "SR_1Y", 12, "유로 지역 1년물")
    EA_2Y = ("EA2Y", "SR_2Y", 24, "유로 지역 2년물")
    EA_3Y = ("EA3Y", "SR_3Y", 36, "유로 지역 3년물")
    EA_5Y = ("EA5Y", "SR_5Y", 60, "유로 지역 5년물")
    EA_7Y = ("EA7Y", "SR_7Y", 84, "유로 지역 7년물")
    EA_10Y = ("EA10Y", "SR_10Y", 120, "유로 지역 10년물")
    EA_15Y = ("EA15Y", "SR_15Y", 180, "유로 지역 15년물")
    EA_20Y = ("EA20Y", "SR_20Y", 240, "유로 지역 20년물")
    EA_30Y = ("EA30Y", "SR_30Y", 360, "유로 지역 30년물")


EURO_YIELD_SERIES: tuple[str, ...] = tuple(series.value for series in EuroYieldSeries)

# 요청 키의 마지막 차원. `+`가 SDMX의 OR다.
DATA_TYPES: tuple[str, ...] = tuple(series.data_type for series in EuroYieldSeries)

# SDMX 만기 차원에서 저장 시계열로 되짚는 표. 응답이 그 값으로만 오기 때문에 필요하다.
SERIES_BY_DATA_TYPE: dict[str, EuroYieldSeries] = {series.data_type: series for series in EuroYieldSeries}

# `detail=dataonly`가 주는 열 구성. 열을 위치로 읽으므로 매번 대조한다. ECB가 차원을
# 추가하거나 순서를 바꾸면 값이 조용히 옆 칸으로 밀린다.
EXPECTED_HEADER: tuple[str, ...] = (
    "KEY",
    "FREQ",
    "REF_AREA",
    "CURRENCY",
    "PROVIDER_FM",
    "INSTRUMENT_FM",
    "PROVIDER_FM_ID",
    "DATA_TYPE_FM",
    "TIME_PERIOD",
    "OBS_VALUE",
)
COLUMN_COUNT = len(EXPECTED_HEADER)

KEY_INDEX = EXPECTED_HEADER.index("KEY")
DATA_TYPE_INDEX = EXPECTED_HEADER.index("DATA_TYPE_FM")
TIME_PERIOD_INDEX = EXPECTED_HEADER.index("TIME_PERIOD")
VALUE_INDEX = EXPECTED_HEADER.index("OBS_VALUE")

# 그날 그 만기의 고시가 없는 칸. 결측이지 오류가 아니다.
MISSING_VALUES = frozenset({"", "NaN"})

# `TIME_PERIOD`가 달력 하루인지 보는 모양. 일별 시계열이므로 항상 이 꼴이어야 한다.
ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
class EcbHTTPError(RuntimeError):
    """ECB가 2xx가 아닌 상태로 응답했다.

    404는 "그런 시계열 키가 없다"는 뜻이고 값이 없는 구간과 갈린다. 값이 없는 구간은
    HTTP 200에 빈 본문으로 온다. 재시도 가능 여부는 호출자가 `status`로 판단한다.
    """

    def __init__(self, status: int, retry_after: str | None = None, url: str | None = None) -> None:
        # 인증이 없어 URL에 비밀이 없다. 그대로 남긴다.
        super().__init__(f"ECB request failed with HTTP {status}: {url}")
        self.status = status
        self.retry_after = retry_after


class EcbPayloadError(ValueError):
    """응답이 계약을 지키지 않았다. 재시도해도 같은 결과다."""


class EcbRequest(BaseModel):
    """한 번의 수집이 저장할 관측 구간."""

    model_config = ConfigDict(frozen=True)

    observation_start: date
    observation_end: date

    @model_validator(mode="after")
    def require_ordered_period(self) -> Self:
        if self.observation_start > self.observation_end:
            raise ValueError("observation_start must not be after observation_end")
        return self


class EcbObservation(BaseModel):
    """정규화한 관측값 1건."""

    model_config = ConfigDict(frozen=True)

    series: EuroYieldSeries
    observation_date: date
    value: Decimal

    @field_validator("value")
    @classmethod
    def require_finite(cls, value: Decimal) -> Decimal:
        # Decimal은 "NaN"과 "Infinity"도 받아들인다. 지표 값으로 저장하면 이후 집계가 전부 오염된다.
        if not value.is_finite():
            raise ValueError("observation value must be a finite number")
        return value

    @property
    def series_id(self) -> str:
        return self.series.value


class EcbCurve(BaseModel):
    """응답 하나를 정규화한 결과.

    관측값과 함께 응답이 실제로 덮은 구간을 들고 있다. 구간 전체가 휴장이면 본문이 비어
    있고 그때는 세 값이 모두 비어 있다. 저장된 0건이 "값이 없는 구간"임을 여기서 되짚는다.
    """

    model_config = ConfigDict(frozen=True)

    observations: tuple[EcbObservation, ...]
    response_first_date: date | None
    response_last_date: date | None
    response_row_count: int


class EcbResponse(BaseModel):
    """한 번의 호출 결과와 그 호출을 재현하는 데 필요한 메타데이터."""

    model_config = ConfigDict(frozen=True)

    request: EcbRequest
    body: bytes
    status: int
    started_at: AwareDatetime
    completed_at: AwareDatetime

    @field_validator("started_at", "completed_at")
    @classmethod
    def normalize_to_utc(cls, moment: datetime) -> datetime:
        # 저장·비교용 시각은 UTC로 정규화한다. naive datetime은 AwareDatetime이 이미 막는다.
        return moment.astimezone(UTC)


def build_series_key() -> str:
    """만기를 한꺼번에 무는 SDMX 시계열 키. 마지막 차원만 `+`로 이어 붙인다."""
    return f"{KEY_PREFIX}.{'+'.join(DATA_TYPES)}"


def build_url(request: EcbRequest) -> str:
    query = urlencode(
        {
            "startPeriod": request.observation_start.isoformat(),
            "endPeriod": request.observation_end.isoformat(),
            "format": "csvdata",
            # 제목과 각주를 빼고 차원과 값만 받는다. 빼지 않으면 한 행이 1KB를 넘는다.
            "detail": "dataonly",
        }
    )
    return f"{ECB_URL}/{DATAFLOW}/{build_series_key()}?{query}"


def _data_rows(body: bytes) -> list[list[str]]:
    """헤더를 검증하고 데이터 행만 돌려준다.

    본문이 비어 있으면 빈 목록이다. 요청 구간이 전부 휴장일 때 ECB가 그렇게 답한다.
    오류가 아니므로 여기서 예외로 올리지 않는다.
    """
    try:
        text = body.decode(ENCODING)
    except UnicodeDecodeError as error:
        raise EcbPayloadError(f"ECB response is not {ENCODING} text") from error

    if not text.strip():
        return []

    rows = [row for row in csv.reader(io.StringIO(text)) if row]
    header = tuple(cell.strip() for cell in rows[0])
    if header != EXPECTED_HEADER:
        # 차원이 늘거나 순서가 바뀌면 값이 옆 칸으로 밀린다. 이 검사가 먼저 실패해야 그걸 안다.
        raise EcbPayloadError(f"ECB changed the CSV header to {header!r}")

    for row in rows[1:]:
        if len(row) != COLUMN_COUNT:
            raise EcbPayloadError(f"ECB row has {len(row)} cells, expected {COLUMN_COUNT}: {row!r}")
    return rows[1:]


def parse_observation_date(text: str) -> date:
    """`TIME_PERIOD`를 날짜로 바꾼다. 일별 시계열이므로 항상 `YYYY-MM-DD`다.

    모양을 먼저 본 뒤 넘긴다. `date.fromisoformat`은 `2026-W32` 같은 ISO 주 표기도 받아
    그 주의 월요일로 바꾼다. 주간이나 월간 빈도의 값이 섞여 들어오면 조용히 엉뚱한 날짜로
    저장되므로 여기서 막는다.
    """
    stripped = text.strip()
    if not ISO_DATE_PATTERN.match(stripped):
        raise EcbPayloadError(f"ECB returned a non-ISO calendar-day observation date {text!r}")

    try:
        return date.fromisoformat(stripped)
    except ValueError as error:
        raise EcbPayloadError(f"ECB returned a non-ISO calendar-day observation date {text!r}") from error


def parse_curve(body: bytes, request: EcbRequest) -> EcbCurve:
    """요청 구간에 드는 유효 관측값과 응답이 덮은 구간을 뽑는다.

    모르는 만기 차원이 오거나 시계열 키가 우리가 물어본 것과 다르면 실패시킨다. 조용히
    다른 곡선(등급이 다른 곡선 등)의 값이 섞여 저장되는 것보다 멈추는 편이 낫다.
    """
    observations: list[EcbObservation] = []
    observation_dates: list[date] = []

    for row in _data_rows(body):
        data_type = row[DATA_TYPE_INDEX].strip()
        series = SERIES_BY_DATA_TYPE.get(data_type)
        if series is None:
            raise EcbPayloadError(f"ECB returned an unrequested maturity {data_type!r}")

        expected_key = f"{SOURCE_KEY}.{data_type}"
        if row[KEY_INDEX].strip() != expected_key:
            raise EcbPayloadError(f"ECB returned {row[KEY_INDEX]!r}, expected {expected_key!r}")

        observation_date = parse_observation_date(row[TIME_PERIOD_INDEX])
        observation_dates.append(observation_date)
        if not request.observation_start <= observation_date <= request.observation_end:
            continue

        cell = row[VALUE_INDEX].strip()
        if cell in MISSING_VALUES:
            # 그날 그 만기의 고시가 없다. 결측이지 오류가 아니다.
            continue

        try:
            observations.append(EcbObservation(series=series, observation_date=observation_date, value=Decimal(cell)))
        except (ValueError, InvalidOperation) as error:
            raise EcbPayloadError(f"ECB returned a non-numeric value {cell!r} for {series.value}") from error

    return EcbCurve(
        observations=tuple(observations),
        response_first_date=min(observation_dates, default=None),
        response_last_date=max(observation_dates, default=None),
        response_row_count=len(observation_dates),
    )


def parse_observations(body: bytes, request: EcbRequest) -> tuple[EcbObservation, ...]:
    """`parse_curve`의 관측값만. fred, ecos, mof, boe와 이름을 맞추려고 둔다."""
    return parse_curve(body, request).observations


def fetch_curve(request: EcbRequest) -> EcbResponse:
    """곡선 전체를 한 번에 받는다."""
    url = build_url(request)
    started_at = datetime.now(UTC)
    http_request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/csv"})
    try:
        with urlopen(http_request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            body = response.read()
            status = response.status
    except HTTPError as error:
        raise EcbHTTPError(error.code, error.headers.get("Retry-After"), url) from error
    except URLError as error:
        # 타임아웃과 DNS·연결 실패는 재시도 가능한 오류로 올린다.
        raise ConnectionError(f"ECB request failed: {error.reason}") from error

    return EcbResponse(
        request=request,
        body=body,
        status=status,
        started_at=started_at,
        completed_at=datetime.now(UTC),
    )


# 쿼리는 `sql/` 볼륨에 둔다. 배포 Airflow가 `/opt/airflow/sql`로 마운트하는 폴더다.
SOURCE_RECORD_INSERT = read_sql("postgres", "source_record", "insert.sql")
OBSERVATION_UPSERT = read_sql("postgres", "indicator_observation", "upsert.sql")


def store_observations(connection: Connection, response: EcbResponse) -> int:
    """조회 1건과 그 응답에서 나온 유효 관측값을 저장하고 관측값 수를 돌려준다.

    파싱을 먼저 해서 형식 오류면 아무 것도 쓰지 않는다. `(provider, series_id,
    observation_date)`가 멱등 키라서 같은 기간을 다시 수집해도 행이 늘지 않고 최신 값으로
    갱신된다. `series_id`는 제공처 안에서만 고유하므로 이 수집기의 `provider`는 항상 `SOURCE`다.

    `source_record`는 조회 한 번을 수집 단위 1건으로 남긴다. 그래서 `source_key`가 시계열
    ID가 아니라 SDMX 키 접두사이고, 모든 만기의 관측값이 그 한 행을 함께 가리킨다.

    `payload`는 비운다. 원본이 CSV인데 컬럼 타입이 jsonb다. 어느 구간을 물어 어느 구간이
    돌아왔는지는 `metadata`가 남긴다.

    관측값이 0건이어도 `source_record`는 남긴다. 휴장 구간을 실제로 조회했다는 사실이
    없으면 아직 수집하지 않은 구간과 구분되지 않는다.

    ORM 대신 문자열 SQL을 쓴다. Airflow 이미지에는 SQLAlchemy와 이 프로젝트의 DB 설정이
    없기 때문이다. 컬럼 이름은 `tests/collectors/test_ecb.py`가 모델 metadata와 맞춰 둔다.
    """
    request = response.request
    curve = parse_curve(response.body, request)
    request_metadata = json.dumps(
        {
            "http_status": response.status,
            "url": build_url(request),
            "series_key": build_series_key(),
            "source_unit_name": SOURCE_UNIT_NAME,
            "observation_start": request.observation_start.isoformat(),
            "observation_end": request.observation_end.isoformat(),
            # 응답이 실제로 덮은 구간. 구간 전체가 휴장이면 둘 다 null이다.
            "response_first_date": curve.response_first_date.isoformat() if curve.response_first_date else None,
            "response_last_date": curve.response_last_date.isoformat() if curve.response_last_date else None,
            "response_row_count": curve.response_row_count,
            "data_types": list(DATA_TYPES),
            "series_ids": list(EURO_YIELD_SERIES),
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
                response.started_at,
                response.completed_at,
                "succeeded",
                len(curve.observations),
                None,
                request_metadata,
            ),
        )
        source_record_id = cursor.fetchone()[0]
        for observation in curve.observations:
            cursor.execute(
                OBSERVATION_UPSERT,
                (
                    SOURCE,
                    observation.series_id,
                    observation.observation_date,
                    observation.value,
                    SERIES_UNIT,
                    source_record_id,
                ),
            )
    return len(curve.observations)
