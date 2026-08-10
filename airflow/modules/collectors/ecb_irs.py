"""유럽중앙은행(ECB)에서 유로 회원국의 장기 국채 금리를 월별로 수집한다.

배포 Airflow와 공유되는 폴더는 `airflow/dags`와 `airflow/modules` 둘뿐이다. Airflow는
`apps/`도 `core/`도 보지 못한다. 그래서 DAG가 실행 시점에 필요한 코드는 전부 여기 있어야
한다. `dags/`에는 스케줄과 오케스트레이션만 두고 수집 규칙은 이 모듈에 둔다.

의존성은 표준 라이브러리와 Pydantic, PEP 249 연결로 제한한다. SQLAlchemy 모델과
`core.config`는 import하지 않는다. 저장 대상 테이블의 정의는 백엔드의 `apps/models`가
원본이고, 여기 SQL의 컬럼 이름은 `tests/collectors/test_ecb_irs.py`가 그 모델 metadata와
대조한다.

받는 곳은 `ecb.py`와 같은 ECB Data Portal SDMX API이고 dataflow만 `IRS`로 다르다. 제공처가
같으므로 `provider`도 `ecb`로 같고, 두 수집은 `source_record.source_key`로 갈린다.

## 왜 파일을 나눴나

같은 제공처지만 **빈도와 담는 것이 다르다.** `ecb.py`는 유로 지역 전체 AAA 곡선을 일별로
3개월~30년 받는다. 이 모듈은 회원국별 10년물 하나를 월별로 받는다. 요청 차원, 응답 헤더,
기간 표기, 스케줄이 모두 달라 한 파일에 넣으면 어느 쪽 규칙인지 읽어내기 어려워진다.

    ecb.py       유로 지역(XM) AAA 곡선   일별   3개월~30년
    bbk.py       독일(DE)                 일별   1~30년
    ecb_irs.py   프랑스·이탈리아·스페인   월별   10년만   ← 이 모듈

**독일은 여기서 받지 않는다.** 분데스방크가 같은 값을 일별로 주므로 `bbk.py`가 맡는다.
여기서 독일까지 받으면 `(country=DE, maturity_months=120)` 시계열이 둘이 되어 국가 비교
패널에 `독일` 선이 두 개 그려진다.

## 월별이라는 사실을 식별자에 남긴다

이 테이블에는 일별 시계열이 대부분이다. 월별 값을 아무 표시 없이 섞으면 대시보드에서
빈도가 다르다는 걸 알 수 없다. 그래서 `series_id` 끝에 `M`을 붙이고(`FR10YM`) `label`에도
`(월평균)`을 남긴다. 범례와 표에 그대로 나온다.

`observation_date`는 **그 달의 1일**이다. 값 자체는 한 달치 평균이므로 특정 날짜의 고시가
아니다. 조회 구간 판정도 이 1일을 기준으로 한다. 구간이 달 중간에서 시작하면 그 달은
빠진다.

## 값이 없는 구간과 잘못된 키가 갈린다

`ecb.py`와 같다. 데이터가 없으면 HTTP 200에 빈 본문이 오고, 없는 키를 물으면 HTTP 404가
온다. 앞은 아직 공표되지 않은 달이라 관측값 0건으로 저장하고 `source_record`는 남긴다.

응답 형식은 다음과 같다. `format=csvdata&detail=dataonly`가 차원과 값만 남긴 CSV를 준다.

    KEY,FREQ,REF_AREA,IR_TYPE,TR_TYPE,MATURITY_CAT,BS_COUNT_SECTOR,CURRENCY_TRANS,IR_BUS_COV,IR_FV_TYPE,TIME_PERIOD,OBS_VALUE
    IRS.M.FR.L.L40.CI.0000.EUR.N.Z,M,FR,L,L40,CI,0000,EUR,N,Z,2026-06,3.68

이 시계열은 EMU 수렴 기준(convergence criterion)에 쓰는 장기 금리다. 각국이 발행한 잔존
10년 안팎 국채의 유통수익률 월평균이며 ECB가 회원국 통계를 모아 공표한다. 공표는 다음
달 중순께라 최근 한두 달은 아직 없을 수 있다. 되돌아보는 구간이 그걸 흡수한다.

원본 응답은 `source_record`에, 유효 관측값은 `indicator_observation`에 저장한다. 두 쓰기는
호출자가 연 하나의 트랜잭션 안에서 실행되며 커밋과 롤백은 호출자가 결정한다.
"""

