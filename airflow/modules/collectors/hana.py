"""하나은행 고시 환율(회차별)을 수집한다.

`modules/crawling/hana.py`의 옛 구현을 대체한다. 옛 코드는 aiohttp + BeautifulSoup으로
표를 읽고 f-string으로 만든 INSERT 문을 실행했다. 이 모듈은 요청·응답·정규화 결과를
모두 Pydantic으로 검증하고, 저장은 자연키 기준 upsert 하나로 끝낸다. 옛 파일은 지웠다.
쓰는 DAG가 없는데 두면 Airflow 환경에 없는 의존성(aiohttp, bs4, pytz)을 계속 끌고 간다.
필요하면 git 이력에서 꺼낸다.

옛 구현과 달라진 점이 두 가지 있다. 둘 다 옛 코드가 잘못된 값을 저장하던 부분이다.

1. 열 위치. 하나은행 표는 현재 11칸이고 `매매 기준율`은 8번째 칸이다.
   옛 코드는 7번째 칸(`외화수표 파실 때`)을 매매 기준율로 저장했다.
2. 날짜 경계. 한 고시일자의 회차는 KST 08:25쯤 1회차로 시작해 자정을 넘겨 다음 날
   새벽까지 이어진다. 옛 코드는 모든 회차에 조회 일자를 그대로 붙여서 자정 이후 회차의
   날짜가 하루 밀렸다. 여기서는 회차를 오름차순으로 훑다가 시각이 되감기면 하루를 더한다.

수집은 HTTP 폼 POST 한 번이다. 브라우저를 띄울 이유가 없어 scrapling의 `Fetcher`
(curl_cffi 기반)를 쓴다. `impersonate`로 실제 크롬의 TLS·HTTP2 지문을 흉내 내므로
은행 앞단 WAF가 기본 파이썬 클라이언트를 막아도 통과한다. 파싱은 같은 패키지의
`Selector`가 한다.

시각은 원본이 KST다. 비교·저장용으로 UTC로 정규화한다. `exchange_rate`는 시간대 없는
`date`/`time` 두 칸으로 쪼개져 있다. 외부 finance DB의 같은 테이블을 복사한 형태라서 그렇고,
그쪽에 쌓인 데이터와 같은 규칙으로 UTC 값을 넣는다.

이 모듈은 **어느 DB에 넣을지 모른다.** 호출자가 연 PEP 249 연결에 쓸 뿐이고, 대상은
`airflow/dags/exchange_rate_daily.py`의 연결 ID가 정한다. 배포 시 저장 위치를 바꾸는 자리는
그 DAG의 docstring에 정리해 뒀다.

`core.config`와 SQLAlchemy는 import하지 않는다. 저장은 PEP 249 연결과
`sql/postgres/exchange_rate/upsert.sql`로 한다. 컬럼 이름은 `tests/collectors/test_hana.py`가
모델 metadata와 대조한다.

수집 가능한 고시일자는 `EARLIEST_QUOTATION_DATE`부터 KST 오늘까지다. 고시일자 하나가
통화당 1500행 가까이 되기 때문에 범위를 열어 두면 백필 한 번이 수백만 행이 된다.
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Protocol, Self
from zoneinfo import ZoneInfo

from curl_cffi.curl import CurlError
from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError, field_validator, model_validator
from scrapling import Selector
from scrapling.fetchers import Fetcher

from modules.sql import read_sql

try:
    # psycopg2 전용 고속 경로. 이 모듈의 필수 의존성은 아니라서 없으면 `None`으로 두고
    # `_execute_upserts`가 PEP 249 `executemany`로 물러선다.
    from psycopg2.extensions import cursor as _Psycopg2Cursor
    from psycopg2.extras import execute_batch as _execute_batch
except ImportError:  # pragma: no cover - Airflow 이미지에는 psycopg2가 항상 있다
    _Psycopg2Cursor = None
    _execute_batch = None

HANA_URL = "https://www.kebhana.com/cms/rate/wpfxd651_07i_01.do"

KST = ZoneInfo("Asia/Seoul")

# 고시 구분 코드. 0은 회차별 고시다.
PUBLICATION_DIVISION = "0"

REQUEST_TIMEOUT_SECONDS = 30

# curl_cffi가 흉내 낼 브라우저. 은행 앞단이 기본 파이썬 TLS 지문을 막는다.
IMPERSONATE = "chrome"

# 수집을 허용하는 가장 이른 고시일자. DAG의 `start_date`와 같은 날이고, 그쪽이 잘못 바뀌어도
# 여기서 한 번 더 막는다. 더 과거를 편입하려면 이 값과 `start_date`를 함께 옮긴다.
EARLIEST_QUOTATION_DATE = date(2026, 7, 1)

# XHR 응답만 돌려주는 내부 엔드포인트라 이 두 헤더가 없으면 빈 문서가 온다.
REQUEST_HEADERS: dict[str, str] = {
    "Referer": "https://www.kebhana.com/cms/rate/index.do?contentUrl=/cms/rate/wpfxd651_07i.do",
    "X-Requested-With": "XMLHttpRequest",
}


class HanaCurrency(StrEnum):
    """수집 대상 통화. 하나은행 `curCd` 코드와 값이 같다."""

    USD = "USD"  # 미국 달러
    JPY = "JPY"  # 일본 엔(100엔 단위 고시)
    CNY = "CNY"  # 중국 위안
    EUR = "EUR"  # 유럽 유로
    HKD = "HKD"  # 홍콩 달러
    TWD = "TWD"  # 대만 달러
    GBP = "GBP"  # 영국 파운드
    AUD = "AUD"  # 호주 달러
    CAD = "CAD"  # 캐나다 달러
    RUB = "RUB"  # 러시아 루블


# 표의 칸 순서. thead는 `현찰`, `송금`이 colspan으로 묶여 있어 헤더 텍스트와 칸 수가 다르다.
# 그래서 헤더가 아니라 위치로 읽는다. 위치가 바뀌면 `EXPECTED_CELL_COUNT`가 먼저 걸린다.
ROUND_CELL = 0
TIME_CELL = 1
BUY_CELL = 2  # 현찰 사실 때
SELL_CELL = 3  # 현찰 파실 때
SEND_CELL = 4  # 송금 보낼 때
RECEIVE_CELL = 5  # 송금 받을 때
STANDARD_CELL = 7  # 매매 기준율. 6번은 `외화수표 파실 때`다.

# 회차, 시간, 현찰 2칸, 송금 2칸, 외화수표, 매매기준율, 직전대비, 환가료율, 미화환산율.
EXPECTED_CELL_COUNT = 11

# 한 고시일자가 자정을 넘는 최대 횟수. 평일 고시는 08:25에 시작해 다음 날 새벽에 끝나 한 번이다.
# 금요일 고시는 다음 영업일 개장까지 이어져 토·일요일 자정을 함께 넘는다(실측: 2026-07-10 USD
# 1,696회차, 금 08:24 ~ 일 06:57). 연휴가 붙으면 더 늘어난다. 상한은 회차와 시각이 아예 어긋난
# 표를 걸러 내려고 두는 것이라 최장 연휴에 맞춰 잡는다.
MAX_DAY_OFFSET = 6

# upsert를 한 번에 보낼 행 수. 고시일자 하나가 통화당 1500행 가까이 되고, DB가 원격이면
# 행마다 왕복하는 비용이 그대로 곱해진다.
UPSERT_PAGE_SIZE = 500


class Cursor(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(self, *args: object) -> bool | None: ...

    def execute(self, statement: str, parameters: Sequence[Any]) -> object: ...

    def executemany(self, statement: str, parameters: Sequence[Sequence[Any]]) -> object: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...


class HanaHTTPError(RuntimeError):
    """하나은행이 2xx가 아닌 상태로 응답했다. 재시도 가능 여부는 호출자가 `status`로 판단한다."""

    def __init__(self, status: int) -> None:
        super().__init__(f"Hana request failed with HTTP {status}")
        self.status = status


class HanaPayloadError(ValueError):
    """응답 HTML이 기대한 표 구조가 아니다. 재시도해도 같은 결과다."""


def latest_quotation_date() -> date:
    """수집 가능한 가장 늦은 고시일자, 곧 KST 오늘.

    하나은행 고시일자는 KST 기준이다. UTC 오늘을 쓰면 KST 00:00~09:00 사이에 정상 고시일자를
    하루 잘못 막는다.

    `HanaRateRequest` 검증이 이 함수를 호출한다. 벽시계를 모델 안에서 직접 읽지 않고 여기로
    빼 두면 테스트가 이 함수만 바꿔서 경계를 결정적으로 확인할 수 있다.
    """
    return datetime.now(KST).date()


class HanaRateRequest(BaseModel):
    """한 통화의 한 고시일자를 요청하는 값. 호출 전에 여기서 전부 검증한다."""

    model_config = ConfigDict(frozen=True)

    currency: HanaCurrency
    # 하나은행이 쓰는 고시일자. UTC가 아니라 KST 날짜다.
    quotation_date: date

    @field_validator("quotation_date")
    @classmethod
    def require_supported_quotation_date(cls, quotation_date: date) -> date:
        # 고시일자 하나가 통화당 1500행 가까이 된다. 아래위 경계를 막지 않으면 `catchup`이나
        # 손으로 만든 백필 하나가 몇 년 치를 한 번에 긁는다.
        if quotation_date < EARLIEST_QUOTATION_DATE:
            raise ValueError(f"quotation_date must not be earlier than {EARLIEST_QUOTATION_DATE.isoformat()}")

        today = latest_quotation_date()
        if quotation_date > today:
            raise ValueError(f"quotation_date must not be later than KST today ({today.isoformat()})")
        return quotation_date

    @property
    def form_data(self) -> dict[str, str]:
        return {
            "pbldDvCd": PUBLICATION_DIVISION,
            "curCd": self.currency.value,
            "inqDt": self.quotation_date.strftime("%Y%m%d"),
        }


class HanaRate(BaseModel):
    """정규화한 고시 환율 1건. 한 통화의 한 회차다."""

    model_config = ConfigDict(frozen=True)

    currency: HanaCurrency
    round: int
    # 해당 회차가 고시된 시각. KST 원본을 UTC로 정규화한 값이다.
    quoted_at: AwareDatetime
    buy: Decimal  # 현찰 사실 때
    sell: Decimal  # 현찰 파실 때
    send: Decimal  # 송금 보낼 때
    receive: Decimal  # 송금 받을 때
    standard: Decimal  # 매매 기준율

    @field_validator("round")
    @classmethod
    def require_positive_round(cls, value: int) -> int:
        if value < 1:
            raise ValueError("round must be a positive number")
        return value

    @field_validator("quoted_at")
    @classmethod
    def normalize_to_utc(cls, moment: datetime) -> datetime:
        # 저장·비교용 시각은 UTC로 정규화한다. naive datetime은 AwareDatetime이 이미 막는다.
        return moment.astimezone(UTC)

    @field_validator("buy", "sell", "send", "receive", "standard")
    @classmethod
    def require_finite(cls, value: Decimal) -> Decimal:
        # Decimal은 "NaN"과 "Infinity"도 받는다. 환율로 저장하면 이후 집계가 전부 오염된다.
        if not value.is_finite():
            raise ValueError("rate must be a finite number")
        if value < 0:
            raise ValueError("rate must not be negative")
        return value

    @property
    def observation_date(self) -> date:
        """저장용 UTC 날짜. `exchange_rate.date`에 그대로 들어간다."""
        return self.quoted_at.date()

    @property
    def observation_time(self) -> time:
        """저장용 UTC 시각. `exchange_rate.time`에 그대로 들어간다."""
        return self.quoted_at.time()


class HanaResponse(BaseModel):
    """한 번의 호출 결과와 그 호출을 재현하는 데 필요한 메타데이터."""

    model_config = ConfigDict(frozen=True)

    request: HanaRateRequest
    body: bytes
    status: int
    started_at: AwareDatetime
    completed_at: AwareDatetime

    @field_validator("started_at", "completed_at")
    @classmethod
    def normalize_to_utc(cls, moment: datetime) -> datetime:
        return moment.astimezone(UTC)

    @model_validator(mode="after")
    def require_ordered_span(self) -> Self:
        if self.started_at > self.completed_at:
            raise ValueError("started_at must not be after completed_at")
        return self

    @property
    def currency(self) -> HanaCurrency:
        return self.request.currency


def quotation_date_for(data_interval_end: datetime) -> date:
    """스케줄 run 하나가 다루는 고시일자.

    `data_interval_end`는 실행 시각이자 구간의 끝이다. 스케줄이 KST 08:00이므로 그 시점에
    완결돼 있는 건 **전날** 고시다. 하나은행 고시일자는 KST 날짜라 UTC로 날짜를 뽑으면 안 된다.

    하루를 빼는 건 `datetime`이 아니라 `date`에서 한다. Airflow가 넘기는 값은 pendulum
    `DateTime`이고, 여기에 `timedelta`를 빼면 결과가 UTC로 정규화돼 시간대 라벨이 날아간다.
    KST 07-01 08:00에서 하루를 빼면 06-30 08:00이 아니라 06-29 23:00(UTC)이 되고, 날짜가
    하루 더 밀린다. 표준 라이브러리 `datetime`은 시간대를 유지하므로 단위 테스트만으로는
    드러나지 않는다.

    수동 run에서는 이 값이 직관과 어긋난다. 크론 timetable의 `infer_manual_data_interval`이
    "넘긴 시각 이전의 마지막 완결 구간"을 주기 때문에, 한 날짜만 확인할 때는 계산에 기대지 말고
    DAG의 `params.quotation_date`로 날짜를 직접 넘긴다.
    """
    return data_interval_end.astimezone(KST).date() - timedelta(days=1)


def fetch_rates(request: HanaRateRequest) -> HanaResponse:
    """고시 표 HTML을 받아 온다. 파싱은 하지 않는다."""
    started_at = datetime.now(UTC)
    try:
        response = Fetcher.post(
            HANA_URL,
            data=request.form_data,
            headers=REQUEST_HEADERS,
            impersonate=IMPERSONATE,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except CurlError as error:
        # 타임아웃, DNS 실패, TLS 실패는 재시도 가능한 오류로 올린다. 원인을 체인으로 남긴다.
        # curl 오류는 메시지만으로 구분이 안 될 때가 있어서 스택이 있어야 파고들 수 있다.
        raise ConnectionError(f"Hana request failed: {error}") from error

    if not 200 <= response.status < 300:
        raise HanaHTTPError(response.status)

    return HanaResponse(
        request=request,
        body=response.body,
        status=response.status,
        started_at=started_at,
        completed_at=datetime.now(UTC),
    )


def _decimal(text: str) -> Decimal:
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation as error:
        raise HanaPayloadError(f"Not a numeric rate cell: {text!r}") from error


def _quotation_time(text: str) -> time:
    try:
        return datetime.strptime(text, "%H:%M:%S").time()  # noqa: DTZ007 - 시각만 있는 셀이라 여기서는 날짜를 붙이지 않는다
    except ValueError as error:
        raise HanaPayloadError(f"Not a quotation time cell: {text!r}") from error


def parse_rates(response: HanaResponse) -> tuple[HanaRate, ...]:
    """표에서 회차별 고시를 뽑아 회차 오름차순으로 돌려준다.

    표 자체가 없으면 실패시킨다. 선택자가 깨졌는데 조용히 0건으로 넘어가면 수집이 멈춘 걸
    아무도 모른다. 반대로 표는 있는데 행이 없는 건 휴일이라 정상이므로 빈 결과를 돌려준다.
    """
    document = Selector(content=response.body)
    body = document.css("table.tblBasic tbody")
    if not body:
        raise HanaPayloadError("Hana response has no rate table")

    rows = body[0].css("tr")
    if not rows:
        return ()

    # 표는 회차 내림차순이다. 자정을 넘는 회차를 판정하려면 오름차순으로 훑어야 한다.
    parsed: list[tuple[int, time, tuple[Decimal, ...]]] = []
    for row in rows:
        cells = [cell.get_all_text(strip=True) for cell in row.css("td")]
        if len(cells) != EXPECTED_CELL_COUNT:
            raise HanaPayloadError(f"Expected {EXPECTED_CELL_COUNT} cells per row but got {len(cells)}")

        try:
            round_number = int(cells[ROUND_CELL])
        except ValueError as error:
            raise HanaPayloadError(f"Not a round cell: {cells[ROUND_CELL]!r}") from error

        rates = tuple(_decimal(cells[index]) for index in (BUY_CELL, SELL_CELL, SEND_CELL, RECEIVE_CELL, STANDARD_CELL))
        parsed.append((round_number, _quotation_time(cells[TIME_CELL]), rates))

    parsed.sort(key=lambda item: item[0])

    quotation_date = response.request.quotation_date
    day_offset = 0
    previous: time | None = None
    normalized: list[HanaRate] = []
    for round_number, quoted_time, (buy, sell, send, receive, standard) in parsed:
        # 회차가 늘었는데 시각이 되감겼으면 자정을 넘긴 것이다.
        if previous is not None and quoted_time < previous:
            day_offset += 1
            if day_offset > MAX_DAY_OFFSET:
                # 최장 연휴보다 더 넘었다면 회차 순서와 시각이 어긋난 것이고, 그대로 두면 날짜가
                # 조용히 하루씩 더 밀린 채 저장된다. 여기서 멈추는 편이 낫다.
                raise HanaPayloadError(
                    f"Quotation times wrapped past midnight more than {MAX_DAY_OFFSET} times by round {round_number}"
                )
        previous = quoted_time

        quoted_at = datetime.combine(quotation_date + timedelta(days=day_offset), quoted_time, tzinfo=KST)
        try:
            normalized.append(
                HanaRate(
                    currency=response.currency,
                    round=round_number,
                    quoted_at=quoted_at,
                    buy=buy,
                    sell=sell,
                    send=send,
                    receive=receive,
                    standard=standard,
                )
            )
        except ValidationError as error:
            raise HanaPayloadError(f"Invalid rate row for round {round_number}") from error

    return tuple(normalized)


EXCHANGE_RATE_UPSERT = read_sql("postgres", "exchange_rate", "upsert.sql")


def _upsert_parameters(rate: HanaRate) -> tuple[Any, ...]:
    """`upsert.sql`의 자리표시자 순서와 같아야 한다."""
    return (
        rate.currency.value,
        rate.round,
        rate.observation_date,
        rate.observation_time,
        rate.buy,
        rate.sell,
        rate.send,
        rate.receive,
        rate.standard,
    )


def _execute_upserts(cursor: Cursor, parameters: Sequence[Sequence[Any]]) -> None:
    """upsert 여러 건을 한 번에 보낸다.

    psycopg2의 `executemany`는 내부적으로 행마다 왕복해서 직접 반복문을 도는 것과 같다
    (측정: 1475행에 0.64s vs 0.66s). 같은 드라이버의 `execute_batch`는 문장을 묶어 보내
    0.18s다. 로컬에서는 차이가 초 단위지만 DB가 원격이면 왕복 지연이 행 수만큼 곱해진다.

    `execute_batch`가 없으면 PEP 249 표준 `executemany`로 물러선다. psycopg3의
    `executemany`는 자체적으로 파이프라이닝을 하므로 그쪽에서는 물러서도 느리지 않다.

    **판정 기준은 import 가능 여부가 아니라 커서의 드라이버다.** 한 이미지에 psycopg2와
    psycopg3이 함께 있고 provider가 psycopg3 연결을 주면, import는 성공하는데
    `execute_batch`가 psycopg3 커서를 받아 `mogrify`를 찾다 죽는다.
    """
    if _execute_batch is None or not isinstance(cursor, _Psycopg2Cursor):
        cursor.executemany(EXCHANGE_RATE_UPSERT, parameters)
        return
    _execute_batch(cursor, EXCHANGE_RATE_UPSERT, parameters, page_size=UPSERT_PAGE_SIZE)


def store_rates(connection: Connection, rates: Sequence[HanaRate]) -> int:
    """고시 환율을 저장하고 쓴 행 수를 돌려준다. 커밋과 롤백은 호출자가 결정한다.

    `(currency, date, time, round)`가 멱등 키라서 같은 날을 다시 수집해도 행이 늘지 않고
    최신 값으로 갱신된다. `f-string`으로 값을 문장에 끼워 넣던 옛 방식과 달리 값은 전부
    파라미터로 넘긴다.

    ORM 대신 문자열 SQL을 쓴다. Airflow 이미지에는 SQLAlchemy와 이 프로젝트의 DB 설정이
    없기 때문이다. 컬럼 이름은 `tests/collectors/test_hana.py`가 모델 metadata와 맞춰 둔다.
    """
    if not rates:
        return 0

    with connection.cursor() as cursor:
        _execute_upserts(cursor, [_upsert_parameters(rate) for rate in rates])
    return len(rates)
