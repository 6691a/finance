"""일본 재무성(MOF)에서 국채 금리(JGB)를 수집한다.

배포 Airflow와 공유되는 폴더는 `airflow/dags`와 `airflow/modules` 둘뿐이다. Airflow는
`apps/`도 `core/`도 보지 못한다. 그래서 DAG가 실행 시점에 필요한 코드는 전부 여기 있어야
한다. `dags/`에는 스케줄과 오케스트레이션만 두고 수집 규칙은 이 모듈에 둔다.

의존성은 표준 라이브러리와 Pydantic, PEP 249 연결로 제한한다. SQLAlchemy 모델과
`core.config`는 import하지 않는다. 저장 대상 테이블의 정의는 백엔드의 `apps/models`가
원본이고, 여기 SQL의 컬럼 이름은 `tests/collectors/test_mof.py`가 그 모델 metadata와
대조한다.

`fred.py`, `ecos.py`와 같은 테이블에 쌓지만 제공처의 성질이 달라 다음이 갈린다.

- **인증이 없다.** 정적 CSV 파일이라 API 키도 등록도 없다. URL에 비밀이 실리지 않으므로
  다른 두 수집기와 달리 예외 메시지와 로그에 URL을 넣고 예외 체인도 끊지 않는다.
  대신 User-Agent를 명시한다. 기본 `Python-urllib/3.x`는 막히는 경우가 있다.
- **한 번 받으면 전 만기가 온다.** 시계열마다 요청하지 않는다. 파일 하나가 곧 곡선
  전체라서 `source_record` 한 행에 여러 `series_id`의 관측값이 달린다.
- **`source_key`가 시계열 ID가 아니라 파일 이름이다**(`jgbcm` 또는 `jgbcm_all`).
  fred와 ecos는 시계열 ID를 넣는다. 수집 단위가 파일이므로 여기서는 규약이 갈린다.
- **`payload`를 저장하지 않는다.** 원본이 CSV인데 컬럼 타입이 jsonb이고, 과거 전체 파일은
  1MB가 넘어 감싸 넣기에도 크다. 어느 파일을 언제 받아 어느 구간이 들어 있었는지는
  `metadata`가 남긴다.
- **결측을 알리지 않는다.** 휴일은 행 자체가 없고, 아직 발행되지 않은 만기는 `-`로 온다.
  그래서 열 구성을 매번 헤더와 대조한다. 재무성이 열을 추가하면 값이 조용히 옆 칸으로 밀린다.

## 파일 두 개가 이어 붙는다

    jgbcm.csv            이번 달치만. 매달 1일에 비워진다
    data/jgbcm_all.csv   1974-09-24부터 지난달 말까지. 이번 달은 없다

**어느 한쪽도 최근 며칠과 과거를 함께 담지 못한다.** 조회 구간이 달 경계를 넘으면 둘 다
받아야 한다. `fetch_curves`가 최신 파일의 첫 날짜를 보고 과거 파일이 필요한지 정한다.
이 판단이 없으면 매달 1일부터 며칠 동안 되돌아본 구간이 조용히 사라진다.

파일 형식은 다음과 같다. 인코딩은 CP932(Shift-JIS)이고 첫 줄은 제목, 둘째 줄이 헤더다.
`jgbcm.csv`는 데이터 뒤에 빈 줄과 안내 문구 줄이 붙는다.

    国債金利情報 (令和8年8月),,,...,(単位 : %)
    基準日,1年,2年,...,40年
    R8.8.3,1.287,1.562,...,3.948
    ,,,,,,,,,,,,,,,
    ※最新のcsvデータがダウンロードできない場合...,,,...,

날짜는 和暦이다. `R8.8.3`은 令和8年8月3日, 즉 2026-08-03이다. 기준일은 일본 영업일이며
제공처 기준을 그대로 보존한다. 저장하는 시각(`started_at`, `completed_at`)은 UTC다.

원본 응답은 `source_record`에, 유효 관측값은 `indicator_observation`에 저장한다. 두 쓰기는
호출자가 연 하나의 트랜잭션 안에서 실행되며 커밋과 롤백은 호출자가 결정한다.
"""

import json
import re
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Self
from urllib.error import HTTPError, URLError
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

MOF_URL = "https://www.mof.go.jp/jgbs/reference/interest_rate"
SOURCE = "mof"

# 재무성 CSV는 CP932다. UTF-8로 읽으면 헤더 대조부터 어긋난다.
ENCODING = "cp932"

