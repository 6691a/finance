"""독일 분데스방크(Bundesbank)에서 중앙은행 대차대조표 총자산을 수집한다.

배포 Airflow와 공유되는 폴더는 `airflow/dags`와 `airflow/modules` 둘뿐이다. Airflow는
`apps/`도 `core/`도 보지 못한다. 그래서 DAG가 실행 시점에 필요한 코드는 전부 여기 있어야
한다. `dags/`에는 스케줄과 오케스트레이션만 두고 수집 규칙은 이 모듈에 둔다.

의존성은 표준 라이브러리와 Pydantic, PEP 249 연결로 제한한다. SQLAlchemy 모델과
`core.config`는 import하지 않는다. 저장 대상 테이블의 정의는 백엔드의 `apps/models`가
원본이고, 여기 SQL의 컬럼 이름은 `tests/collectors/test_bbk_statement.py`가 그 모델
metadata와 대조한다.

받는 곳은 분데스방크 통계 API(`api.statistiken.bundesbank.de`)의 `BBBK11`
(Wochenausweis / Weekly financial statement) 데이터셋이다. 인증이 없고 URL에 비밀이 실리지
않으므로 `bbk.py`, `mof.py`, `boe.py`, `ecb.py`와 마찬가지로 예외 메시지와 로그에 URL을
그대로 남긴다.

## 왜 `bbk.py`와 다른 모듈인가

같은 호스트를 쓰지만 그것 말고 겹치는 것이 없다. `bbk.py`는 `BBSIS`(금리구조)의 만기
차원을 `+`로 묶어 곡선 하나를 받고, 이 모듈은 `BBBK11`의 계정 코드를 받는다. 데이터셋이
다르면 SDMX 키의 차원 구성이 다르고, 그러면 키를 만드는 코드도 헤더를 대조하는 코드도
공유할 것이 없다. 한 모듈에 두 데이터셋을 두면 키 조립이 데이터셋별 분기가 된다.

## 주기가 `D`인데 값은 주 1회다

`BBBK11`의 빈도 차원은 일별(`D`)이지만 실제 값은 금요일 잔액 하나뿐이고 나머지 날짜는
결측(`.`)에 `No value available` 플래그가 붙어 온다. 결측 행을 저장하지 않으므로
관측일은 자연히 금요일만 남는다. 저장 식별자를 `_W`로 끝내는 근거가 응답의 빈도 차원이
아니라 이 실제 간격이다.

    "",BBBK11.D.TTA032,BBBK11.D.TTA032_FLAGS
    "",Total assets / unadjusted / Deutsche Bundesbank,
    BBK_UNIT_ENG,,
    Decimals,0,
    Time format code,P1D,
    category,BABA11,
    unit multiplier,Millions,
    last update,2026-08-26 11:44:47,
    2026-08-14,2272143,
    2026-08-21,2265320,

`lang=en`을 붙여야 구분자가 `,`이고 소수점이 `.`다. 붙이지 않으면 독일어 표기라 구분자가
`;`가 되어 파싱이 통째로 어긋난다. 인코딩은 BOM 붙은 UTF-8이다. `bbk.py`와 같은 규칙이다.

## 배수 표기를 매 응답 대조한다

`unit multiplier` 줄이 `Millions`다. 이 값이 `Billions`로 바뀌면 같은 숫자가 1000배가
되는데 응답 형식은 그대로라 아무 것도 실패하지 않는다. 조용히 자릿수가 어긋나는 대신
멈춘다. 통화 표기(`BBK_UNIT_ENG`)는 영어 응답에서 빈 칸으로 와서 대조할 것이 없다 —
독일어 응답에만 `EURO`가 들어 있고, 그것 하나 때문에 구분자가 `;`인 응답을 파싱할 값어치는
없다.

기준일은 독일 영업일이며 제공처 기준을 그대로 보존한다. 저장하는 시각(`started_at`,
`completed_at`)은 UTC다.

**단위를 유로로 그대로 저장한다.** 한 통화로 환산하면 환율 변동이 자산 증감으로 위장한다.
나라 사이 비교는 잔액이 아니라 증가율로 한다.

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

BBK_URL = "https://api.statistiken.bundesbank.de/rest/data"

# 제공처는 `bbk.py`와 같다. 같은 기관이 준 값이므로 `provider`가 갈리면 안 된다.
SOURCE = "bbk"

# 데이터셋. `BBBK11`이 주간 재무제표(Wochenausweis)다.
DATASET = "BBBK11"

# 시계열 키의 빈도 차원. 값은 주 1회지만 데이터셋이 스스로를 일별로 부른다(위 docstring).
FREQUENCY = "D"

# BOM이 붙어 온다. 떼지 않으면 헤더 첫 칸 대조부터 어긋난다.
ENCODING = "utf-8-sig"

USER_AGENT = "news-collector/1.0 (+https://www.bundesbank.de/en/statistics)"

REQUEST_TIMEOUT_SECONDS = 30

# 제공처 표기와 저장 표기. 금리가 아니라 잔액이라 `Percent`가 아니다.
SOURCE_UNIT_NAME = "Millions"
SERIES_UNIT = "Millions of Euros"


class StatementSeries(StrEnum):
    """수집 대상 시계열. 저장 식별자, `BBBK11` 계정 코드, 종류, 한국어 이름을 한 줄에 묶는다.

    코드는 2026-08-27에 데이터셋 전체를 훑어 `BBK_TITLE_ENG`로 확인했다. `TTA032`가
    분데스방크 자신의 총자산이고, 같은 데이터셋의 `TTA082`가 유로시스템 총자산이다.

    **유로시스템은 여기서 받지 않는다.** 값은 FRED `ECBASSETSW`와 글자 그대로 같았지만
    (2026-08-21에 둘 다 5,913,041), 여기서 받으면 `provider`가 `bbk`가 되어 "유로 지역
    값인데 제공처가 독일"로 읽힌다. 유로 지역은 `fred.FredSeries.EA_ASSETS_W`가 받는다.

    **주간 계열은 `_W`로 끝난다.** 한 테이블에 일별·주간·월간이 섞여 있어 표시가 없으면
    조회하는 쪽이 주기를 구분할 수 없다.

    Enum 값은 `indicator_observation.series_id`에 그대로 저장한다. 시계열을 늘리려면
    여기와 `reference.indicator_series` 시드를 같은 커밋에서 함께 늘린다. 관측값에서
    마스터로 외래키를 걸지 않으므로 어긋나면 DAG가 아니라 대시보드가 조용히 빈다.
    `tests/migrations/test_indicator_series_catalog.py`가 둘을 대조한다.
    """

    bbk_code: str
    kind: str
    label: str

    def __new__(cls, series_id: str, bbk_code: str, kind: str, label: str) -> Self:
        member = str.__new__(cls, series_id)
        member._value_ = series_id
        member.bbk_code = bbk_code
        member.kind = kind
        member.label = label
        return member

    DE_ASSETS_W = ("DEASSETS_W", "TTA032", "balance_sheet", "분데스방크 총자산(주간)")

    @property
    def series_key(self) -> str:
        """SDMX 시계열 키. 요청에도 쓰고 응답 헤더 대조에도 쓴다."""
        return f"{FREQUENCY}.{self.bbk_code}"

    @property
    def column_name(self) -> str:
        """응답 헤더에 나오는 값 열 이름. 데이터셋 이름이 앞에 붙는다."""
        return f"{DATASET}.{self.series_key}"


BALANCE_SHEET_SERIES: tuple[str, ...] = tuple(series.value for series in StatementSeries)

# 요청 키의 계정 차원. `+`가 SDMX의 OR다. 지금은 하나뿐이지만 계열을 늘려도 요청은 하나다.
STATEMENT_CODES: tuple[str, ...] = tuple(series.bbk_code for series in StatementSeries)

# 수집 단위는 시계열이 아니라 조회 한 번이다. `source_record.source_key`에 이 값을 넣는다.
SOURCE_KEY = f"{DATASET}.{FREQUENCY}.{'+'.join(STATEMENT_CODES)}"

# 응답 헤더의 값 열 이름에서 저장 시계열로 되짚는 표.
SERIES_BY_COLUMN: dict[str, StatementSeries] = {series.column_name: series for series in StatementSeries}

# 헤더 첫 칸. 날짜 열에는 이름이 없다. 원문에는 `""`로 적혀 있지만 그건 CSV의 빈 문자열
# 표기라서 `csv.reader`를 거치면 따옴표가 벗겨져 빈 문자열이 된다.
DATE_COLUMN = ""

# 값 열마다 따라붙는 상태 열. 값이 아니므로 읽지 않는다.
FLAGS_SUFFIX = "_FLAGS"

# 배수 표기를 담고 있는 메타데이터 줄의 첫 칸. 이 값이 바뀌면 자릿수가 조용히 어긋난다.
UNIT_MULTIPLIER_LABEL = "unit multiplier"

# 데이터 줄인지 가르는 모양. 앞의 몇 줄은 제목·소수점 자릿수·최종 갱신 시각 같은 메타데이터다.
ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 그날 값이 없는 칸. 주간 잔액이라 대부분의 날짜가 여기 걸린다.
MISSING_VALUES = frozenset({"", ".", "-"})


class BbkStatementHTTPError(RuntimeError):
    """분데스방크가 2xx가 아닌 상태로 응답했다. 재시도 가능 여부는 호출자가 `status`로 판단한다."""

    def __init__(self, status: int, retry_after: str | None = None, url: str | None = None) -> None:
        # 인증이 없어 URL에 비밀이 없다. 그대로 남긴다.
        super().__init__(f"Bundesbank statement request failed with HTTP {status}: {url}")
        self.status = status
        self.retry_after = retry_after


class BbkStatementPayloadError(ValueError):
    """응답이 계약을 지키지 않았다. 재시도해도 같은 결과다."""


class StatementRequest(BaseModel):
    """한 번의 수집이 저장할 관측 구간."""

    model_config = ConfigDict(frozen=True)

    observation_start: date
    observation_end: date

    @model_validator(mode="after")
    def require_ordered_period(self) -> Self:
        if self.observation_start > self.observation_end:
            raise ValueError("observation_start must not be after observation_end")
        return self


class StatementObservation(BaseModel):
    """정규화한 관측값 1건."""

    model_config = ConfigDict(frozen=True)

    series: StatementSeries
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


class StatementSnapshot(BaseModel):
    """응답 하나를 정규화한 결과.

    관측값과 함께 응답이 실제로 덮은 구간을 들고 있다. 저장된 0건이 "값이 없는 구간"인지
    "응답이 담지 않은 구간"인지는 이 값으로만 갈린다.
    """

    model_config = ConfigDict(frozen=True)

    observations: tuple[StatementObservation, ...]
    response_first_date: date | None
    response_last_date: date | None
    response_row_count: int


class StatementResponse(BaseModel):
    """한 번의 호출 결과와 그 호출을 재현하는 데 필요한 메타데이터."""

    model_config = ConfigDict(frozen=True)

    request: StatementRequest
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
    """계정 코드를 한꺼번에 무는 SDMX 시계열 키. 계정 차원만 `+`로 이어 붙인다."""
    return f"{FREQUENCY}.{'+'.join(STATEMENT_CODES)}"


def build_url(request: StatementRequest) -> str:
    query = urlencode(
        {
            "startPeriod": request.observation_start.isoformat(),
            "endPeriod": request.observation_end.isoformat(),
            "format": "csv",
            # 이걸 빼면 독일어 표기가 온다. 구분자가 `;`라 파싱이 통째로 어긋난다.
            "lang": "en",
        }
    )
    return f"{BBK_URL}/{DATASET}/{build_series_key()}?{query}"


def _column_indexes(header: Sequence[str]) -> dict[StatementSeries, int]:
    """헤더를 검증하고 시계열마다 값 열의 위치를 돌려준다.

    위치가 아니라 이름으로 묶는다. 값 열과 `_FLAGS` 열이 번갈아 오고 계열을 늘리면 순서가
    바뀔 수 있어서, 위치로 세면 값이 옆 칸으로 밀린 것을 못 잡는다.
    """
    cells = [cell.strip() for cell in header]
    if not cells or cells[0] != DATE_COLUMN:
        raise BbkStatementPayloadError(f"Bundesbank response does not start with the date column: {cells[:1]!r}")

    indexes: dict[StatementSeries, int] = {}
    for position, name in enumerate(cells[1:], start=1):
        if name.endswith(FLAGS_SUFFIX):
            continue
        series = SERIES_BY_COLUMN.get(name)
        if series is None:
            raise BbkStatementPayloadError(f"Bundesbank returned an unrequested series column {name!r}")
        indexes[series] = position

    missing = [series.value for series in StatementSeries if series not in indexes]
    if missing:
        raise BbkStatementPayloadError(f"Bundesbank response is missing the columns for {missing!r}")
    return indexes


def _require_unit_multiplier(rows: Sequence[Sequence[str]]) -> None:
    """배수 표기가 그대로인지 본다.

    `Millions`가 `Billions`로 바뀌면 같은 숫자가 1000배가 되는데 응답 형식은 그대로라
    아무 것도 실패하지 않는다. 조용히 자릿수가 어긋나는 대신 멈춘다.
    """
    for row in rows:
        if row and row[0].strip() == UNIT_MULTIPLIER_LABEL:
            reported = row[1].strip() if len(row) > 1 else ""
            if reported != SOURCE_UNIT_NAME:
                raise BbkStatementPayloadError(
                    f"Bundesbank changed the unit multiplier to {reported!r}, expected {SOURCE_UNIT_NAME!r}"
                )
            return
    raise BbkStatementPayloadError(f"Bundesbank response has no {UNIT_MULTIPLIER_LABEL!r} row")


def _rows(body: bytes) -> tuple[dict[StatementSeries, int], list[list[str]]]:
    """헤더와 배수 표기를 검증하고 값 열 위치와 데이터 줄만 돌려준다.

    앞의 몇 줄은 제목과 소수점 자릿수 같은 메타데이터다. 첫 칸이 ISO 날짜인 줄만 데이터로
    본다. 데이터 줄이 하나도 없으면 요청 구간에 고시가 없었다는 뜻이라 빈 목록이다.
    """
    try:
        text = body.decode(ENCODING)
    except UnicodeDecodeError as error:
        raise BbkStatementPayloadError(f"Bundesbank response is not {ENCODING} text") from error

    lines = [row for row in csv.reader(io.StringIO(text)) if row]
    if not lines:
        raise BbkStatementPayloadError("Bundesbank returned an empty body")

    indexes = _column_indexes(lines[0])
    column_count = len(lines[0])
    _require_unit_multiplier(lines[1:])

    rows: list[list[str]] = []
    for line in lines[1:]:
        if not ISO_DATE_PATTERN.match(line[0].strip()):
            # 제목, 소수점 자릿수, 최종 갱신 시각 같은 메타데이터 줄이다.
            continue
        if len(line) != column_count:
            raise BbkStatementPayloadError(f"Bundesbank row has {len(line)} cells, expected {column_count}: {line!r}")
        rows.append(line)
    return indexes, rows


def parse_snapshot(body: bytes, request: StatementRequest) -> StatementSnapshot:
    """요청 구간에 드는 유효 관측값과 응답이 덮은 구간을 뽑는다."""
    indexes, rows = _rows(body)

    observations: list[StatementObservation] = []
    observation_dates: list[date] = []

    for row in rows:
        observation_date = date.fromisoformat(row[0].strip())
        observation_dates.append(observation_date)
        if not request.observation_start <= observation_date <= request.observation_end:
            continue

        for series, index in indexes.items():
            cell = row[index].strip()
            if cell in MISSING_VALUES:
                # 그날 고시가 없다. 주간 잔액이라 대부분의 날짜가 여기 걸린다.
                continue
            try:
                observations.append(
                    StatementObservation(series=series, observation_date=observation_date, value=Decimal(cell))
                )
            except (ValueError, InvalidOperation) as error:
                raise BbkStatementPayloadError(
                    f"Bundesbank returned a non-numeric value {cell!r} for {series.value}"
                ) from error

    return StatementSnapshot(
        observations=tuple(observations),
        response_first_date=min(observation_dates, default=None),
        response_last_date=max(observation_dates, default=None),
        response_row_count=len(observation_dates),
    )


def parse_observations(body: bytes, request: StatementRequest) -> tuple[StatementObservation, ...]:
    """`parse_snapshot`의 관측값만. 다른 수집기와 이름을 맞추려고 둔다."""
    return parse_snapshot(body, request).observations


def fetch_statement(request: StatementRequest) -> StatementResponse:
    """계정 전체를 한 번에 받는다."""
    url = build_url(request)
    started_at = datetime.now(UTC)
    http_request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/csv"})
    try:
        with urlopen(http_request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            body = response.read()
            status = response.status
    except HTTPError as error:
        raise BbkStatementHTTPError(error.code, error.headers.get("Retry-After"), url) from error
    except URLError as error:
        # 타임아웃과 DNS·연결 실패는 재시도 가능한 오류로 올린다.
        raise ConnectionError(f"Bundesbank statement request failed: {error.reason}") from error

    return StatementResponse(
        request=request,
        body=body,
        status=status,
        started_at=started_at,
        completed_at=datetime.now(UTC),
    )


# 쿼리는 `sql/` 볼륨에 둔다. 배포 Airflow가 `/opt/airflow/sql`로 마운트하는 폴더다.
SOURCE_RECORD_INSERT = read_sql("postgres", "source_record", "insert.sql")
OBSERVATION_UPSERT = read_sql("postgres", "indicator_observation", "upsert.sql")


def store_observations(connection: Connection, response: StatementResponse) -> int:
    """조회 1건과 그 응답에서 나온 유효 관측값을 저장하고 관측값 수를 돌려준다.

    파싱을 먼저 해서 형식 오류면 아무 것도 쓰지 않는다. `(provider, series_id,
    observation_date)`가 멱등 키라서 같은 기간을 다시 수집해도 행이 늘지 않고 최신 값으로
    갱신된다. `series_id`는 제공처 안에서만 고유하므로 이 수집기의 `provider`는 항상 `SOURCE`다.

    `payload`는 비운다. 원본이 CSV인데 컬럼 타입이 jsonb다. 어느 구간을 물어 어느 구간이
    돌아왔는지는 `metadata`가 남긴다.

    관측값이 0건이어도 `source_record`는 남긴다. 고시가 없는 구간을 실제로 조회했다는 사실이
    없으면 아직 수집하지 않은 구간과 구분되지 않는다.

    ORM 대신 문자열 SQL을 쓴다. Airflow 이미지에는 SQLAlchemy와 이 프로젝트의 DB 설정이
    없기 때문이다. 컬럼 이름은 `tests/collectors/test_bbk_statement.py`가 모델 metadata와
    맞춰 둔다.
    """
    request = response.request
    snapshot = parse_snapshot(response.body, request)
    request_metadata = json.dumps(
        {
            "http_status": response.status,
            "url": build_url(request),
            "source_unit_name": SOURCE_UNIT_NAME,
            "observation_start": request.observation_start.isoformat(),
            "observation_end": request.observation_end.isoformat(),
            # 응답이 실제로 덮은 구간. 0건이 값 없음인지 구간 밖인지를 여기서 되짚는다.
            "response_first_date": (snapshot.response_first_date.isoformat() if snapshot.response_first_date else None),
            "response_last_date": (snapshot.response_last_date.isoformat() if snapshot.response_last_date else None),
            "response_row_count": snapshot.response_row_count,
            "series_codes": list(STATEMENT_CODES),
            "series_ids": list(BALANCE_SHEET_SERIES),
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
                len(snapshot.observations),
                None,
                request_metadata,
            ),
        )
        source_record_id = cursor.fetchone()[0]
        for observation in snapshot.observations:
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
    return len(snapshot.observations)
