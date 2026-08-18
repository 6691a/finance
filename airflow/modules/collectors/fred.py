"""FRED에서 미국 국채 금리(Treasury constant maturity)를 수집한다.

배포 Airflow와 공유되는 폴더는 `airflow/dags`와 `airflow/modules` 둘뿐이다. Airflow는
`apps/`도 `core/`도 보지 못한다. 그래서 DAG가 실행 시점에 필요한 코드는 전부 여기 있어야
한다. `dags/`에는 스케줄과 오케스트레이션만 두고 수집 규칙은 이 모듈에 둔다.

의존성은 표준 라이브러리와 Pydantic, PEP 249 연결로 제한한다. SQLAlchemy 모델과
`core.config`는 import하지 않는다. Airflow 환경에는 백엔드의 런타임 의존성이 없다.

대신 백엔드와 겹치는 규칙은 백엔드를 따른다. 외부 응답은 Pydantic 모델로 검증한 뒤에만
정규화하고, 시각은 timezone-aware UTC이며, 주석은 한국어로 쓴다. 저장 대상 테이블의 정의는
백엔드의 `apps/models`가 원본이고, 여기 SQL의 컬럼 이름은 `tests/collectors/test_fred.py`가
그 모델 metadata와 대조한다.

원본 응답은 `source_record`에, 유효 관측값은 `indicator_observation`에 저장한다. 두 쓰기는
호출자가 연 하나의 트랜잭션 안에서 실행되며 커밋과 롤백은 호출자가 결정한다.
"""

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol, Self
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

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

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
SOURCE = "fred"


class FredSeries(StrEnum):
    """수집 대상. 저장 식별자, FRED 좌표, 단위, 종류, 라벨을 한 줄에 묶는다.

    **단위를 계열마다 단다.** 초판은 `SERIES_UNIT = "Percent"` 상수 하나였다. 국채만 받을 때는
    맞았지만 물가지수와 소매판매가 들어오면서 거짓이 됐다. observations 응답에는 단위가 없어서
    (`series` 엔드포인트에만 있다) 여기 선언하는 값이 유일한 근거다.

    **FRED id를 저장 식별자로 쓰지 않는 계열이 있다.** `DGS10`은 사람이 읽으니 그대로 두지만
    `CPIAUCSL`은 DB만 보고 무슨 값인지 알 수 없다. 그런 계열은 읽히는 이름을 만들어 저장하고
    FRED 좌표는 요청과 `source_record.metadata`에만 쓴다. `ecos.py`의 `MarketRateSeries`가
    항목코드를 다루는 방식과 같다.

    **월간 계열은 `M`으로 끝난다.** 한 테이블에 일별과 월간이 섞여 있어 표시가 없으면 조회하는
    쪽이 주기를 구분할 수 없다. `ecb_irs.py`가 먼저 쓴 규칙이다.
    """

    fred_id: str
    unit: str
    kind: str
    label: str

    def __new__(cls, series_id: str, fred_id: str, unit: str, kind: str, label: str) -> Self:
        member = str.__new__(cls, series_id)
        member._value_ = series_id
        member.fred_id = fred_id
        member.unit = unit
        member.kind = kind
        member.label = label
        return member

    @property
    def is_monthly(self) -> bool:
        """월간 계열인지. 저장 식별자의 `M` 접미사가 그 표시다."""
        return self.value.endswith("_M")

    # 미국 국채 수익률 곡선에서 단기·중기·장기를 대표하는 일별 시계열.
    DGS3MO = ("DGS3MO", "DGS3MO", "Percent", "government_bond", "미국 3개월물")
    DGS2 = ("DGS2", "DGS2", "Percent", "government_bond", "미국 2년물")
    DGS10 = ("DGS10", "DGS10", "Percent", "government_bond", "미국 10년물")
    DGS30 = ("DGS30", "DGS30", "Percent", "government_bond", "미국 30년물")

    # 월간 거시지표. 연준이 왜 움직이는지를 설명하는 값이고, 금리만으로는 안 보인다.
    # 단위와 기준연도는 2026-08-16에 `series` 엔드포인트로 확인했다.
    CPI_M = ("CPI_M", "CPIAUCSL", "Index 1982-1984=100", "price_index", "미국 소비자물가지수")
    PPI_M = ("PPI_M", "PPIFIS", "Index Nov 2009=100", "price_index", "미국 생산자물가지수(최종수요)")
    RETAIL_SALES_M = ("RETAIL_SALES_M", "RSAFS", "Millions of Dollars", "activity", "미국 소매판매")
    # 고용 둘은 2026-08-18에 `series` 엔드포인트로 확인했다. 레벨만 저장한다. 변화율은 계산된다.
    UNEMPLOYMENT_M = ("UNEMPLOYMENT_M", "UNRATE", "Percent", "activity", "미국 실업률")
    NONFARM_PAYROLL_M = ("NONFARM_PAYROLL_M", "PAYEMS", "Thousands of Persons", "activity", "미국 비농업고용")


