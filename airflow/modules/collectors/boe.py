"""잉글랜드은행(BoE)에서 영국 국채 금리(gilt)를 수집한다.

배포 Airflow와 공유되는 폴더는 `airflow/dags`와 `airflow/modules` 둘뿐이다. Airflow는
`apps/`도 `core/`도 보지 못한다. 그래서 DAG가 실행 시점에 필요한 코드는 전부 여기 있어야
한다. `dags/`에는 스케줄과 오케스트레이션만 두고 수집 규칙은 이 모듈에 둔다.

의존성은 표준 라이브러리와 Pydantic, PEP 249 연결로 제한한다. SQLAlchemy 모델과
`core.config`는 import하지 않는다. 저장 대상 테이블의 정의는 백엔드의 `apps/models`가
원본이고, 여기 SQL의 컬럼 이름은 `tests/collectors/test_boe.py`가 그 모델 metadata와
대조한다.

받는 곳은 BoE의 IADB(Interactive Database) CSV 내보내기다. 인증이 없고 URL에 비밀이
실리지 않으므로 `mof.py`와 마찬가지로 예외 메시지와 로그에 URL을 그대로 남긴다.
대신 User-Agent를 명시한다. 기본 `Python-urllib/3.x`로 요청하면 `Access Denied`가 온다.

## 만기가 세 개뿐이다

IADB의 `British Government Securities / Nominal par yields` 노드가 고시하는 일별 만기는
5년·10년·20년 셋뿐이다(`IUDSNPY`, `IUDMNPY`, `IUDLNPY`). 제로쿠폰 노드도 같은 셋이다.
0.5~40년 전 구간을 담은 일별 수익률 곡선은 BoE가 따로 내지만 형식이 xlsx라 이 이미지의
의존성으로는 읽을 수 없다. 그래서 여기서는 par yield 세 만기만 받는다.

한 번 요청하면 세 시계열이 함께 온다. `fred`, `ecos`처럼 시계열마다 요청하지 않고
`mof`처럼 한 응답이 곡선 전체를 담는다. 그래서 `source_record`도 시계열이 아니라 조회
단위로 한 행만 남기고 `source_key`에 `SOURCE_KEY`를 넣는다.

## 값이 없는 구간과 잘못된 코드를 구분할 수 없다

IADB는 요청 구간에 데이터가 한 행도 없으면 CSV가 아니라 **HTTP 200으로 HTML 오류
페이지**를 돌려준다. 존재하지 않는 시계열 코드를 물었을 때도 같은 페이지가 온다. 응답만
보고는 둘을 가를 수 없다.

그래서 조회 구간보다 `FETCH_PADDING_DAYS`만큼 앞에서부터 받는다. 주말이나 영국 공휴일만
걸린 구간이라도 넉넉히 앞을 붙이면 영업일이 반드시 들어가고, 응답은 CSV가 된다. 구간
밖의 행은 저장 전에 버리므로 저장 결과는 달라지지 않는다. 패딩까지 붙였는데도 오류
페이지가 오면 그건 시계열 코드나 구간 자체가 틀린 것이라 실패시킨다.

응답 형식은 다음과 같다. 인코딩은 UTF-8이고 첫 줄이 헤더다. 값이 없는 날은 행 자체가
없다.

    DATE,SERIES,VALUE
    03 Aug 2026,IUDSNPY,4.4656
    04 Aug 2026,IUDSNPY,4.4217
    03 Aug 2026,IUDMNPY,4.9382

날짜는 `03 Aug 2026` 꼴이다. 달 이름을 `strptime`에 맡기지 않고 표를 직접 둔다.
`%b`는 실행 환경의 `LC_TIME`을 타므로 컨테이너 로케일이 바뀌면 조용히 실패한다.
기준일은 영국 영업일이며 제공처 기준을 그대로 보존한다. 저장하는 시각(`started_at`,
`completed_at`)은 UTC다.

원본 응답은 `source_record`에, 유효 관측값은 `indicator_observation`에 저장한다. 두 쓰기는
호출자가 연 하나의 트랜잭션 안에서 실행되며 커밋과 롤백은 호출자가 결정한다.
"""

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
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