# 기본 User-Agent를 막는 경우가 있어 명시한다. 인증이 아니므로 값 자체에 의미는 없다.
USER_AGENT = "news-collector/1.0 (+https://www.mof.go.jp/jgbs/reference/interest_rate/)"

REQUEST_TIMEOUT_SECONDS = 30

# 제공처 표기. 저장 표기는 fred, ecos와 맞춘다. 세 나라 금리를 한 쿼리로 비교하려면
# 단위 문자열이 같아야 한다.
SOURCE_UNIT_NAME = "%"
SERIES_UNIT = "Percent"


class MofFile(StrEnum):
    """받을 파일. 이번 달치와 지난달 말까지의 과거 전체, 둘뿐이다."""

    CURRENT = "current"
    ALL = "all"

    @property
    def filename(self) -> str:
        """`source_record.source_key`에 남기는 이름. 경로가 아니라 파일 이름만 쓴다."""
        return "jgbcm" if self is MofFile.CURRENT else "jgbcm_all"

    @property
    def path(self) -> str:
        # 과거 전체 파일만 data/ 아래에 있다. 두 파일의 경로가 다르다.
        return "jgbcm.csv" if self is MofFile.CURRENT else "data/jgbcm_all.csv"


class JgbSeries(StrEnum):
    """수집 대상 시계열. 저장 식별자, CSV 열 이름, 만기, 한국어 이름을 한 줄에 묶는다.

    재무성 CSV는 1·2·3·4·5·6·7·8·9·10·15·20·25·30·40년 열을 준다. 그중 실제로 입찰
    발행되는 연한만 저장한다. 나머지는 발행 종목이 없는 곡선 위의 값이라 시장이 인용하지
    않는다. 열은 전부 검증하되 저장은 여기 있는 것만 한다.

    Enum 값은 `indicator_observation.series_id`에 그대로 저장한다. 시계열을 늘리려면
    여기와 `reference.indicator_series` 시드를 같은 커밋에서 함께 늘린다. 관측값에서
    마스터로 외래키를 걸지 않으므로 어긋나면 DAG가 아니라 대시보드가 조용히 빈다.
    `tests/migrations/test_indicator_series_catalog.py`가 둘을 대조한다.
    """

    column_label: str
    maturity_months: int
    label: str

    def __new__(cls, series_id: str, column_label: str, maturity_months: int, label: str) -> Self:
        member = str.__new__(cls, series_id)
        member._value_ = series_id
        member.column_label = column_label
        member.maturity_months = maturity_months
        member.label = label
        return member

    JGB_2Y = ("JGB2Y", "2年", 24, "일본 2년물")
    JGB_5Y = ("JGB5Y", "5年", 60, "일본 5년물")
    JGB_10Y = ("JGB10Y", "10年", 120, "일본 10년물")
    JGB_20Y = ("JGB20Y", "20年", 240, "일본 20년물")
    JGB_30Y = ("JGB30Y", "30年", 360, "일본 30년물")
    JGB_40Y = ("JGB40Y", "40年", 480, "일본 40년물")


JGB_SERIES: tuple[str, ...] = tuple(series.value for series in JgbSeries)

# 헤더 줄의 열 구성. 저장하지 않는 만기까지 전부 둔다. 재무성이 열을 추가하거나 빼면 값이
# 조용히 옆 칸으로 밀리므로, 저장 대상만 확인해서는 그 사고를 잡을 수 없다.
DATE_COLUMN = "基準日"
EXPECTED_HEADER: tuple[str, ...] = (
    DATE_COLUMN,
    "1年",
    "2年",
    "3年",
    "4年",
    "5年",
    "6年",
    "7年",
    "8年",
    "9年",
    "10年",
    "15年",
    "20年",
    "25年",
    "30年",
    "40年",
)
COLUMN_COUNT = len(EXPECTED_HEADER)

# 제목 줄과 헤더 줄. 데이터는 그 다음부터다.
HEADER_LINE_INDEX = 1

# 和暦 연호별 기준 연도. 연호 n년은 기준 연도 + n이다(昭和49년=1974, 平成1년=1989, 令和1년=2019).
ERA_BASE_YEARS = {"S": 1925, "H": 1988, "R": 2018}

# 기준일 칸의 모양. 이 모양이 아니고 다른 칸이 전부 비어 있으면 데이터가 아니라 안내 문구 줄이다.
ERA_DATE_PATTERN = re.compile(r"^[SHRshr]\d")

