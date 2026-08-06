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

# 미국 국채 수익률 곡선에서 단기·중기·장기를 대표하는 FRED 일별 시계열.
# 시계열을 늘리려면 여기에만 추가한다. 저장 계약은 `series_id`로 갈라지므로 코드 변경이 없다.
TREASURY_SERIES: tuple[str, ...] = ("DGS3MO", "DGS2", "DGS10", "DGS30")

# FRED는 이 시계열들을 연이율 퍼센트로 발표한다. observations 응답에는 단위가 없다.
SERIES_UNIT = "Percent"

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
        if series_id not in TREASURY_SERIES:
            raise ValueError(f"Unknown treasury series: {series_id!r}")
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
            "series_id": request.series_id,
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


def store_observations(connection: Connection, response: FredResponse) -> int:
    """원본 1건과 유효 관측값을 저장하고 정규화한 관측값 수를 돌려준다.

    파싱을 먼저 해서 형식 오류면 아무 것도 쓰지 않는다. `(series_id, observation_date)`가
    멱등 키라서 같은 기간을 다시 수집해도 행이 늘지 않고 최신 값으로 갱신된다.

    ORM 대신 문자열 SQL을 쓴다. Airflow 이미지에는 SQLAlchemy와 이 프로젝트의 DB 설정이
    없기 때문이다. 컬럼 이름은 `tests/collectors/test_fred.py`가 모델 metadata와 맞춰 둔다.
    """
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
            cursor.execute(
                OBSERVATION_UPSERT,
                (
                    response.series_id,
                    observation.observation_date,
                    observation.value,
                    SERIES_UNIT,
                    source_record_id,
                ),
            )
    return len(observations)