BOE_URL = "https://www.bankofengland.co.uk/boeapps/iadb/fromshowcolumns.asp"
SOURCE = "boe"

# 수집 단위는 시계열이 아니라 조회 한 번이다. `source_record.source_key`에 이 값을 넣는다.
# IADB에서 이 세 시계열이 놓인 노드 이름이다.
SOURCE_KEY = "gilt_nominal_par_yields"

ENCODING = "utf-8"

# 기본 User-Agent(`Python-urllib/3.x`)로 요청하면 `Access Denied`가 온다. 인증이 아니므로
# 값 자체에 의미는 없고 막히지만 않으면 된다.
USER_AGENT = "news-collector/1.0 (+https://www.bankofengland.co.uk/boeapps/database/)"

REQUEST_TIMEOUT_SECONDS = 30

# 제공처 표기. 저장 표기는 fred, ecos, mof와 맞춘다. 여러 나라 금리를 한 쿼리로 비교하려면
# 단위 문자열이 같아야 한다.
SOURCE_UNIT_NAME = "%"
SERIES_UNIT = "Percent"

# 조회 구간 앞에 붙여 받는 일수. 값이 없는 구간과 잘못된 시계열 코드를 응답만으로 가를 수
# 없기 때문에, 영업일이 반드시 들어가도록 넉넉히 앞에서부터 받는다. 크리스마스 연휴가
# 가장 긴 휴장이고 그보다 넉넉하다. 구간 밖의 행은 저장 전에 버린다.
FETCH_PADDING_DAYS = 14


class GiltSeries(StrEnum):
    """수집 대상 시계열. 저장 식별자, IADB 코드, 만기, 한국어 이름을 한 줄에 묶는다.

    IADB의 명목 par yield 노드가 일별로 고시하는 만기는 이 셋뿐이다. `S`, `M`, `L`은
    short(5년), medium(10년), long(20년)이다. 코드만 보고는 만기를 알 수 없어 여기서
    사람이 읽을 수 있는 ID로 바꿔 저장한다.

    Enum 값은 `indicator_observation.series_id`에 그대로 저장한다. 시계열을 늘리려면
    여기와 `reference.indicator_series` 시드를 같은 커밋에서 함께 늘린다. 관측값에서
    마스터로 외래키를 걸지 않으므로 어긋나면 DAG가 아니라 대시보드가 조용히 빈다.
    `tests/migrations/test_indicator_series_catalog.py`가 둘을 대조한다.
    """

    boe_code: str
    maturity_months: int
    label: str

    def __new__(cls, series_id: str, boe_code: str, maturity_months: int, label: str) -> Self:
        member = str.__new__(cls, series_id)
        member._value_ = series_id
        member.boe_code = boe_code
        member.maturity_months = maturity_months
        member.label = label
        return member

    GILT_5Y = ("GILT5Y", "IUDSNPY", 60, "영국 5년물")
    GILT_10Y = ("GILT10Y", "IUDMNPY", 120, "영국 10년물")
    GILT_20Y = ("GILT20Y", "IUDLNPY", 240, "영국 20년물")


GILT_SERIES: tuple[str, ...] = tuple(series.value for series in GiltSeries)

# 요청에 넣는 IADB 코드. 요청 순서가 응답 순서를 정한다.
SERIES_CODES: tuple[str, ...] = tuple(series.boe_code for series in GiltSeries)

# IADB 코드에서 저장 시계열로 되짚는 표. 응답이 코드로만 오기 때문에 필요하다.
SERIES_BY_CODE: dict[str, GiltSeries] = {series.boe_code: series for series in GiltSeries}

# `CSVF=CN`이 주는 세로 형식의 헤더. 열을 위치로 읽으므로 매번 대조한다. BoE가 열을
# 추가하거나 순서를 바꾸면 값이 조용히 옆 칸으로 밀린다.
EXPECTED_HEADER: tuple[str, ...] = ("DATE", "SERIES", "VALUE")
COLUMN_COUNT = len(EXPECTED_HEADER)

DATE_INDEX, SERIES_INDEX, VALUE_INDEX = range(COLUMN_COUNT)