# 과거 전체 파일이 昭和49년부터라 그보다 이른 날짜는 형식이 깨진 것이다. 위쪽은 연호 표기가
# 밀리는 사고를 잡을 정도로만 넉넉하게 둔다.
MIN_YEAR = 1974
MAX_YEAR = 2100

# 그 만기가 아직 발행되지 않았거나 그날 고시가 없는 칸. 결측이지 오류가 아니다.
MISSING_VALUES = frozenset({"", "-", "－"})
class MofHTTPError(RuntimeError):
    """재무성이 2xx가 아닌 상태로 응답했다. 재시도 가능 여부는 호출자가 `status`로 판단한다."""

    def __init__(self, status: int, retry_after: str | None = None, url: str | None = None) -> None:
        # 인증이 없어 URL에 비밀이 없다. fred, ecos와 달리 URL을 그대로 남긴다.
        super().__init__(f"MOF request failed with HTTP {status}: {url}")
        self.status = status
        self.retry_after = retry_after


class MofPayloadError(ValueError):
    """CSV가 계약을 지키지 않았다. 재시도해도 같은 결과다."""


class MofCoverageError(MofPayloadError):
    """받은 파일들이 요청 구간의 시작을 덮지 못한다.

    자동 선택에서는 1974-09-24보다 이른 구간을 물었을 때만 난다. `file`을 직접 지정했다면
    그 파일이 구간을 못 덮는다는 뜻이다. 이 검사가 없으면 요청한 구간 일부가 조용히 빈다.
    """


class MofRequest(BaseModel):
    """한 번의 수집이 저장할 관측 구간과 받을 파일."""

    model_config = ConfigDict(frozen=True)

    observation_start: date
    observation_end: date
    # None이면 자동이다. 이번 달 파일의 첫 날짜를 보고 과거 파일이 더 필요한지 정한다.
    file: MofFile | None = None

    @model_validator(mode="after")
    def require_ordered_period(self) -> Self:
        if self.observation_start > self.observation_end:
            raise ValueError("observation_start must not be after observation_end")
        return self


class MofObservation(BaseModel):
    """정규화한 관측값 1건."""

    model_config = ConfigDict(frozen=True)

    series: JgbSeries
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


class MofCurve(BaseModel):
    """파일 하나를 정규화한 결과.

    관측값과 함께 그 파일이 실제로 덮은 구간을 들고 있다. 파일마다 담는 기간이 다르므로,
    저장된 0건이 "값이 없는 구간"인지 "그 파일이 담지 않은 구간"인지는 이 값으로만 갈린다.
    """

    model_config = ConfigDict(frozen=True)

    observations: tuple[MofObservation, ...]
    file_first_date: date
    file_last_date: date
    file_row_count: int


class MofResponse(BaseModel):
    """한 번의 호출 결과와 그 호출을 재현하는 데 필요한 메타데이터."""

    model_config = ConfigDict(frozen=True)

    request: MofRequest
    file: MofFile
    body: bytes
    status: int
    started_at: AwareDatetime
    completed_at: AwareDatetime

    @field_validator("started_at", "completed_at")
    @classmethod
    def normalize_to_utc(cls, moment: datetime) -> datetime:
        # 저장·비교용 시각은 UTC로 정규화한다. naive datetime은 AwareDatetime이 이미 막는다.
        return moment.astimezone(UTC)


def build_url(file: MofFile) -> str:
    return f"{MOF_URL}/{file.path}"


def parse_era_date(text: str) -> date:
    """和暦 표기를 서기 날짜로 바꾼다. `R8.8.3` → 2026-08-03.

    연호 글자를 모르면 실패시킨다. 재무성이 표기를 바꿨을 때 조용히 엉뚱한 연도로 저장되는
    것보다 수집이 멈추는 편이 낫다.
    """
    stripped = text.strip()
    base_year = ERA_BASE_YEARS.get(stripped[:1].upper())
    if base_year is None:
        raise MofPayloadError(f"unknown Japanese era in date {text!r}")

    parts = stripped[1:].split(".")
    if len(parts) != 3:
        raise MofPayloadError(f"date must be <era><year>.<month>.<day>, got {text!r}")

    try:
        era_year, month, day = (int(part) for part in parts)
    except ValueError as error:
        raise MofPayloadError(f"date has a non-numeric part: {text!r}") from error

    if era_year <= 0:
        raise MofPayloadError(f"era year must be positive, got {text!r}")

    year = base_year + era_year
    if not MIN_YEAR <= year <= MAX_YEAR:
        raise MofPayloadError(f"date {text!r} resolves to {year}, outside {MIN_YEAR}..{MAX_YEAR}")

    try:
        return date(year, month, day)
    except ValueError as error:
        raise MofPayloadError(f"date {text!r} is not a real calendar date") from error


