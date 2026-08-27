"""한국은행 ECOS에서 국내 시장금리(국고채·CD)를 수집한다.

배포 Airflow와 공유되는 폴더는 `airflow/dags`와 `airflow/modules` 둘뿐이다. Airflow는
`apps/`도 `core/`도 보지 못한다. 그래서 DAG가 실행 시점에 필요한 코드는 전부 여기 있어야
한다. `dags/`에는 스케줄과 오케스트레이션만 두고 수집 규칙은 이 모듈에 둔다.

의존성은 표준 라이브러리와 Pydantic, PEP 249 연결로 제한한다. SQLAlchemy 모델과
`core.config`는 import하지 않는다. 저장 대상 테이블의 정의는 백엔드의 `apps/models`가
원본이고, 여기 SQL의 컬럼 이름은 `tests/collectors/test_ecos.py`가 그 모델 metadata와
대조한다.

인증키는 `EcosCollector`가 쥔다. 항목·구간처럼 호출마다 바뀌는 것은 메서드 인자고,
파싱(`parse_observations`)은 키도 연결도 보지 않아 모듈 함수로 둔다.

`fred.py`와 같은 테이블에 쌓지만 API의 성질이 달라 다음 세 가지를 따로 다룬다.

- **오류가 HTTP 상태로 오지 않는다.** ECOS는 인증 실패도 데이터 없음도 HTTP 200에
  `{"RESULT": {"CODE": ...}}` 본문으로 답한다. 그래서 `EcosResultError`가 본문의 코드를
  담아 올라가고 재시도 여부는 DAG가 그 코드로 판단한다.
- **결측을 알리지 않는다.** 휴장일은 행 자체가 없고, 없는 항목코드를 물어도 데이터 없음과
  같은 `INFO-200`이 온다. 오타가 조용한 0건이 되지 않도록 항목코드는 `EcosSeries`가
  막는다.
- **행 수를 넘겨도 경고하지 않는다.** 요청한 범위보다 데이터가 많으면 앞부분만 돌려주므로
  `list_total_count`와 받은 행 수를 대조해 잘림을 실패로 만든다.

인증키는 URL 경로에 들어간다. FRED와 같은 이유로 예외 메시지와 로그에 URL을 넣지 않는다.

원본 응답은 `source_record`에, 유효 관측값은 `indicator_observation`에 저장한다. 두 쓰기는
호출자가 연 하나의 트랜잭션 안에서 실행되며 커밋과 롤백은 호출자가 결정한다.
"""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Self
from urllib.error import HTTPError, URLError
from urllib.parse import quote
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

from modules.db import Connection
from modules.sql import read_sql

ECOS_URL = "https://ecos.bok.or.kr/api/StatisticSearch"
SOURCE = "ecos"

# 통계표. 계열마다 다르므로 Enum이 들고 간다. 초판은 시장금리 하나뿐이라 모듈 상수였다.
MARKET_RATE_STAT_CODE = "817Y002"  # 1.3.2.1. 시장금리(일별)
POLICY_RATE_STAT_CODE = "722Y001"  # 1.3.1. 한국은행 기준금리 및 여수신금리
FOREIGN_POLICY_RATE_STAT_CODE = "902Y006"  # 9.1.1.3. 국제 주요국 중앙은행 정책금리

DAILY_CYCLE = "D"
MONTHLY_CYCLE = "M"


