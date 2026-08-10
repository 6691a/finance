"""독일 분데스방크(Bundesbank)에서 독일 국채 수익률 곡선을 수집한다.

배포 Airflow와 공유되는 폴더는 `airflow/dags`와 `airflow/modules` 둘뿐이다. Airflow는
`apps/`도 `core/`도 보지 못한다. 그래서 DAG가 실행 시점에 필요한 코드는 전부 여기 있어야
한다. `dags/`에는 스케줄과 오케스트레이션만 두고 수집 규칙은 이 모듈에 둔다.

의존성은 표준 라이브러리와 Pydantic, PEP 249 연결로 제한한다. SQLAlchemy 모델과
`core.config`는 import하지 않는다. 저장 대상 테이블의 정의는 백엔드의 `apps/models`가
원본이고, 여기 SQL의 컬럼 이름은 `tests/collectors/test_bbk.py`가 그 모델 metadata와
대조한다.

받는 곳은 분데스방크 통계 API(`api.statistiken.bundesbank.de`)의 `BBSIS` 데이터셋이다.
인증이 없고 URL에 비밀이 실리지 않으므로 `mof.py`, `boe.py`, `ecb.py`와 마찬가지로 예외
메시지와 로그에 URL을 그대로 남긴다.

## 왜 독일만 따로 받나

유로 지역 개별 회원국의 **일별** 국채 금리를 무료·무인증으로 주는 공식 소스는 독일뿐이다.
프랑스·이탈리아·스페인은 월별밖에 없어 `ecb_irs.py`가 따로 받는다. 유로 지역 전체의
AAA 곡선은 `ecb.py`가 계속 받는다. 셋은 성격이 달라 서로를 대체하지 않는다.

    ecb.py       유로 지역(XM) AAA 곡선   일별   3개월~30년
    bbk.py       독일(DE)                 일별   1~30년   ← 이 모듈
    ecb_irs.py   프랑스·이탈리아·스페인   월별   10년만

## 한 번 요청하면 곡선 전체가 온다

SDMX 키의 만기 차원에 `+`를 넣어 만기를 한꺼번에 물을 수 있다. `mof`, `boe`, `ecb`처럼
한 응답이 곡선 전체를 담으므로 `source_record`도 시계열이 아니라 조회 단위로 한 행만
남기고 `source_key`에 `SOURCE_KEY`를 넣는다.

응답은 만기가 **열로 늘어선 가로 형식**이다. 값 열마다 `_FLAGS` 열이 하나씩 따라붙는다.
`lang=en`을 붙여야 구분자가 `,`이고 소수점이 `.`다. 붙이지 않으면 독일어 표기라 구분자가
`;`이고 소수점이 `,`가 되어 파싱이 통째로 어긋난다. 인코딩은 BOM 붙은 UTF-8이다.

    "",BBSIS.D.I.ZST...R01XX...A,BBSIS.D.I.ZST...R01XX...A_FLAGS,BBSIS...R10XX...A,...
    "",Term structure of interest rates ... 1.0 years / daily data,,Term structure ... ,
    Decimals,2,,2,
    last update,2026-08-06 12:48:47,,2026-08-06 12:48:48,
    2026-08-05,2.61,,3.16,
    2026-08-06,2.62,,3.17,

**열을 위치가 아니라 이름으로 묶는다.** 만기를 늘리면 열 순서가 바뀔 수 있고, 값 열과
`_FLAGS` 열이 번갈아 오기 때문에 위치로 세면 실수가 조용히 지나간다. 헤더에서 우리가
요청한 시계열 키가 전부 있고 모르는 키가 없다는 것까지 확인한 뒤 그 인덱스를 쓴다.

첫 몇 줄은 메타데이터다. 첫 칸이 날짜인 줄만 데이터로 본다.

기준일은 독일 영업일이며 제공처 기준을 그대로 보존한다. 곡선은 독일 시간 정오 무렵에
갱신된다. 저장하는 시각(`started_at`, `completed_at`)은 UTC다.

값은 Svensson 모형이 추정한 잔존만기별 금리다. 개별 국채의 실제 체결 수익률이 아니다.
ECB의 유로 지역 AAA 곡선과 같은 방식이라 둘을 나란히 놓고 봐도 성격이 어긋나지 않는다.

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

BBK_URL = "https://api.statistiken.bundesbank.de/rest/data"
SOURCE = "bbk"

# 데이터셋. `BBSIS`가 상장 연방채권 금리구조(Zinsstruktur)다.
DATASET = "BBSIS"

# 시계열 키에서 만기를 뺀 앞뒤. 만기 차원만 갈리고 나머지는 고정이다.
# D=일별, ZST=금리구조, S1311=중앙정부, A604=상장 연방채권 Svensson 추정.
KEY_PREFIX = "D.I.ZST.ZI.EUR.S1311.B.A604"
KEY_SUFFIX = "R.A.A._Z._Z.A"

# 수집 단위는 시계열이 아니라 조회 한 번이다. `source_record.source_key`에 이 값을 넣는다.
SOURCE_KEY = f"{DATASET}.{KEY_PREFIX}"

# BOM이 붙어 온다. 떼지 않으면 헤더 첫 칸 대조부터 어긋난다.
ENCODING = "utf-8-sig"

USER_AGENT = "news-collector/1.0 (+https://www.bundesbank.de/en/statistics)"

REQUEST_TIMEOUT_SECONDS = 30

# 제공처 표기. 저장 표기는 다른 수집기와 맞춘다. 여러 나라 금리를 한 쿼리로 비교하려면
# 단위 문자열이 같아야 한다.
SOURCE_UNIT_NAME = "%"
SERIES_UNIT = "Percent"


class BundSeries(StrEnum):
    """수집 대상 시계열. 저장 식별자, SDMX 만기 차원, 만기, 한국어 이름을 한 줄에 묶는다.

    `BBSIS`는 1~10년과 15·20·25·30년을 준다. 그중 시장이 인용하는 표준 만기만 저장한다.
    4·6·8·9·25년은 같은 Svensson 곡선 위의 보간값이라 따로 쌓아도 비교에 쓰이지 않는다.
    남긴 아홉 개는 `ecb.EuroYieldSeries`의 1년 이상 만기와 정확히 같아서, 독일 곡선과
    유로 지역 AAA 곡선을 만기별로 그대로 겹쳐 볼 수 있다.

    Enum 값은 `indicator_observation.series_id`에 그대로 저장한다. 시계열을 늘리려면
    여기와 `reference.indicator_series` 시드를 같은 커밋에서 함께 늘린다. 관측값에서
    마스터로 외래키를 걸지 않으므로 어긋나면 DAG가 아니라 대시보드가 조용히 빈다.
    `tests/migrations/test_indicator_series_catalog.py`가 둘을 대조한다.
    """

    maturity_code: str
    maturity_months: int
    label: str

    def __new__(cls, series_id: str, maturity_code: str, maturity_months: int, label: str) -> Self:
        member = str.__new__(cls, series_id)
        member._value_ = series_id
        member.maturity_code = maturity_code
        member.maturity_months = maturity_months
        member.label = label
        return member

    DE_1Y = ("DE1Y", "R01XX", 12, "독일 1년물")
    DE_2Y = ("DE2Y", "R02XX", 24, "독일 2년물")
    DE_3Y = ("DE3Y", "R03XX", 36, "독일 3년물")
    DE_5Y = ("DE5Y", "R05XX", 60, "독일 5년물")
    DE_7Y = ("DE7Y", "R07XX", 84, "독일 7년물")
    DE_10Y = ("DE10Y", "R10XX", 120, "독일 10년물")
    DE_15Y = ("DE15Y", "R15XX", 180, "독일 15년물")
    DE_20Y = ("DE20Y", "R20XX", 240, "독일 20년물")
    DE_30Y = ("DE30Y", "R30XX", 360, "독일 30년물")

    @property
    def series_key(self) -> str:
        """SDMX 시계열 키. 요청에도 쓰고 응답 헤더 대조에도 쓴다."""
        return f"{KEY_PREFIX}.{self.maturity_code}.{KEY_SUFFIX}"

    @property
    def column_name(self) -> str:
        """응답 헤더에 나오는 값 열 이름. 데이터셋 이름이 앞에 붙는다."""
        return f"{DATASET}.{self.series_key}"


BUND_SERIES: tuple[str, ...] = tuple(series.value for series in BundSeries)

# 요청 키의 만기 차원. `+`가 SDMX의 OR다.
MATURITY_CODES: tuple[str, ...] = tuple(series.maturity_code for series in BundSeries)

# 응답 헤더의 값 열 이름에서 저장 시계열로 되짚는 표.
SERIES_BY_COLUMN: dict[str, BundSeries] = {series.column_name: series for series in BundSeries}

# 헤더 첫 칸. 날짜 열에는 이름이 없다. 원문에는 `""`로 적혀 있지만 그건 CSV의 빈 문자열
# 표기라서 `csv.reader`를 거치면 따옴표가 벗겨져 빈 문자열이 된다. 원문 그대로 두면
# 어떤 응답도 헤더 검사를 통과하지 못한다.
DATE_COLUMN = ""

# 값 열마다 따라붙는 상태 열. 값이 아니므로 읽지 않는다.
FLAGS_SUFFIX = "_FLAGS"

# 데이터 줄인지 가르는 모양. 앞의 몇 줄은 제목·소수점 자릿수·최종 갱신 시각 같은 메타데이터다.
ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 그날 그 만기의 고시가 없는 칸. 결측이지 오류가 아니다.
MISSING_VALUES = frozenset({"", ".", "-"})


class Cursor(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(self, *args: object) -> bool | None: ...

    def execute(self, statement: str, parameters: Sequence[Any]) -> object: ...

    def fetchone(self) -> Any: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...


class BbkHTTPError(RuntimeError):
    """분데스방크가 2xx가 아닌 상태로 응답했다. 재시도 가능 여부는 호출자가 `status`로 판단한다."""

    def __init__(self, status: int, retry_after: str | None = None, url: str | None = None) -> None:
        # 인증이 없어 URL에 비밀이 없다. 그대로 남긴다.
        super().__init__(f"Bundesbank request failed with HTTP {status}: {url}")
        self.status = status
        self.retry_after = retry_after


class BbkPayloadError(ValueError):
    """응답이 계약을 지키지 않았다. 재시도해도 같은 결과다."""


class BbkRequest(BaseModel):
    """한 번의 수집이 저장할 관측 구간."""

    model_config = ConfigDict(frozen=True)

    observation_start: date
    observation_end: date

    @model_validator(mode="after")
    def require_ordered_period(self) -> Self:
        if self.observation_start > self.observation_end:
            raise ValueError("observation_start must not be after observation_end")
        return self


class BbkObservation(BaseModel):
    """정규화한 관측값 1건."""

    model_config = ConfigDict(frozen=True)

    series: BundSeries
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


class BbkCurve(BaseModel):
    """응답 하나를 정규화한 결과.

    관측값과 함께 응답이 실제로 덮은 구간을 들고 있다. 구간 전체가 휴장이면 데이터 줄이
    없고 그때는 세 값이 모두 비어 있다. 저장된 0건이 "값이 없는 구간"임을 여기서 되짚는다.
    """

    model_config = ConfigDict(frozen=True)

    observations: tuple[BbkObservation, ...]
    response_first_date: date | None
    response_last_date: date | None
    response_row_count: int


class BbkResponse(BaseModel):
    """한 번의 호출 결과와 그 호출을 재현하는 데 필요한 메타데이터."""

    model_config = ConfigDict(frozen=True)

    request: BbkRequest
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
    """만기를 한꺼번에 무는 SDMX 시계열 키. 만기 차원만 `+`로 이어 붙인다."""
    return f"{KEY_PREFIX}.{'+'.join(MATURITY_CODES)}.{KEY_SUFFIX}"


def build_url(request: BbkRequest) -> str:
    query = urlencode(
        {
            "startPeriod": request.observation_start.isoformat(),
            "endPeriod": request.observation_end.isoformat(),
            "format": "csv",
            # 이걸 빼면 독일어 표기가 온다. 구분자가 `;`, 소수점이 `,`라 파싱이 통째로 어긋난다.
            "lang": "en",
        }
    )
    return f"{BBK_URL}/{DATASET}/{build_series_key()}?{query}"


def _column_indexes(header: Sequence[str]) -> dict[BundSeries, int]:
    """헤더를 검증하고 시계열마다 값 열의 위치를 돌려준다.

    위치가 아니라 이름으로 묶는다. 값 열과 `_FLAGS` 열이 번갈아 오고 만기를 늘리면 순서가
    바뀔 수 있어서, 위치로 세면 값이 옆 칸으로 밀린 것을 못 잡는다.
    """
    cells = [cell.strip() for cell in header]
    if not cells or cells[0] != DATE_COLUMN:
        raise BbkPayloadError(f"Bundesbank response does not start with the date column: {cells[:1]!r}")

    indexes: dict[BundSeries, int] = {}
    for position, name in enumerate(cells[1:], start=1):
        if name.endswith(FLAGS_SUFFIX):
            continue
        series = SERIES_BY_COLUMN.get(name)
        if series is None:
            raise BbkPayloadError(f"Bundesbank returned an unrequested series column {name!r}")
        indexes[series] = position

    missing = [series.value for series in BundSeries if series not in indexes]
    if missing:
        raise BbkPayloadError(f"Bundesbank response is missing the columns for {missing!r}")
    return indexes


def _rows(body: bytes) -> tuple[dict[BundSeries, int], list[list[str]]]:
    """헤더를 검증하고 값 열 위치와 데이터 줄만 돌려준다.

    앞의 몇 줄은 제목과 소수점 자릿수 같은 메타데이터다. 첫 칸이 ISO 날짜인 줄만 데이터로
    본다. 데이터 줄이 하나도 없으면 요청 구간이 전부 휴장이라는 뜻이라 빈 목록이다.
    """
    try:
        text = body.decode(ENCODING)
    except UnicodeDecodeError as error:
        raise BbkPayloadError(f"Bundesbank response is not {ENCODING} text") from error

    lines = [row for row in csv.reader(io.StringIO(text)) if row]
    if not lines:
        raise BbkPayloadError("Bundesbank returned an empty body")

    indexes = _column_indexes(lines[0])
    column_count = len(lines[0])

    rows: list[list[str]] = []
    for line in lines[1:]:
        if not ISO_DATE_PATTERN.match(line[0].strip()):
            # 제목, 소수점 자릿수, 최종 갱신 시각 같은 메타데이터 줄이다.
            continue
        if len(line) != column_count:
            raise BbkPayloadError(f"Bundesbank row has {len(line)} cells, expected {column_count}: {line!r}")
        rows.append(line)
    return indexes, rows


def parse_curve(body: bytes, request: BbkRequest) -> BbkCurve:
    """요청 구간에 드는 유효 관측값과 응답이 덮은 구간을 뽑는다."""
    indexes, rows = _rows(body)

    observations: list[BbkObservation] = []
    observation_dates: list[date] = []

    for row in rows:
        observation_date = date.fromisoformat(row[0].strip())
        observation_dates.append(observation_date)
        if not request.observation_start <= observation_date <= request.observation_end:
            continue

        for series, index in indexes.items():
            cell = row[index].strip()
            if cell in MISSING_VALUES:
                # 그날 그 만기의 고시가 없다. 결측이지 오류가 아니다.
                continue
            try:
                observations.append(
                    BbkObservation(series=series, observation_date=observation_date, value=Decimal(cell))
                )
            except (ValueError, InvalidOperation) as error:
                raise BbkPayloadError(f"Bundesbank returned a non-numeric value {cell!r} for {series.value}") from error

    return BbkCurve(
        observations=tuple(observations),
        response_first_date=min(observation_dates, default=None),
        response_last_date=max(observation_dates, default=None),
        response_row_count=len(observation_dates),
    )


def parse_observations(body: bytes, request: BbkRequest) -> tuple[BbkObservation, ...]:
    """`parse_curve`의 관측값만. 다른 수집기와 이름을 맞추려고 둔다."""
    return parse_curve(body, request).observations


def fetch_curve(request: BbkRequest) -> BbkResponse:
    """곡선 전체를 한 번에 받는다."""
    url = build_url(request)
    started_at = datetime.now(UTC)
    http_request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/csv"})
    try:
        with urlopen(http_request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            body = response.read()
            status = response.status
    except HTTPError as error:
        raise BbkHTTPError(error.code, error.headers.get("Retry-After"), url) from error
    except URLError as error:
        # 타임아웃과 DNS·연결 실패는 재시도 가능한 오류로 올린다.
        raise ConnectionError(f"Bundesbank request failed: {error.reason}") from error

    return BbkResponse(
        request=request,
        body=body,
        status=status,
        started_at=started_at,
        completed_at=datetime.now(UTC),
    )


# 쿼리는 `sql/` 볼륨에 둔다. 배포 Airflow가 `/opt/airflow/sql`로 마운트하는 폴더다.
SOURCE_RECORD_INSERT = read_sql("postgres", "source_record", "insert.sql")
OBSERVATION_UPSERT = read_sql("postgres", "indicator_observation", "upsert.sql")


def store_observations(connection: Connection, response: BbkResponse) -> int:
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
    없기 때문이다. 컬럼 이름은 `tests/collectors/test_bbk.py`가 모델 metadata와 맞춰 둔다.
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
            "maturity_codes": list(MATURITY_CODES),
            "series_ids": list(BUND_SERIES),
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