def _data_rows(body: bytes) -> list[list[str]]:
    """헤더를 검증하고 데이터 행만 돌려준다."""
    try:
        text = body.decode(ENCODING)
    except UnicodeDecodeError as error:
        raise MofPayloadError(f"MOF response is not {ENCODING} text") from error

    lines = [[cell.strip() for cell in line.split(",")] for line in text.splitlines()]
    if len(lines) <= HEADER_LINE_INDEX + 1:
        raise MofPayloadError("MOF response has no data rows")

    header = lines[HEADER_LINE_INDEX]
    if tuple(header) != EXPECTED_HEADER:
        # 열이 늘거나 이름이 바뀌면 값이 옆 칸으로 밀린다. 이 검사가 먼저 실패해야 그걸 안다.
        raise MofPayloadError(f"MOF changed the CSV header to {header!r}")

    rows: list[list[str]] = []
    for line in lines[HEADER_LINE_INDEX + 1 :]:
        if not any(line):
            # 데이터 뒤의 `,,,,,,` 빈 줄.
            continue
        if not ERA_DATE_PATTERN.match(line[0]) and not any(line[1:]):
            # 데이터 뒤의 안내 문구 줄. 값이 아니라 문장 하나가 첫 칸에 들어 있다.
            continue
        if len(line) != COLUMN_COUNT:
            raise MofPayloadError(f"MOF row has {len(line)} cells, expected {COLUMN_COUNT}: {line!r}")
        rows.append(line)

    if not rows:
        raise MofPayloadError("MOF response has no data rows")
    return rows


def file_span(body: bytes) -> tuple[date, date]:
    """파일이 담고 있는 첫 기준일과 마지막 기준일.

    과거 파일이 더 필요한지 정할 때 쓴다. 값 칸은 읽지 않는다.
    """
    dates = [parse_era_date(row[0]) for row in _data_rows(body)]
    return min(dates), max(dates)


def parse_curve(body: bytes, request: MofRequest) -> MofCurve:
    """요청 구간에 드는 유효 관측값과 파일이 덮은 구간을 뽑는다.

    구간 밖의 행은 버리고, 아직 발행되지 않은 만기의 빈 칸은 건너뛴다. 형식이 깨지면 전체를
    실패시킨다. 구간을 덮는지는 파일마다 판정할 수 없으므로 여기서 보지 않는다.
    `fetch_curves`가 받은 파일 전체를 놓고 판정한다.
    """
    observations: list[MofObservation] = []
    observation_dates: list[date] = []

    for row in _data_rows(body):
        observation_date = parse_era_date(row[0])
        observation_dates.append(observation_date)
        if not request.observation_start <= observation_date <= request.observation_end:
            continue

        for series in JgbSeries:
            cell = row[EXPECTED_HEADER.index(series.column_label)]
            if cell in MISSING_VALUES:
                # 그 만기가 아직 발행되지 않았거나 그날 고시가 없다. 결측이지 오류가 아니다.
                continue
            try:
                observations.append(
                    MofObservation(series=series, observation_date=observation_date, value=Decimal(cell))
                )
            except (ValueError, InvalidOperation) as error:
                raise MofPayloadError(f"MOF returned a non-numeric value {cell!r} for {series.value}") from error

    return MofCurve(
        observations=tuple(observations),
        file_first_date=min(observation_dates),
        file_last_date=max(observation_dates),
        file_row_count=len(observation_dates),
    )


def parse_observations(body: bytes, request: MofRequest) -> tuple[MofObservation, ...]:
    """`parse_curve`의 관측값만. fred, ecos와 이름을 맞추려고 둔다."""
    return parse_curve(body, request).observations