class EcosSeries(StrEnum):
    """수집 대상 시계열. 저장 식별자, ECOS 좌표, 주기, 단위, 종류, 한국어 이름을 한 줄에 묶는다.

    Enum 값은 `indicator_observation.series_id`에 그대로 저장한다. 항목코드
    (`010210000`)를 저장하면 DB와 대시보드에서 무슨 값인지 읽을 수 없다. 항목코드는
    `item_code`로 들고 있다가 요청 URL에만 쓰고 `source_record.metadata`에 남긴다.

    `item_code`는 그 통계표의 `StatisticItemList`가 실제로 돌려준 코드다. ECOS는 없는
    항목코드에도 데이터 없음(`INFO-200`)으로 답하므로 오타를 응답으로는 잡을 수 없다.
    그래서 여기에 없는 코드는 요청 전에 막는다. 시계열을 늘리려면 항목 목록을 다시 확인해
    여기에만 추가한다. 저장 계약은 `series_id`로 갈라지므로 그 밖의 코드 변경이 없다.

    **통계표·주기·단위 표기를 계열마다 단다.** 초판은 시장금리 하나뿐이라 모듈 상수 셋이었다.
    정책금리가 다른 통계표에 있고 일본은 월별이라 그 상수들이 거짓이 됐다. `fred.py`의
    `FredSeries`가 단위를 계열마다 다는 것과 같은 이유다.

    **월간 계열은 `_M`으로 끝난다.** 한 테이블에 일별과 월간이 섞여 있어 표시가 없으면
    조회하는 쪽이 주기를 구분할 수 없다.

    저장 컬럼은 `Text`로 둔다. `series_id`는 제공처마다 값 집합이 다른 열린 식별자라
    DB `CHECK` 제약을 걸면 시계열을 늘릴 때마다 제약을 다시 만들어야 한다. 허용 값은
    이 Enum이 막는다.
    """

    stat_code: str
    item_code: str
    cycle: str
    source_unit_name: str
    kind: str
    label: str

    def __new__(
        cls,
        series_id: str,
        stat_code: str,
        item_code: str,
        cycle: str,
        source_unit_name: str,
        kind: str,
        label: str,
    ) -> Self:
        member = str.__new__(cls, series_id)
        member._value_ = series_id
        member.stat_code = stat_code
        member.item_code = item_code
        member.cycle = cycle
        member.source_unit_name = source_unit_name
        member.kind = kind
        member.label = label
        return member

    @property
    def is_monthly(self) -> bool:
        return self.cycle == MONTHLY_CYCLE

    # 시장이 만드는 값. 통계표 하나에 주기 D, 단위 `연%`다.
    KTB_2Y = ("KTB2Y", MARKET_RATE_STAT_CODE, "010195000", DAILY_CYCLE, "연%", "government_bond", "국고채 2년")
    KTB_3Y = ("KTB3Y", MARKET_RATE_STAT_CODE, "010200000", DAILY_CYCLE, "연%", "government_bond", "국고채 3년")
    KTB_10Y = ("KTB10Y", MARKET_RATE_STAT_CODE, "010210000", DAILY_CYCLE, "연%", "government_bond", "국고채 10년")
    KTB_30Y = ("KTB30Y", MARKET_RATE_STAT_CODE, "010230000", DAILY_CYCLE, "연%", "government_bond", "국고채 30년")
    CD_91D = ("CD91D", MARKET_RATE_STAT_CODE, "010502000", DAILY_CYCLE, "연%", "money_market", "CD 91일")

    # 중앙은행이 정하는 값. 좌표·주기·단위는 2026-08-27에 `StatisticItemList`로 확인했다.
    #
    # 한국은행 기준금리는 **달력 하루도 빠짐없이** 채워 온다(주말 포함). 국채 계열과 날짜 축이
    # 같아 `KTB10Y - KRBASE`가 조인 하나다.
    #
    # **일본은 월별뿐이다.** 국제 정책금리 통계표(`902Y006`)에 국가 코드가 항목코드로 들어 있고
    # 주기는 M이다. FRED의 일별 대안(`IRSTCB01JPM156N`)은 2023-12에 끊겼다. 월별이라 일본에
    # 대해서는 발표일 전후 며칠을 보는 선반영 분석이 성립하지 않는다.
    KR_BASE = ("KRBASE", POLICY_RATE_STAT_CODE, "0101000", DAILY_CYCLE, "연%", "policy_rate", "한국은행 기준금리")
    JP_BASE_M = (
        "JPBASE_M",
        FOREIGN_POLICY_RATE_STAT_CODE,
        "JP",
        MONTHLY_CYCLE,
        "%",
        "policy_rate",
        "일본은행 정책금리(월별)",
    )


# DAG이 태스크를 매핑하는 단위. 시장금리는 일별로, 정책금리는 주별로 돈다.
MARKET_RATE_SERIES: tuple[str, ...] = tuple(series.value for series in EcosSeries if series.kind != "policy_rate")
POLICY_RATE_SERIES: tuple[str, ...] = tuple(series.value for series in EcosSeries if series.kind == "policy_rate")