# 응답과 요청 모두 영어 달 이름을 쓴다. `strptime`의 `%b`는 실행 환경의 `LC_TIME`을 타므로
# 컨테이너 로케일이 바뀌면 조용히 실패한다. 표를 직접 둔다.
MONTH_NAMES: tuple[str, ...] = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
MONTH_NUMBERS: dict[str, int] = {name: number for number, name in enumerate(MONTH_NAMES, start=1)}

# 그날 그 만기의 고시가 없는 칸. 결측이지 오류가 아니다.
MISSING_VALUES = frozenset({"", "-", "ND", "n/a"})


class Cursor(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(self, *args: object) -> bool | None: ...

    def execute(self, statement: str, parameters: Sequence[Any]) -> object: ...

    def fetchone(self) -> Any: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...


class BoeHTTPError(RuntimeError):
    """IADB가 2xx가 아닌 상태로 응답했다. 재시도 가능 여부는 호출자가 `status`로 판단한다."""

    def __init__(self, status: int, retry_after: str | None = None, url: str | None = None) -> None:
        # 인증이 없어 URL에 비밀이 없다. 그대로 남긴다.
        super().__init__(f"BoE request failed with HTTP {status}: {url}")
        self.status = status
        self.retry_after = retry_after


class BoePayloadError(ValueError):
    """응답이 계약을 지키지 않았다. 재시도해도 같은 결과다."""


class BoeNotCsvError(BoePayloadError):
    """CSV가 아니라 HTML 오류 페이지가 왔다.

    IADB는 요청 구간에 데이터가 한 행도 없을 때와 시계열 코드가 틀렸을 때 똑같이 이 페이지를
    HTTP 200으로 돌려준다. `FETCH_PADDING_DAYS`를 붙이고도 이게 나오면 코드나 구간 자체가
    틀린 것이다.
    """


class BoeRequest(BaseModel):
    """한 번의 수집이 저장할 관측 구간."""

    model_config = ConfigDict(frozen=True)

    observation_start: date
    observation_end: date

    @model_validator(mode="after")
    def require_ordered_period(self) -> Self:
        if self.observation_start > self.observation_end:
            raise ValueError("observation_start must not be after observation_end")
        return self

    @property
    def fetch_start(self) -> date:
        """실제로 요청할 시작일. 구간이 휴장일뿐이어도 영업일이 들어가도록 앞을 붙인다."""
        return self.observation_start - timedelta(days=FETCH_PADDING_DAYS)

    @property
    def fetch_end(self) -> date:
        return self.observation_end


class BoeObservation(BaseModel):
    """정규화한 관측값 1건."""

    model_config = ConfigDict(frozen=True)

    series: GiltSeries
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


class BoeCurve(BaseModel):
    """응답 하나를 정규화한 결과.

    관측값과 함께 응답이 실제로 덮은 구간을 들고 있다. 요청 구간보다 앞에서부터 받으므로,
    저장된 0건이 "값이 없는 구간"인지 "응답이 담지 않은 구간"인지는 이 값으로만 갈린다.
    """

    model_config = ConfigDict(frozen=True)

    observations: tuple[BoeObservation, ...]
    response_first_date: date
    response_last_date: date
    response_row_count: int


class BoeResponse(BaseModel):
    """한 번의 호출 결과와 그 호출을 재현하는 데 필요한 메타데이터."""

    model_config = ConfigDict(frozen=True)

    request: BoeRequest
    body: bytes
    status: int
    started_at: AwareDatetime
    completed_at: AwareDatetime

    @field_validator("started_at", "completed_at")
    @classmethod
    def normalize_to_utc(cls, moment: datetime) -> datetime:
        # 저장·비교용 시각은 UTC로 정규화한다. naive datetime은 AwareDatetime이 이미 막는다.
        return moment.astimezone(UTC)


def format_query_date(day: date) -> str:
    """IADB가 받는 날짜 표기. `2026-08-03` → `03/Aug/2026`."""
    return f"{day.day:02d}/{MONTH_NAMES[day.month - 1]}/{day.year}"


def build_url(fetch_start: date, fetch_end: date) -> str:
    """세 시계열을 한 번에 받는 CSV 내보내기 URL.

    `CSVF=CN`이 `DATE,SERIES,VALUE` 세로 형식을 준다. 가로 형식(`CSVF=TN`)은 시계열을
    늘릴 때 열이 늘어 헤더 대조가 시계열 목록에 묶인다.
    """
    query = urlencode(
        {
            "csv.x": "yes",
            "Datefrom": format_query_date(fetch_start),
            "Dateto": format_query_date(fetch_end),
            "SeriesCodes": ",".join(SERIES_CODES),
            "CSVF": "CN",
            "UsingCodes": "Y",
            "VPD": "Y",
            "VFD": "N",
        }
    )
    return f"{BOE_URL}?{query}"


def parse_query_date(text: str) -> date:
    """IADB 표기를 날짜로 바꾼다. `03 Aug 2026` → 2026-08-03.

    달 이름을 모르면 실패시킨다. BoE가 표기를 바꿨을 때 조용히 엉뚱한 날짜로 저장되는
    것보다 수집이 멈추는 편이 낫다.
    """
    parts = text.strip().split()
    if len(parts) != 3:
        raise BoePayloadError(f"date must be <day> <month> <year>, got {text!r}")

    month = MONTH_NUMBERS.get(parts[1])
    if month is None:
        raise BoePayloadError(f"unknown month name in date {text!r}")

    try:
        return date(int(parts[2]), month, int(parts[0]))
    except ValueError as error:
        raise BoePayloadError(f"date {text!r} is not a real calendar date") from error


def _data_rows(body: bytes) -> list[list[str]]:
    """헤더를 검증하고 데이터 행만 돌려준다."""
    try:
        text = body.decode(ENCODING)
    except UnicodeDecodeError as error:
        raise BoePayloadError(f"BoE response is not {ENCODING} text") from error

    lines = [line for line in (line.strip() for line in text.splitlines()) if line]
    if not lines:
        raise BoePayloadError("BoE returned an empty body")

    header = tuple(cell.strip() for cell in lines[0].split(","))
    if header != EXPECTED_HEADER:
        if not lines[0].startswith(EXPECTED_HEADER[0]):
            # 값이 없는 구간과 잘못된 코드 둘 다 HTML 오류 페이지를 HTTP 200으로 돌려준다.
            raise BoeNotCsvError(
                "BoE returned an HTML page instead of CSV; the series codes or the requested period is wrong"
            )
        # 열이 늘거나 이름이 바뀌면 값이 옆 칸으로 밀린다. 이 검사가 먼저 실패해야 그걸 안다.
        raise BoePayloadError(f"BoE changed the CSV header to {header!r}")

    rows: list[list[str]] = []
    for line in lines[1:]:
        cells = [cell.strip() for cell in line.split(",")]
        if len(cells) != COLUMN_COUNT:
            raise BoePayloadError(f"BoE row has {len(cells)} cells, expected {COLUMN_COUNT}: {cells!r}")
        rows.append(cells)

    if not rows:
        raise BoePayloadError("BoE response has no data rows")
    return rows


def parse_curve(body: bytes, request: BoeRequest) -> BoeCurve:
    """요청 구간에 드는 유효 관측값과 응답이 덮은 구간을 뽑는다.

    구간 밖의 행은 버린다. 요청은 `FETCH_PADDING_DAYS`만큼 앞에서부터 하므로 응답에는
    항상 구간 밖의 행이 들어 있다. 모르는 시계열 코드가 오면 실패시킨다. 우리가 물어본
    코드만 응답에 있어야 하고, 그렇지 않다면 IADB가 다른 노드를 준 것이다.
    """
    observations: list[BoeObservation] = []
    observation_dates: list[date] = []

    for row in _data_rows(body):
        observation_date = parse_query_date(row[DATE_INDEX])
        observation_dates.append(observation_date)

        code = row[SERIES_INDEX]
        series = SERIES_BY_CODE.get(code)
        if series is None:
            raise BoePayloadError(f"BoE returned an unrequested series code {code!r}")

        if not request.observation_start <= observation_date <= request.observation_end:
            continue

        cell = row[VALUE_INDEX]
        if cell in MISSING_VALUES:
            # 그날 그 만기의 고시가 없다. 결측이지 오류가 아니다.
            continue

        try:
            observations.append(BoeObservation(series=series, observation_date=observation_date, value=Decimal(cell)))
        except (ValueError, InvalidOperation) as error:
            raise BoePayloadError(f"BoE returned a non-numeric value {cell!r} for {series.value}") from error

    return BoeCurve(
        observations=tuple(observations),
        response_first_date=min(observation_dates),
        response_last_date=max(observation_dates),
        response_row_count=len(observation_dates),
    )


def parse_observations(body: bytes, request: BoeRequest) -> tuple[BoeObservation, ...]:
    """`parse_curve`의 관측값만. fred, ecos, mof와 이름을 맞추려고 둔다."""
    return parse_curve(body, request).observations


def fetch_curve(request: BoeRequest) -> BoeResponse:
    """세 시계열을 한 번에 받는다. 요청 구간보다 앞에서부터 받는다."""
    url = build_url(request.fetch_start, request.fetch_end)
    started_at = datetime.now(UTC)
    http_request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(http_request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            body = response.read()
            status = response.status
    except HTTPError as error:
        raise BoeHTTPError(error.code, error.headers.get("Retry-After"), url) from error
    except URLError as error:
        # 타임아웃과 DNS·연결 실패는 재시도 가능한 오류로 올린다.
        raise ConnectionError(f"BoE request failed: {error.reason}") from error

    return BoeResponse(
        request=request,
        body=body,
        status=status,
        started_at=started_at,
        completed_at=datetime.now(UTC),
    )


# 쿼리는 `sql/` 볼륨에 둔다. 배포 Airflow가 `/opt/airflow/sql`로 마운트하는 폴더다.
SOURCE_RECORD_INSERT = read_sql("postgres", "source_record", "insert.sql")
OBSERVATION_UPSERT = read_sql("postgres", "indicator_observation", "upsert.sql")


def store_observations(connection: Connection, response: BoeResponse) -> int:
    """조회 1건과 그 응답에서 나온 유효 관측값을 저장하고 관측값 수를 돌려준다.

    파싱을 먼저 해서 형식 오류면 아무 것도 쓰지 않는다. `(provider, series_id,
    observation_date)`가 멱등 키라서 같은 기간을 다시 수집해도 행이 늘지 않고 최신 값으로
    갱신된다. `series_id`는 제공처 안에서만 고유하므로 이 수집기의 `provider`는 항상 `SOURCE`다.

    `source_record`는 조회 한 번을 수집 단위 1건으로 남긴다. 그래서 `source_key`가 시계열
    ID가 아니라 `SOURCE_KEY`이고, 세 시계열의 관측값이 그 한 행을 함께 가리킨다.

    `payload`는 비운다. 원본이 CSV인데 컬럼 타입이 jsonb다. 어느 구간을 물어 어느 구간이
    돌아왔는지는 `metadata`가 남긴다.

    관측값이 0건이어도 `source_record`는 남긴다. 휴장 구간을 실제로 조회했다는 사실이
    없으면 아직 수집하지 않은 구간과 구분되지 않는다.

    ORM 대신 문자열 SQL을 쓴다. Airflow 이미지에는 SQLAlchemy와 이 프로젝트의 DB 설정이
    없기 때문이다. 컬럼 이름은 `tests/collectors/test_boe.py`가 모델 metadata와 맞춰 둔다.
    """
    request = response.request
    curve = parse_curve(response.body, request)
    request_metadata = json.dumps(
        {
            "http_status": response.status,
            "url": build_url(request.fetch_start, request.fetch_end),
            "source_unit_name": SOURCE_UNIT_NAME,
            "observation_start": request.observation_start.isoformat(),
            "observation_end": request.observation_end.isoformat(),
            # 저장 구간이 아니라 실제로 요청한 구간. 앞에 붙인 패딩이 여기 드러난다.
            "fetch_start": request.fetch_start.isoformat(),
            "fetch_end": request.fetch_end.isoformat(),
            # 응답이 실제로 덮은 구간. 0건이 값 없음인지 구간 밖인지를 여기서 되짚는다.
            "response_first_date": curve.response_first_date.isoformat(),
            "response_last_date": curve.response_last_date.isoformat(),
            "response_row_count": curve.response_row_count,
            "series_codes": list(SERIES_CODES),
            "series_ids": list(GILT_SERIES),
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