import csv
import io
import json
import re
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Protocol, Self
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

from modules.sql import read_sql

ECB_URL = "https://data-api.ecb.europa.eu/service/data"

# 제공처는 `ecb.py`와 같다. 두 수집은 `source_record.source_key`로 갈린다.
SOURCE = "ecb"

# SDMX dataflow. `IRS`가 EMU 수렴 기준 장기 금리다.
DATAFLOW = "IRS"

# 시계열 키에서 국가만 뺀 앞뒤. 차례로 FREQ(월별), REF_AREA, IR_TYPE(장기),
# TR_TYPE(L40: 수렴 기준 국채), MATURITY_CAT, BS_COUNT_SECTOR, CURRENCY_TRANS, IR_BUS_COV,
# IR_FV_TYPE이다. 국가 차원만 갈린다.
KEY_PREFIX = "M"
KEY_SUFFIX = "L.L40.CI.0000.EUR.N.Z"

# 수집 단위는 시계열이 아니라 조회 한 번이다. 국가 차원을 비운 SDMX 표기를 그대로 쓴다.
SOURCE_KEY = f"{DATAFLOW}.{KEY_PREFIX}..{KEY_SUFFIX}"

ENCODING = "utf-8"

USER_AGENT = "news-collector/1.0 (+https://data.ecb.europa.eu/)"

REQUEST_TIMEOUT_SECONDS = 30

# 제공처 표기. 저장 표기는 다른 수집기와 맞춘다.
SOURCE_UNIT_NAME = "PCPA"
SERIES_UNIT = "Percent"

# 이 시계열의 만기. 수렴 기준 금리는 잔존 10년 안팎 국채 하나뿐이라 상수다.
MATURITY_MONTHS = 120


class ConvergenceSeries(StrEnum):
    """수집 대상 시계열. 저장 식별자, SDMX 국가 차원, 국가 이름을 한 줄에 묶는다.

    ECB는 EU 전 회원국을 준다. 그중 유로 지역에서 시장이 늘 인용하는 큰 나라만 저장한다.
    독일은 분데스방크가 같은 값을 일별로 주므로 여기서 받지 않는다. 받으면 국가 비교
    패널에 `독일` 선이 두 개 그려진다.

    `series_id` 끝의 `M`은 월별이라는 표시다. 이 테이블은 대부분 일별이라 표시가 없으면
    대시보드에서 빈도가 다르다는 걸 알 수 없다.

    Enum 값은 `indicator_observation.series_id`에 그대로 저장한다. 나라를 늘리려면 여기와
    `reference.indicator_series` 시드를 같은 커밋에서 함께 늘린다. 관측값에서 마스터로
    외래키를 걸지 않으므로 어긋나면 DAG가 아니라 대시보드가 조용히 빈다.
    `tests/migrations/test_indicator_series_catalog.py`가 둘을 대조한다.
    """

    country: str
    country_name: str
    label: str

    def __new__(cls, series_id: str, country: str, country_name: str, label: str) -> Self:
        member = str.__new__(cls, series_id)
        member._value_ = series_id
        member.country = country
        member.country_name = country_name
        member.label = label
        return member

    FR_10Y = ("FR10YM", "FR", "프랑스", "프랑스 10년물(월평균)")
    IT_10Y = ("IT10YM", "IT", "이탈리아", "이탈리아 10년물(월평균)")
    ES_10Y = ("ES10YM", "ES", "스페인", "스페인 10년물(월평균)")

    @property
    def series_key(self) -> str:
        """SDMX 시계열 키. 응답의 `KEY` 칸 대조에 쓴다."""
        return f"{KEY_PREFIX}.{self.country}.{KEY_SUFFIX}"


CONVERGENCE_SERIES: tuple[str, ...] = tuple(series.value for series in ConvergenceSeries)

# 요청 키의 국가 차원. `+`가 SDMX의 OR다.
COUNTRIES: tuple[str, ...] = tuple(series.country for series in ConvergenceSeries)

# SDMX 국가 차원에서 저장 시계열로 되짚는 표. 응답이 그 값으로만 오기 때문에 필요하다.
SERIES_BY_COUNTRY: dict[str, ConvergenceSeries] = {series.country: series for series in ConvergenceSeries}