# 저장 표기는 FRED와 맞춘다. 두 나라 금리를 한 쿼리로 비교하려면 단위 문자열이 같아야 한다.
SERIES_UNIT = "Percent"

# 데이터가 없다는 정상 응답. 휴장일만 걸린 구간이거나 아직 발표 전이다.
NO_DATA_CODE = "INFO-200"

# `TIME`은 구분자가 없다. 일별은 YYYYMMDD, 월별은 YYYYMM이다.
DAILY_TIME_LENGTH = 8
MONTHLY_TIME_LENGTH = 6

# ECOS가 한 번에 허용하는 최대 조회 건수. 넘겨 요청해도 오류가 아니라 앞부분만 돌아온다.
MAX_ROWS_PER_REQUEST = 100000

REQUEST_TIMEOUT_SECONDS = 30
class EcosHTTPError(RuntimeError):
    """ECOS가 2xx가 아닌 상태로 응답했다. 재시도 가능 여부는 호출자가 `status`로 판단한다."""

    def __init__(self, status: int, retry_after: str | None = None) -> None:
        super().__init__(f"ECOS request failed with HTTP {status}")
        self.status = status
        self.retry_after = retry_after


class EcosResultError(RuntimeError):
    """ECOS가 HTTP 200에 `RESULT` 본문으로 실패를 알렸다.

    인증키 오류(`INFO-100`)와 서버 오류(`ERROR-5xx`)가 같은 형태로 오므로 재시도 가능
    여부는 여기서 판단하지 않는다. 호출자가 `code`로 나눈다.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"ECOS request failed with {code}: {message}")
        self.code = code


class EcosPayloadError(ValueError):
    """응답이 JSON이 아니거나 `StatisticSearch` 계약을 지키지 않았다. 재시도해도 같은 결과다."""


class EcosRequest(BaseModel):
    """한 시계열의 한 기간을 요청하는 값. 호출 전에 여기서 전부 검증한다."""

    model_config = ConfigDict(frozen=True)

    series: EcosSeries
    observation_start: date
    observation_end: date

    @model_validator(mode="after")
    def require_ordered_period(self) -> Self:
        if self.observation_start > self.observation_end:
            raise ValueError("observation_start must not be after observation_end")
        return self

    @property
    def series_id(self) -> str:
        """저장에 쓰는 시계열 식별자. Enum 값이 그대로 `series_id`가 된다."""
        return self.series.value

    @property
    def item_code(self) -> str:
        """ECOS 항목코드. 요청 URL과 응답 대조에만 쓰고 저장하지 않는다."""
        return self.series.item_code

    @property
    def period_bounds(self) -> tuple[str, str]:
        """URL에 넣는 조회 구간 표기. 주기가 정한다.

        월별 통계표에 YYYYMMDD를 넘기면 ECOS는 오류가 아니라 데이터 없음(`INFO-200`)으로
        답한다. 조용한 0건이 되므로 주기별로 갈라 만든다.
        """
        if self.series.is_monthly:
            return self.observation_start.strftime("%Y%m"), self.observation_end.strftime("%Y%m")
        return self.observation_start.strftime("%Y%m%d"), self.observation_end.strftime("%Y%m%d")


class EcosObservation(BaseModel):
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


class EcosRawRow(BaseModel):
    """ECOS가 보낸 관측값 1행. 검증에 필요한 필드만 읽고 나머지는 버린다."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    item_code: str = Field(alias="ITEM_CODE1")
    item_name: str = Field(alias="ITEM_NAME1")
    unit_name: str = Field(alias="UNIT_NAME")
    time: str = Field(alias="TIME")
    value: str | None = Field(alias="DATA_VALUE", default=None)