def _fetch(file: MofFile, request: MofRequest) -> MofResponse:
    url = build_url(file)
    started_at = datetime.now(UTC)
    http_request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(http_request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            body = response.read()
            status = response.status
    except HTTPError as error:
        raise MofHTTPError(error.code, error.headers.get("Retry-After"), url) from error
    except URLError as error:
        # 타임아웃과 DNS·연결 실패는 재시도 가능한 오류로 올린다.
        raise ConnectionError(f"MOF request failed: {error.reason}") from error

    return MofResponse(
        request=request,
        file=file,
        body=body,
        status=status,
        started_at=started_at,
        completed_at=datetime.now(UTC),
    )


def fetch_curves(request: MofRequest) -> tuple[MofResponse, ...]:
    """요청 구간을 덮는 파일을 전부 받는다. 오래된 파일이 앞에 온다.

    `request.file`이 정해져 있으면 그 파일만 받는다. 비어 있으면 이번 달 파일을 먼저 받고
    (수 KB다), 그 파일의 첫 날짜가 요청 시작보다 늦을 때만 과거 전체 파일을 더 받는다.
    두 파일은 겹치지 않고 이어 붙으므로 같은 날짜를 두 번 저장하지 않는다.

    어느 파일을 고르는지는 재시도 정책이 아니라 수집 규칙이므로 DAG가 아니라 여기서 정한다.
    """
    if request.file is not None:
        responses = (_fetch(request.file, request),)
    else:
        current = _fetch(MofFile.CURRENT, request)
        current_first, _ = file_span(current.body)
        responses = (
            (current,) if request.observation_start >= current_first else (_fetch(MofFile.ALL, request), current)
        )

    first_date = min(file_span(response.body)[0] for response in responses)
    if first_date > request.observation_start:
        raise MofCoverageError(
            f"MOF files start at {first_date}, after the requested {request.observation_start}; "
            f"the published history begins on {MIN_YEAR}-09-24"
        )
    return responses


# 쿼리는 `sql/` 볼륨에 둔다. 배포 Airflow가 `/opt/airflow/sql`로 마운트하는 폴더다.
SOURCE_RECORD_INSERT = read_sql("postgres", "source_record", "insert.sql")
OBSERVATION_UPSERT = read_sql("postgres", "indicator_observation", "upsert.sql")


def store_observations(connection: Connection, response: MofResponse) -> int:
    """파일 1건과 그 파일에서 나온 유효 관측값을 저장하고 관측값 수를 돌려준다.

    파싱을 먼저 해서 형식 오류면 아무 것도 쓰지 않는다. `(provider, series_id,
    observation_date)`가 멱등 키라서 같은 기간을 다시 수집해도 행이 늘지 않고 최신 값으로
    갱신된다. `series_id`는 제공처 안에서만 고유하므로 이 수집기의 `provider`는 항상 `SOURCE`다.

    `source_record`는 파일 하나를 수집 단위 1건으로 남긴다. 그래서 `source_key`가 시계열
    ID가 아니라 파일 이름이고, 여러 시계열의 관측값이 그 한 행을 함께 가리킨다.

    `payload`는 비운다. 원본이 CSV라 jsonb 컬럼에 그대로 들어가지 않고, 과거 전체 파일은
    1MB가 넘는다. 어느 파일이 어느 구간을 담고 있었는지는 `metadata`가 남긴다.

    관측값이 0건이어도 `source_record`는 남긴다. 휴일 구간을 실제로 조회했다는 사실이
    없으면 아직 수집하지 않은 구간과 구분되지 않는다.

    ORM 대신 문자열 SQL을 쓴다. Airflow 이미지에는 SQLAlchemy와 이 프로젝트의 DB 설정이
    없기 때문이다. 컬럼 이름은 `tests/collectors/test_mof.py`가 모델 metadata와 맞춰 둔다.
    """
    curve = parse_curve(response.body, response.request)
    request_metadata = json.dumps(
        {
            "http_status": response.status,
            "url": build_url(response.file),
            "file": response.file.filename,
            "source_unit_name": SOURCE_UNIT_NAME,
            "observation_start": response.request.observation_start.isoformat(),
            "observation_end": response.request.observation_end.isoformat(),
            # 파일이 실제로 덮은 구간. 0건이 값 없음인지 구간 밖인지를 여기서 되짚는다.
            "file_first_date": curve.file_first_date.isoformat(),
            "file_last_date": curve.file_last_date.isoformat(),
            "file_row_count": curve.file_row_count,
            "series_ids": list(JGB_SERIES),
        },
        ensure_ascii=False,
    )

    with connection.cursor() as cursor:
        cursor.execute(
            SOURCE_RECORD_INSERT,
            (
                "api",
                SOURCE,
                response.file.filename,
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