# `detail=dataonly`가 주는 열 구성. 열을 위치로 읽으므로 매번 대조한다. ECB가 차원을
# 추가하거나 순서를 바꾸면 값이 조용히 옆 칸으로 밀린다.
EXPECTED_HEADER: tuple[str, ...] = (
    "KEY",
    "FREQ",
    "REF_AREA",
    "IR_TYPE",
    "TR_TYPE",
    "MATURITY_CAT",
    "BS_COUNT_SECTOR",
    "CURRENCY_TRANS",
    "IR_BUS_COV",
    "IR_FV_TYPE",
    "TIME_PERIOD",
    "OBS_VALUE",
)
COLUMN_COUNT = len(EXPECTED_HEADER)

KEY_INDEX = EXPECTED_HEADER.index("KEY")
REF_AREA_INDEX = EXPECTED_HEADER.index("REF_AREA")
TIME_PERIOD_INDEX = EXPECTED_HEADER.index("TIME_PERIOD")
VALUE_INDEX = EXPECTED_HEADER.index("OBS_VALUE")

# 그 달의 값이 아직 없는 칸. 결측이지 오류가 아니다.
MISSING_VALUES = frozenset({"", "NaN"})

# `TIME_PERIOD`가 달인지 보는 모양. 월별 시계열이므로 항상 이 꼴이어야 한다.
MONTH_PATTERN = re.compile(r"^(\d{4})-(\d{2})$")