def parse_time(text: str, cycle: str) -> date:
    """`TIME`을 날짜로 바꾼다. 요청한 주기의 표기만 받는다.

    ECOS의 `TIME`은 구분자가 없어 Pydantic의 date 파서가 읽지 못한다. 그리고 일별
    (`20260806`)과 월별(`202608`)이 같은 칸에 다른 길이로 온다. **요청한 주기가 아닌 표기를
    통과시키면 월별 값이 일별 관측값으로 저장된다.** 그래서 주기를 아는 자리에서 변환한다.

    월별 관측일은 **그 달의 1일**이다. `ecb_irs.py`·`fred.py`의 월간 계열과 같은 규약이라
    조회하는 쪽이 주기별로 다른 날짜 규칙을 알 필요가 없다.
    """
    if cycle == MONTHLY_CYCLE:
        if len(text) != MONTHLY_TIME_LENGTH or not text.isdigit():
            raise EcosPayloadError(f"monthly TIME must be YYYYMM, got {text!r}")
        day = "01"
    else:
        if len(text) != DAILY_TIME_LENGTH or not text.isdigit():
            raise EcosPayloadError(f"daily TIME must be YYYYMMDD, got {text!r}")
        day = text[6:8]

    try:
        return date(int(text[:4]), int(text[4:6]), int(day))
    except ValueError as error:
        # 길이와 숫자 여부만 보면 `20261331` 같은 값이 통과한다.
        raise EcosPayloadError(f"TIME {text!r} is not a real calendar date") from error


class EcosStatisticSearch(BaseModel):
    """`StatisticSearch` 응답의 본문."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    list_total_count: int
    row: tuple[EcosRawRow, ...]


class EcosSearchPayload(BaseModel):
    """데이터가 있을 때의 응답 최상위."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    statistic_search: EcosStatisticSearch = Field(alias="StatisticSearch")


class EcosResult(BaseModel):
    """`RESULT` 본문. 정상 조회가 아닐 때만 온다."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    code: str = Field(alias="CODE")
    message: str = Field(alias="MESSAGE")


class EcosResultPayload(BaseModel):
    """데이터가 없거나 요청이 거절됐을 때의 응답 최상위."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    result: EcosResult = Field(alias="RESULT")


class EcosResponse(BaseModel):
    """한 번의 ECOS 호출 결과와 그 호출을 재현하는 데 필요한 메타데이터."""

    model_config = ConfigDict(frozen=True)

    request: EcosRequest
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


def parse_observations(body: bytes, request: EcosRequest) -> tuple[EcosObservation, ...]:
    """유효 관측값만 뽑는다.

    데이터 없음(`INFO-200`)은 실패가 아니라 빈 결과다. 그 밖의 `RESULT` 코드는 그대로
    올려 DAG가 재시도 여부를 정하게 한다. 형식이 깨졌거나 응답이 잘렸으면 전체를 실패시킨다.
    """
    try:
        document = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise EcosPayloadError("ECOS response is not JSON") from error

    if not isinstance(document, dict):
        raise EcosPayloadError("ECOS response is not a JSON object")

    if "RESULT" in document:
        try:
            result = EcosResultPayload.model_validate(document).result
        except ValidationError as error:
            raise EcosPayloadError("ECOS response is not a valid RESULT payload") from error
        if result.code == NO_DATA_CODE:
            return ()
        raise EcosResultError(result.code, result.message)

    try:
        search = EcosSearchPayload.model_validate(document).statistic_search
    except ValidationError as error:
        raise EcosPayloadError("ECOS response is not a valid StatisticSearch payload") from error

    if search.list_total_count > len(search.row):
        # ECOS는 요청한 건수 범위를 넘는 데이터를 경고 없이 잘라 앞부분만 돌려준다.
        # 그대로 저장하면 구간에 조용히 구멍이 남는다.
        raise EcosPayloadError(
            f"ECOS returned {len(search.row)} of {search.list_total_count} rows; widen the requested row range"
        )

    series = request.series
    observations: list[EcosObservation] = []
    for row in search.row:
        if row.item_code != request.item_code:
            # 항목코드가 요청과 다르면 값이 엉뚱한 시계열로 저장된다.
            raise EcosPayloadError(f"ECOS returned item {row.item_code!r} for a request of {request.item_code!r}")
        if row.unit_name != series.source_unit_name:
            raise EcosPayloadError(f"ECOS changed the unit of {row.item_code} to {row.unit_name!r}")
        if not row.value:
            continue
        observation_date = parse_time(row.time, series.cycle)
        try:
            observations.append(EcosObservation(observation_date=observation_date, value=Decimal(row.value)))
        except (ValidationError, ArithmeticError) as error:
            raise EcosPayloadError(f"ECOS returned a non-numeric value for {row.item_code}") from error

    return tuple(observations)