# DAG이 태스크를 매핑하는 단위. 국채와 거시는 발표 주기가 달라 되돌아볼 구간이 다르고,
# 그래서 DAG도 나뉜다.
TREASURY_SERIES: tuple[str, ...] = tuple(series.value for series in FredSeries if series.kind == "government_bond")
MACRO_SERIES: tuple[str, ...] = tuple(series.value for series in FredSeries if series.is_monthly)

# FRED가 휴장일과 미발표일에 값 대신 넣는 표시.
MISSING_VALUE = "."

REQUEST_TIMEOUT_SECONDS = 30


class Cursor(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(self, *args: object) -> bool | None: ...

    def execute(self, statement: str, parameters: Sequence[Any]) -> object: ...

    def fetchone(self) -> Any: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...


class FredHTTPError(RuntimeError):
    """FRED가 2xx가 아닌 상태로 응답했다. 재시도 가능 여부는 호출자가 `status`로 판단한다."""

    def __init__(self, status: int, retry_after: str | None = None) -> None:
        super().__init__(f"FRED request failed with HTTP {status}")
        self.status = status
        self.retry_after = retry_after


class FredPayloadError(ValueError):
    """응답이 JSON이 아니거나 `observations` 계약을 지키지 않았다. 재시도해도 같은 결과다."""


class FredRequest(BaseModel):
    """한 시계열의 한 기간을 요청하는 값. 호출 전에 여기서 전부 검증한다."""

    model_config = ConfigDict(frozen=True)

    series_id: str
    observation_start: date
    observation_end: date

    @field_validator("series_id")
    @classmethod
    def require_known_series(cls, series_id: str) -> str:
        if series_id not in {series.value for series in FredSeries}:
            raise ValueError(f"Unknown FRED series: {series_id!r}")
        return series_id

    @model_validator(mode="after")
    def require_ordered_period(self) -> Self:
        if self.observation_start > self.observation_end:
            raise ValueError("observation_start must not be after observation_end")
        return self


class FredObservation(BaseModel):
    """정규화한 관측값 1건."""

    model_config = ConfigDict(frozen=True)

    observation_date: date
    value: Decimal

    @field_validator("value")
    @classmethod
    def require_finite(cls, value: Decimal) -> Decimal:
        # Decimal은 "NaN"과 "Infinity"도 받아들인다. 지표 값으로 저장하면 이후 집계가 전부 오염된다.
        if not value.is_finite():
            raise ValueError("observation value must be a finite number")
        return value


class FredRawObservation(BaseModel):
    """FRED가 보낸 관측값 1건. 값은 숫자 문자열이거나 결측 표시(`.`)다."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    observation_date: date = Field(alias="date")
    value: str


class FredObservationsPayload(BaseModel):
    """`series/observations` 응답 본문. 검증에 필요한 필드만 읽고 나머지는 버린다."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    observations: tuple[FredRawObservation, ...]

    def normalized(self) -> tuple[FredObservation, ...]:
        """결측을 뺀 관측값. 값 하나가 숫자가 아니면 전체를 실패시킨다."""
        return tuple(
            FredObservation(observation_date=observation.observation_date, value=Decimal(observation.value))
            for observation in self.observations
            if observation.value != MISSING_VALUE
        )


class FredResponse(BaseModel):
    """한 번의 FRED 호출 결과와 그 호출을 재현하는 데 필요한 메타데이터."""

    model_config = ConfigDict(frozen=True)

    request: FredRequest
    body: bytes
    status: int
    started_at: AwareDatetime
    completed_at: AwareDatetime

    @field_validator("started_at", "completed_at")
    @classmethod
    def normalize_to_utc(cls, moment: datetime) -> datetime:
        # 저장·비교용 시각은 UTC로 정규화한다. naive datetime은 AwareDatetime이 이미 막는다.
        return moment.astimezone(UTC)

    @property
    def series_id(self) -> str:
        return self.request.series_id


def build_url(api_key: SecretStr, request: FredRequest) -> str:
    """호출 URL. 반환값에 API 키가 들어가므로 로그나 예외 메시지에 넣지 않는다."""
    if not api_key.get_secret_value():
        raise ValueError("FRED API key is required")

    query = urlencode(
        {
            # 요청에는 FRED 좌표가 들어간다. 저장 식별자와 다른 계열이 있다.
            "series_id": FredSeries(request.series_id).fred_id,
            "api_key": api_key.get_secret_value(),
            "file_type": "json",
            "observation_start": request.observation_start.isoformat(),
            "observation_end": request.observation_end.isoformat(),
        }
    )
    return f"{FRED_URL}?{query}"


def fetch_series(api_key: SecretStr, request: FredRequest) -> FredResponse:
    url = build_url(api_key, request)
    started_at = datetime.now(UTC)
    try:
        with urlopen(url, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            body = response.read()
            status = response.status
    # `from None`은 여기서 의도적이다. `from error`로 바꾸지 않는다. URL에 API 키가 들어 있고
    # `HTTPError`는 그 URL을 `filename`에 담는다. 체인을 남기면 Sentry나 Airflow 로그가 원인
    # 예외를 붙잡을 때 키가 함께 실린다. hana 수집기는 URL에 비밀이 없어서 체인을 남긴다.
    except HTTPError as error:
        raise FredHTTPError(error.code, error.headers.get("Retry-After")) from None
    except URLError as error:
        # 타임아웃과 DNS·연결 실패는 재시도 가능한 오류로 올린다.
        raise ConnectionError(f"FRED request failed: {error.reason}") from None

    return FredResponse(
        request=request,
        body=body,
        status=status,
        started_at=started_at,
        completed_at=datetime.now(UTC),
    )


def parse_observations(body: bytes) -> tuple[FredObservation, ...]:
    """유효 관측값만 뽑는다. 결측(`.`)은 건너뛰고, 형식이 깨진 항목은 전체를 실패시킨다."""
    try:
        payload = FredObservationsPayload.model_validate_json(body)
        return payload.normalized()
    except (ValidationError, ArithmeticError) as error:
        raise FredPayloadError("FRED response is not a valid observations payload") from error


# 쿼리는 `sql/` 볼륨에 둔다. 배포 Airflow가 `/opt/airflow/sql`로 마운트하는 폴더다.
SOURCE_RECORD_INSERT = read_sql("postgres", "source_record", "insert.sql")
OBSERVATION_UPSERT = read_sql("postgres", "indicator_observation", "upsert.sql")


def _require_first_of_month(series: FredSeries, observation_date: date) -> None:
    """월간 계열의 관측일이 그 달 1일인지 본다.

    FRED는 월간 값을 그 달 1일로 준다(실측 2026-08-16). 달 중간 날짜가 섞이면 같은 달이 두 행이
    되고, 그 뒤로는 어느 쪽이 진짜인지 알 수 없다. `ecb_irs.py`가 같은 검사를 한다.
    """
    if series.is_monthly and observation_date.day != 1:
        raise FredPayloadError(f"{series.value} is monthly but FRED returned {observation_date.isoformat()}")


def store_observations(connection: Connection, response: FredResponse) -> int:
    """원본 1건과 유효 관측값을 저장하고 정규화한 관측값 수를 돌려준다.

    파싱을 먼저 해서 형식 오류면 아무 것도 쓰지 않는다. `(provider, series_id, observation_date)`가
    멱등 키라서 같은 기간을 다시 수집해도 행이 늘지 않고 최신 값으로 갱신된다. `series_id`는
    제공처 안에서만 고유하므로 이 수집기의 `provider`는 항상 `SOURCE`다.

    ORM 대신 문자열 SQL을 쓴다. Airflow 이미지에는 SQLAlchemy와 이 프로젝트의 DB 설정이
    없기 때문이다. 컬럼 이름은 `tests/collectors/test_fred.py`가 모델 metadata와 맞춰 둔다.
    """
    series = FredSeries(response.series_id)
    observations = parse_observations(response.body)
    request_metadata = json.dumps(
        {
            "http_status": response.status,
            "observation_start": response.request.observation_start.isoformat(),
            "observation_end": response.request.observation_end.isoformat(),
        }
    )

    with connection.cursor() as cursor:
        cursor.execute(
            SOURCE_RECORD_INSERT,
            (
                "api",
                SOURCE,
                response.series_id,
                response.started_at,
                response.completed_at,
                "succeeded",
                len(observations),
                response.body.decode("utf-8"),
                request_metadata,
            ),
        )
        source_record_id = cursor.fetchone()[0]
        for observation in observations:
            _require_first_of_month(series, observation.observation_date)
            cursor.execute(
                OBSERVATION_UPSERT,
                (
                    SOURCE,
                    response.series_id,
                    observation.observation_date,
                    observation.value,
                    series.unit,
                    source_record_id,
                ),
            )
    return len(observations)