class Cursor(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(self, *args: object) -> bool | None: ...

    def execute(self, statement: str, parameters: Sequence[Any]) -> object: ...

    def fetchone(self) -> Any: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...


class EcbIrsHTTPError(RuntimeError):
    """ECB가 2xx가 아닌 상태로 응답했다.

    404는 "그런 시계열 키가 없다"는 뜻이고 아직 공표되지 않은 달과 갈린다. 값이 없는
    구간은 HTTP 200에 빈 본문으로 온다. 재시도 가능 여부는 호출자가 `status`로 판단한다.
    """

    def __init__(self, status: int, retry_after: str | None = None, url: str | None = None) -> None:
        # 인증이 없어 URL에 비밀이 없다. 그대로 남긴다.
        super().__init__(f"ECB IRS request failed with HTTP {status}: {url}")
        self.status = status
        self.retry_after = retry_after


class EcbIrsPayloadError(ValueError):
    """응답이 계약을 지키지 않았다. 재시도해도 같은 결과다."""


class EcbIrsRequest(BaseModel):
    """한 번의 수집이 저장할 관측 구간.

    구간은 날짜로 받고 요청에는 달로 바꿔 넘긴다. 다른 수집 DAG와 `modules.period`를
    함께 쓰기 위해서다.
    """

    model_config = ConfigDict(frozen=True)

    observation_start: date
    observation_end: date

    @model_validator(mode="after")
    def require_ordered_period(self) -> Self:
        if self.observation_start > self.observation_end:
            raise ValueError("observation_start must not be after observation_end")
        return self

    @property
    def start_month(self) -> str:
        return self.observation_start.strftime("%Y-%m")

    @property
    def end_month(self) -> str:
        return self.observation_end.strftime("%Y-%m")


class EcbIrsObservation(BaseModel):
    """정규화한 관측값 1건. `observation_date`는 그 달의 1일이다."""

    model_config = ConfigDict(frozen=True)

    series: ConvergenceSeries
    observation_date: date
    value: Decimal

    @field_validator("value")
    @classmethod
    def require_finite(cls, value: Decimal) -> Decimal:
        # Decimal은 "NaN"과 "Infinity"도 받아들인다. 지표 값으로 저장하면 이후 집계가 전부 오염된다.
        if not value.is_finite():
            raise ValueError("observation value must be a finite number")
        return value

    @field_validator("observation_date")
    @classmethod
    def require_first_of_month(cls, observation_date: date) -> date:
        # 월평균을 달의 1일에 못박는다. 달 중간 날짜가 섞이면 같은 달이 두 행이 된다.
        if observation_date.day != 1:
            raise ValueError("monthly observations must be keyed to the first day of the month")
        return observation_date

    @property
    def series_id(self) -> str:
        return self.series.value


class EcbIrsResult(BaseModel):
    """응답 하나를 정규화한 결과.

    관측값과 함께 응답이 실제로 덮은 구간을 들고 있다. 아직 공표되지 않은 달만 물으면
    본문이 비어 있고 그때는 세 값이 모두 비어 있다.
    """

    model_config = ConfigDict(frozen=True)

    observations: tuple[EcbIrsObservation, ...]
    response_first_date: date | None
    response_last_date: date | None
    response_row_count: int


class EcbIrsResponse(BaseModel):
    """한 번의 호출 결과와 그 호출을 재현하는 데 필요한 메타데이터."""

    model_config = ConfigDict(frozen=True)

    request: EcbIrsRequest
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
    """나라를 한꺼번에 무는 SDMX 시계열 키. 국가 차원만 `+`로 이어 붙인다."""
    return f"{KEY_PREFIX}.{'+'.join(COUNTRIES)}.{KEY_SUFFIX}"


def build_url(request: EcbIrsRequest) -> str:
    query = urlencode(
        {
            "startPeriod": request.start_month,
            "endPeriod": request.end_month,
            "format": "csvdata",
            # 제목과 각주를 빼고 차원과 값만 받는다.
            "detail": "dataonly",
        }
    )
    return f"{ECB_URL}/{DATAFLOW}/{build_series_key()}?{query}"


def parse_month(text: str) -> date:
    """`TIME_PERIOD`를 그 달의 1일로 바꾼다. `2026-06` → 2026-06-01.

    모양을 먼저 본다. `date.fromisoformat`은 `2026-06-15`도 받아들여, 빈도가 섞여 들어와도
    조용히 저장된다. 이 시계열은 월별이므로 달 표기가 아니면 실패시킨다.
    """
    matched = MONTH_PATTERN.match(text.strip())
    if matched is None:
        raise EcbIrsPayloadError(f"ECB returned a non-monthly observation period {text!r}")

    year, month = (int(part) for part in matched.groups())
    try:
        return date(year, month, 1)
    except ValueError as error:
        raise EcbIrsPayloadError(f"observation period {text!r} is not a real month") from error


def _data_rows(body: bytes) -> list[list[str]]:
    """헤더를 검증하고 데이터 행만 돌려준다.

    본문이 비어 있으면 빈 목록이다. 요청한 달이 아직 공표되지 않았을 때 ECB가 그렇게
    답한다. 오류가 아니므로 여기서 예외로 올리지 않는다.
    """
    try:
        text = body.decode(ENCODING)
    except UnicodeDecodeError as error:
        raise EcbIrsPayloadError(f"ECB response is not {ENCODING} text") from error

    if not text.strip():
        return []

    rows = [row for row in csv.reader(io.StringIO(text)) if row]
    header = tuple(cell.strip() for cell in rows[0])
    if header != EXPECTED_HEADER:
        # 차원이 늘거나 순서가 바뀌면 값이 옆 칸으로 밀린다. 이 검사가 먼저 실패해야 그걸 안다.
        raise EcbIrsPayloadError(f"ECB changed the CSV header to {header!r}")

    for row in rows[1:]:
        if len(row) != COLUMN_COUNT:
            raise EcbIrsPayloadError(f"ECB row has {len(row)} cells, expected {COLUMN_COUNT}: {row!r}")
    return rows[1:]


def parse_result(body: bytes, request: EcbIrsRequest) -> EcbIrsResult:
    """요청 구간에 드는 유효 관측값과 응답이 덮은 구간을 뽑는다.

    모르는 나라가 오거나 시계열 키가 우리가 물어본 것과 다르면 실패시킨다. 조용히 다른
    금리(단기물이나 다른 수렴 기준)가 섞여 저장되는 것보다 멈추는 편이 낫다.

    구간 판정은 그 달의 1일로 한다. 구간이 달 중간에서 시작하면 그 달은 빠진다.
    """
    observations: list[EcbIrsObservation] = []
    observation_dates: list[date] = []

    for row in _data_rows(body):
        country = row[REF_AREA_INDEX].strip()
        series = SERIES_BY_COUNTRY.get(country)
        if series is None:
            raise EcbIrsPayloadError(f"ECB returned an unrequested country {country!r}")

        expected_key = f"{DATAFLOW}.{series.series_key}"
        if row[KEY_INDEX].strip() != expected_key:
            raise EcbIrsPayloadError(f"ECB returned {row[KEY_INDEX]!r}, expected {expected_key!r}")

        observation_date = parse_month(row[TIME_PERIOD_INDEX])
        observation_dates.append(observation_date)
        if not request.observation_start <= observation_date <= request.observation_end:
            continue

        cell = row[VALUE_INDEX].strip()
        if cell in MISSING_VALUES:
            # 그 달의 값이 아직 없다. 결측이지 오류가 아니다.
            continue

        try:
            observations.append(
                EcbIrsObservation(series=series, observation_date=observation_date, value=Decimal(cell))
            )
        except (ValueError, InvalidOperation) as error:
            raise EcbIrsPayloadError(f"ECB returned a non-numeric value {cell!r} for {series.value}") from error

    return EcbIrsResult(
        observations=tuple(observations),
        response_first_date=min(observation_dates, default=None),
        response_last_date=max(observation_dates, default=None),
        response_row_count=len(observation_dates),
    )


def parse_observations(body: bytes, request: EcbIrsRequest) -> tuple[EcbIrsObservation, ...]:
    """`parse_result`의 관측값만. 다른 수집기와 이름을 맞추려고 둔다."""
    return parse_result(body, request).observations


def fetch_rates(request: EcbIrsRequest) -> EcbIrsResponse:
    """세 나라를 한 번에 받는다."""
    url = build_url(request)
    started_at = datetime.now(UTC)
    http_request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/csv"})
    try:
        with urlopen(http_request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            body = response.read()
            status = response.status
    except HTTPError as error:
        raise EcbIrsHTTPError(error.code, error.headers.get("Retry-After"), url) from error
    except URLError as error:
        # 타임아웃과 DNS·연결 실패는 재시도 가능한 오류로 올린다.
        raise ConnectionError(f"ECB IRS request failed: {error.reason}") from error

    return EcbIrsResponse(
        request=request,
        body=body,
        status=status,
        started_at=started_at,
        completed_at=datetime.now(UTC),
    )


# 쿼리는 `sql/` 볼륨에 둔다. 배포 Airflow가 `/opt/airflow/sql`로 마운트하는 폴더다.
SOURCE_RECORD_INSERT = read_sql("postgres", "source_record", "insert.sql")
OBSERVATION_UPSERT = read_sql("postgres", "indicator_observation", "upsert.sql")


def store_observations(connection: Connection, response: EcbIrsResponse) -> int:
    """조회 1건과 그 응답에서 나온 유효 관측값을 저장하고 관측값 수를 돌려준다.

    파싱을 먼저 해서 형식 오류면 아무 것도 쓰지 않는다. `(provider, series_id,
    observation_date)`가 멱등 키라서 같은 기간을 다시 수집해도 행이 늘지 않고 최신 값으로
    갱신된다. 월평균은 다음 달에 개정되는 일이 있어 이 갱신이 실제로 쓰인다.

    `provider`는 `ecb.py`와 같은 `ecb`다. `series_id`가 제공처 안에서 고유하면 되고
    `EA10Y`(일별 곡선)와 `FR10YM`(월별 회원국)은 겹치지 않는다. 두 수집은
    `source_record.source_key`로 갈린다.

    `payload`는 비운다. 원본이 CSV인데 컬럼 타입이 jsonb다. 어느 구간을 물어 어느 구간이
    돌아왔는지는 `metadata`가 남긴다.

    관측값이 0건이어도 `source_record`는 남긴다. 아직 공표되지 않은 달을 실제로 조회했다는
    사실이 없으면 아직 수집하지 않은 구간과 구분되지 않는다.

    ORM 대신 문자열 SQL을 쓴다. Airflow 이미지에는 SQLAlchemy와 이 프로젝트의 DB 설정이
    없기 때문이다. 컬럼 이름은 `tests/collectors/test_ecb_irs.py`가 모델 metadata와 맞춰 둔다.
    """
    request = response.request
    result = parse_result(response.body, request)
    request_metadata = json.dumps(
        {
            "http_status": response.status,
            "url": build_url(request),
            "series_key": build_series_key(),
            "source_unit_name": SOURCE_UNIT_NAME,
            "observation_start": request.observation_start.isoformat(),
            "observation_end": request.observation_end.isoformat(),
            # 실제로 요청한 달. 날짜 구간을 달로 바꾼 결과가 여기 드러난다.
            "start_month": request.start_month,
            "end_month": request.end_month,
            # 응답이 실제로 덮은 구간. 아직 공표되지 않은 달만 물으면 둘 다 null이다.
            "response_first_date": result.response_first_date.isoformat() if result.response_first_date else None,
            "response_last_date": result.response_last_date.isoformat() if result.response_last_date else None,
            "response_row_count": result.response_row_count,
            "countries": list(COUNTRIES),
            "series_ids": list(CONVERGENCE_SERIES),
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
                len(result.observations),
                None,
                request_metadata,
            ),
        )
        source_record_id = cursor.fetchone()[0]
        for observation in result.observations:
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
    return len(result.observations)