# 쿼리는 `sql/` 볼륨에 둔다. 배포 Airflow가 `/opt/airflow/sql`로 마운트하는 폴더다.
SOURCE_RECORD_INSERT = read_sql("postgres", "source_record", "insert.sql")
OBSERVATION_UPSERT = read_sql("postgres", "indicator_observation", "upsert.sql")


class EcosCollector:
    """ECOS 수집기. 인증키를 쥐고 계열마다 조회·저장한다.

    한 실행이 객체 하나다. 키는 이 객체가 사는 동안 안 변하는 값이라 생성자가 받고, 항목과
    구간은 `EcosRequest`로 호출마다 들어온다. 파싱은 밖에 둔다(`parse_observations`).
    """

    def __init__(self, api_key: SecretStr) -> None:
        if not api_key.get_secret_value():
            raise ValueError("ECOS API key is required")
        self._api_key = api_key

    def build_url(self, request: EcosRequest) -> str:
        """호출 URL. 반환값에 인증키가 들어가므로 로그나 예외 메시지에 넣지 않는다.

        ECOS는 질의 문자열이 아니라 경로 조각으로 인자를 받는다. 순서는
        `인증키/형식/언어/시작건수/종료건수/통계표/주기/시작일자/종료일자/항목코드`다.
        """
        return "/".join(
            (
                ECOS_URL,
                quote(self._api_key.get_secret_value(), safe=""),
                "json",
                "kr",
                "1",
                str(MAX_ROWS_PER_REQUEST),
                request.series.stat_code,
                request.series.cycle,
                *request.period_bounds,
                request.item_code,
            )
        )

    def fetch_series(self, request: EcosRequest) -> EcosResponse:
        url = self.build_url(request)
        started_at = datetime.now(UTC)
        try:
            with urlopen(url, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                body = response.read()
                status = response.status
        # `from None`은 여기서 의도적이다. URL에 인증키가 들어 있고 `HTTPError`는 그 URL을
        # `filename`에 담는다. 체인을 남기면 Sentry나 Airflow 로그가 원인 예외를 붙잡을 때
        # 키가 함께 실린다. fred 수집기가 같은 이유로 같은 처리를 한다.
        except HTTPError as error:
            raise EcosHTTPError(error.code, error.headers.get("Retry-After")) from None
        except URLError as error:
            # 타임아웃과 DNS·연결 실패는 재시도 가능한 오류로 올린다.
            raise ConnectionError(f"ECOS request failed: {error.reason}") from None

        return EcosResponse(
            request=request,
            body=body,
            status=status,
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )

    def store_observations(self, connection: Connection, response: EcosResponse) -> int:
        """원본 1건과 유효 관측값을 저장하고 정규화한 관측값 수를 돌려준다.

        파싱을 먼저 해서 형식 오류면 아무 것도 쓰지 않는다. `(provider, series_id,
        observation_date)`가 멱등 키라서 같은 기간을 다시 수집해도 행이 늘지 않고 최신 값으로
        갱신된다. `series_id`는 제공처 안에서만 고유하므로 이 수집기의 `provider`는 항상
        `SOURCE`다.

        관측값이 0건이어도 `source_record`는 남긴다. 휴장일 구간을 실제로 조회했다는 사실이
        없으면 아직 수집하지 않은 구간과 구분되지 않는다.

        ORM 대신 문자열 SQL을 쓴다. Airflow 이미지에는 SQLAlchemy와 이 프로젝트의 DB 설정이
        없기 때문이다. 컬럼 이름은 `tests/collectors/test_ecos.py`가 모델 metadata와 맞춰 둔다.
        """
        observations = parse_observations(response.body, response.request)
        request_metadata = json.dumps(
            {
                "http_status": response.status,
                # 저장하는 `series_id`는 읽을 수 있는 ID다. 그 값이 ECOS의 어느 시계열에서 왔는지는
                # 통계표 코드와 항목코드가 있어야 되짚을 수 있으므로 여기 남긴다.
                "stat_code": response.request.series.stat_code,
                "item_code": response.request.item_code,
                "item_name": response.request.series.label,
                "cycle": response.request.series.cycle,
                "source_unit_name": response.request.series.source_unit_name,
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
                        SOURCE,
                        response.series_id,
                        observation.observation_date,
                        observation.value,
                        SERIES_UNIT,
                        source_record_id,
                    ),
                )
        return len(observations)
