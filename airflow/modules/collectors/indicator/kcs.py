r"""관세청 공공데이터포털 API에서 10일 단위 수출입 잠정치를 수집한다.

배포 Airflow와 공유되는 폴더는 `airflow/dags`와 `airflow/modules` 둘뿐이다. Airflow는
`apps/`도 `core/`도 보지 못한다. 그래서 DAG가 실행 시점에 필요한 코드는 전부 여기 있어야
한다. `dags/`에는 스케줄과 오케스트레이션만 두고 수집 규칙은 이 모듈에 둔다.

의존성은 표준 라이브러리와 Pydantic, PEP 249 연결로 제한한다. **응답이 XML이라 파서가 필요한데
표준 라이브러리 `xml.etree.ElementTree`를 쓴다.** 새 의존성을 넣지 않고, 이 파서는 외부 엔티티를
처리하지 않아 XXE 경로가 없다.

설계 배경은 [docs/collection/korea-trade-collection.md](../../../../docs/collection/korea-trade-collection.md)에 있다.

## 데이터셋 넷이 규격을 공유한다

`{수출, 수입} × {품목별, 국가별}` 넷이고 **요청 인자와 응답 태그가 글자 그대로 같다**
(2026-08-28 실측). 다른 것은 서비스 경로와 `itemUsdAmt00`\~`10`이 무엇을 가리키는가뿐이다.
그래서 수집기가 하나이고 `KcsDataset`이 그 차이를 든다.

## 이 제공처의 특징

**오류를 HTTP 상태가 아니라 본문으로 알린다.** 필수 인자 누락도, 구간이 뒤집힌 것도 HTTP 200에
`<resultCode>99</resultCode>`로 온다(2026-08-28 실측). 그래서 `KcsResultError`가 따로 있고
재시도 여부는 DAG가 코드로 판단한다. `ecos.py`의 `EcosResultError`와 같은 형태다.

**한 행이 열한 칸이다.** 시계열마다 요청하는 FRED와 달리 한 응답이 전체와 열 항목을 한꺼번에
준다. 그래서 태스크를 계열이 아니라 **데이터셋마다** 매핑한다.

**금액은 콤마와 좌측 공백이 붙은 문자열이다**(`"          29,827,757"`). 단위는 천 달러다.
수출은 신고미화금액, 수입은 과세가격미화금액 기준이라 방향이 다르면 기준도 다르다.
"""

import json
import re
from calendar import monthrange
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from http.client import HTTPSConnection
from typing import Self
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPSHandler, build_opener
from xml.etree import ElementTree

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    SecretStr,
    field_validator,
    model_validator,
)

from modules.db import Connection
from modules.sql import read_sql

KCS_HOST = "https://apis.data.go.kr/1220000"
SOURCE = "kcs"

# 값의 단위. 네 데이터셋이 전부 "천 달러"로 고시한다(2026-08-28 명세 실측).
UNIT = "Thousand US Dollars"

# 한 행의 금액 칸 수. 네 데이터셋이 같다. **저장하지 않는 칸까지 센다** — 국가별은 `00`(전체)을
# 버리는데, 그 칸이 사라진 것도 응답 모양이 바뀐 것이므로 알아야 한다.
COLUMN_COUNT = 11

# 제공처가 받는 최대 조회 구간. 넘기면 본문 오류로 답한다("조회기간은 10년이내"). 2016-09~2026-08
# (120개월)은 통과하고 2016-08~2026-08(121개월)은 거부되는 것을 2026-08-28에 실측했다.
MAX_MONTHS = 120

# 제공처가 값을 주기 시작한 달. 2015-12는 정상 응답에 0건으로 답한다(2026-08-28 실측).
FIRST_MONTH = "201601"

MONTH_PATTERN = re.compile(r"^\d{6}$")

# `priodDt`는 그 달 1일부터의 누계 구간이다. 끝 숫자가 관측일이 된다.
PERIOD_PATTERN = re.compile(r"^01~(\d{2})$")

# 월 중간 마감일. 나머지 하나는 그 달의 말일이라 `calendar`가 정한다.
INTERIM_PERIOD_DAYS = (10, 20)

REQUEST_TIMEOUT_SECONDS = 30

# 제공처가 정상으로 답할 때의 코드. 나머지는 전부 실패다.
SUCCESS_CODE = "00"

# 보내지 않는 요청 헤더.
#
# **이 게이트웨이는 `baggage` 헤더가 붙으면 HTTP 400 `INVALID_REQUEST_PARAMETER_ERROR`로
# 거절한다**(2026-08-28 실측). Sentry SDK의 stdlib 통합이 켜져 있으면 urllib 요청마다
# `sentry-trace`와 `baggage`를 자동으로 끼워 넣는데, Airflow는 그 통합을 켠 채로 돈다. 그래서
# 태스크에서만 400이 나고 `docker exec python`으로 같은 URL을 부르면 200이 나왔다.
#
# `sentry-trace` 하나만 붙는 것은 통과하지만 **둘 다 뺀다.** 우리 추적 문맥을 외부 제공처에
# 흘려 보낼 이유가 없고, 남겨 두면 제공처가 규칙을 좁힐 때 같은 사고가 되풀이된다.
#
# 다른 수집기(FRED·DART·ECOS)는 지금 이 헤더를 그대로 보내고도 멀쩡하다. 그쪽이 무시할 뿐이라
# 언젠가 같은 일이 날 수 있지만, 관측된 곳만 고친다.
DROPPED_REQUEST_HEADERS = frozenset({"baggage", "sentry-trace"})


class _NoTracingConnection(HTTPSConnection):
    """추적 헤더를 빼고 보내는 연결.

    Sentry는 `HTTPConnection.putrequest`를 감싸 헤더를 `putheader`로 밀어 넣는다. 그래서 우리가
    `Request(headers=...)`로 무엇을 주든 그 뒤에 덧붙는다. 막을 수 있는 자리가 `putheader`다.
    """

    def putheader(self, header: str, *values: object) -> None:
        if header.lower() in DROPPED_REQUEST_HEADERS:
            return
        super().putheader(header, *values)


class _NoTracingHandler(HTTPSHandler):
    def https_open(self, request: object):
        return self.do_open(_NoTracingConnection, request, context=self._context)


# 이 수집기만 쓰는 opener다. **전역 opener를 갈아 끼우지 않는다** — 그러면 같은 프로세스의
# 다른 수집기까지 조용히 따라 바뀐다.
_OPENER = build_opener(_NoTracingHandler)


class KcsSeries(BaseModel):
    """저장 식별자 하나와 그 값이 들어 있는 칸.

    **열 번호가 곧 항목이다.** 응답은 `itemUsdAmt00`부터 `itemUsdAmt10`까지이고 태그 이름이
    아니라 순서가 품목·국가를 정한다. 그래서 제공처가 목록 구성을 바꾸면 값이 조용히 옆 칸으로
    밀리고, 칸 수 검사로는 그것을 못 잡는다. 순서의 원본은 제공처 명세이고 여기 옮겨 적은 것을
    2026-08-28에 실측으로 대조했다.
    """

    model_config = ConfigDict(frozen=True)

    series_id: str
    column: int
    label: str

    @property
    def field(self) -> str:
        """응답에서 이 계열의 값을 담고 있는 태그 이름."""
        return f"itemUsdAmt{self.column:02d}"


def _series(rows: tuple[tuple[str, int, str], ...]) -> tuple[KcsSeries, ...]:
    return tuple(KcsSeries(series_id=series_id, column=column, label=label) for series_id, column, label in rows)


# 전체 + 수출 10대 품목. 순서는 데이터셋 명세 그대로다.
EXPORT_ITEM_SERIES = _series(
    (
        ("KR_EXPORT_MTD", 0, "한국 수출 전체(월 누계)"),
        ("KR_EXPORT_SEMICON_MTD", 1, "한국 반도체 수출(월 누계)"),
        ("KR_EXPORT_STEEL_MTD", 2, "한국 철강제품 수출(월 누계)"),
        ("KR_EXPORT_CAR_MTD", 3, "한국 승용차 수출(월 누계)"),
        ("KR_EXPORT_OILPROD_MTD", 4, "한국 석유제품 수출(월 누계)"),
        ("KR_EXPORT_WIRELESS_MTD", 5, "한국 무선통신기기 수출(월 누계)"),
        ("KR_EXPORT_SHIP_MTD", 6, "한국 선박 수출(월 누계)"),
        ("KR_EXPORT_AUTOPART_MTD", 7, "한국 자동차부품 수출(월 누계)"),
        ("KR_EXPORT_COMPUTER_MTD", 8, "한국 컴퓨터 주변기기 수출(월 누계)"),
        ("KR_EXPORT_PRECISION_MTD", 9, "한국 정밀기기 수출(월 누계)"),
        ("KR_EXPORT_APPLIANCE_MTD", 10, "한국 가전제품 수출(월 누계)"),
    )
)

# 전체 + 수입 10대 품목. **수출과 품목이 다르고 순서도 다르다** — 원유·가스·석탄처럼 수입에만
# 있는 것이 있고, 반도체 제조용장비는 설비투자 신호다.
IMPORT_ITEM_SERIES = _series(
    (
        ("KR_IMPORT_MTD", 0, "한국 수입 전체(월 누계)"),
        ("KR_IMPORT_SEMICON_MTD", 1, "한국 반도체 수입(월 누계)"),
        ("KR_IMPORT_CRUDE_MTD", 2, "한국 원유 수입(월 누계)"),
        ("KR_IMPORT_MACHINERY_MTD", 3, "한국 기계류 수입(월 누계)"),
        ("KR_IMPORT_GAS_MTD", 4, "한국 가스 수입(월 누계)"),
        ("KR_IMPORT_CHIPEQUIP_MTD", 5, "한국 반도체 제조용장비 수입(월 누계)"),
        ("KR_IMPORT_PRECISION_MTD", 6, "한국 정밀기기 수입(월 누계)"),
        ("KR_IMPORT_OILPROD_MTD", 7, "한국 석유제품 수입(월 누계)"),
        ("KR_IMPORT_WIRELESS_MTD", 8, "한국 무선통신기기 수입(월 누계)"),
        ("KR_IMPORT_CAR_MTD", 9, "한국 승용차 수입(월 누계)"),
        ("KR_IMPORT_COAL_MTD", 10, "한국 석탄 수입(월 누계)"),
    )
)

# 수출 주요 10개국. **`00`(전체)을 담지 않는다** — 품목별 데이터셋의 `KR_EXPORT_MTD`와 같은 값이고
# (2026-07 1~10일 둘 다 29,827,757로 일치했다) 같은 자연키에 두 번 쓰면 계보만 흐려진다.
EXPORT_COUNTRY_SERIES = _series(
    (
        ("KR_EXPORT_CN_MTD", 1, "한국 대중국 수출(월 누계)"),
        ("KR_EXPORT_US_MTD", 2, "한국 대미국 수출(월 누계)"),
        ("KR_EXPORT_EU_MTD", 3, "한국 대유럽연합 수출(월 누계)"),
        ("KR_EXPORT_VN_MTD", 4, "한국 대베트남 수출(월 누계)"),
        ("KR_EXPORT_HK_MTD", 5, "한국 대홍콩 수출(월 누계)"),
        ("KR_EXPORT_JP_MTD", 6, "한국 대일본 수출(월 누계)"),
        ("KR_EXPORT_TW_MTD", 7, "한국 대대만 수출(월 누계)"),
        ("KR_EXPORT_IN_MTD", 8, "한국 대인도 수출(월 누계)"),
        ("KR_EXPORT_SG_MTD", 9, "한국 대싱가포르 수출(월 누계)"),
        ("KR_EXPORT_MY_MTD", 10, "한국 대말레이시아 수출(월 누계)"),
    )
)

# 수입 주요 10개국. **나라 목록과 순서가 수출과 다르다** — 홍콩·인도·싱가포르 대신 호주·
# 사우디아라비아·러시아연방이고, 일본과 베트남의 자리도 바뀐다. 한쪽 순서를 복사하면 값이
# 통째로 다른 나라에 붙는다.
IMPORT_COUNTRY_SERIES = _series(
    (
        ("KR_IMPORT_CN_MTD", 1, "한국 대중국 수입(월 누계)"),
        ("KR_IMPORT_US_MTD", 2, "한국 대미국 수입(월 누계)"),
        ("KR_IMPORT_EU_MTD", 3, "한국 대유럽연합 수입(월 누계)"),
        ("KR_IMPORT_JP_MTD", 4, "한국 대일본 수입(월 누계)"),
        ("KR_IMPORT_VN_MTD", 5, "한국 대베트남 수입(월 누계)"),
        ("KR_IMPORT_AU_MTD", 6, "한국 대호주 수입(월 누계)"),
        ("KR_IMPORT_TW_MTD", 7, "한국 대대만 수입(월 누계)"),
        ("KR_IMPORT_SA_MTD", 8, "한국 대사우디아라비아 수입(월 누계)"),
        ("KR_IMPORT_RU_MTD", 9, "한국 대러시아연방 수입(월 누계)"),
        ("KR_IMPORT_MY_MTD", 10, "한국 대말레이시아 수입(월 누계)"),
    )
)


class KcsDataset(StrEnum):
    """수집 대상 데이터셋 넷. 값이 `source_record.source_key`이기도 하다.

    넷이 요청 인자와 응답 태그를 공유하므로 다른 것은 서비스 경로와 칸의 뜻뿐이다.
    데이터셋마다 공공데이터포털에서 따로 활용신청해야 한다 — 승인되지 않은 것은 서비스가
    있어도 `SERVICE_KEY_IS_NOT_REGISTERED_ERROR`로 거절된다(2026-08-28 실측).
    """

    stem: str
    series: tuple[KcsSeries, ...]
    label: str

    def __new__(cls, source_key: str, stem: str, series: tuple[KcsSeries, ...], label: str) -> Self:
        member = str.__new__(cls, source_key)
        member._value_ = source_key
        member.stem = stem
        member.series = series
        member.label = label
        return member

    @property
    def url(self) -> str:
        """이 데이터셋의 오퍼레이션 주소. 경로와 오퍼레이션 이름이 대문자 하나만 다르다."""
        operation = f"get{self.stem[0].upper()}{self.stem[1:]}"
        return f"{KCS_HOST}/{self.stem}/{operation}"

    EXPORT_ITEM = ("export_item_tenday", "prlstMmUtPrviExpAcrs", EXPORT_ITEM_SERIES, "수출 주요품목별")
    IMPORT_ITEM = ("import_item_tenday", "prlstMmUtPrviImpAcrs", IMPORT_ITEM_SERIES, "수입 주요품목별")
    EXPORT_COUNTRY = ("export_country_tenday", "cntyMmUtPrviExpAcrs", EXPORT_COUNTRY_SERIES, "수출 주요국가별")
    IMPORT_COUNTRY = ("import_country_tenday", "cntyMmUtPrviImpAcrs", IMPORT_COUNTRY_SERIES, "수입 주요국가별")


# DAG이 태스크를 매핑하는 단위.
DATASETS: tuple[str, ...] = tuple(dataset.value for dataset in KcsDataset)

# 마스터 시드와 대조하는 계열 전부.
ALL_SERIES: tuple[str, ...] = tuple(
    series.series_id for dataset in KcsDataset for series in dataset.series
)


class KcsHTTPError(RuntimeError):
    """게이트웨이가 2xx가 아닌 상태로 응답했다. 재시도 가능 여부는 호출자가 `status`로 판단한다.

    **상태 코드만으로는 무엇이 틀렸는지 알 수 없다.** 이 게이트웨이는 2xx가 아닌 응답에도
    사유를 XML 본문에 담는다(`SERVICE_KEY_IS_NOT_REGISTERED_ERROR`, `NO_OPENAPI_SERVICE_ERROR`
    처럼). 초판은 그 본문을 버려서 운영 로그에 "HTTP 400"만 남았고, 키 문제인지 주소 문제인지를
    가릴 수 없었다. 그래서 `reason`을 함께 든다.

    **본문만 담고 URL은 담지 않는다.** `HTTPError`가 URL을 `filename`에 들고 있는데 거기에는
    서비스키가 들어 있다.
    """

    def __init__(self, status: int, retry_after: str | None = None, reason: str | None = None) -> None:
        detail = f" ({reason})" if reason else ""
        super().__init__(f"KCS request failed with HTTP {status}{detail}")
        self.status = status
        self.retry_after = retry_after
        self.reason = reason


class KcsResultError(RuntimeError):
    """제공처가 HTTP 200 본문에 실패 코드를 담아 보냈다.

    코드를 해석하지 않는다. 재시도할 값어치가 있는지는 DAG가 `code`로 정한다.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"KCS returned result code {code}: {message}")
        self.code = code
        self.result_message = message


class KcsPayloadError(ValueError):
    """응답이 XML이 아니거나 우리가 아는 모양이 아니다. 재시도해도 같은 결과다."""


class KcsRequest(BaseModel):
    """한 데이터셋의 한 조회 구간. 호출 전에 여기서 전부 검증한다.

    제공처가 받는 형식이 `YYYYMM`이라 날짜가 아니라 월 문자열을 든다. 부르는 쪽(DAG)이 날짜에서
    월로 줄이고, 그 변환은 `from_dates`가 한 벌 갖는다.
    """

    model_config = ConfigDict(frozen=True)

    dataset: KcsDataset
    start_month: str
    end_month: str

    @field_validator("start_month", "end_month")
    @classmethod
    def require_month_shape(cls, month: str) -> str:
        if not MONTH_PATTERN.match(month):
            raise ValueError(f"month must be YYYYMM: {month!r}")
        if not 1 <= int(month[4:]) <= 12:
            raise ValueError(f"month must name a real month: {month!r}")
        return month

    @model_validator(mode="after")
    def require_supported_window(self) -> Self:
        if self.start_month > self.end_month:
            raise ValueError("start_month must not be after end_month")
        if self.month_count > MAX_MONTHS:
            # 제공처도 본문 오류로 막지만 요청 전에 막는 편이 낫다. 백필이 이 상한에 걸린다.
            raise ValueError(f"window must not exceed {MAX_MONTHS} months")
        return self

    @property
    def month_count(self) -> int:
        start = int(self.start_month[:4]) * 12 + int(self.start_month[4:])
        end = int(self.end_month[:4]) * 12 + int(self.end_month[4:])
        return end - start + 1

    @classmethod
    def from_dates(cls, dataset: KcsDataset, start: date, end: date) -> Self:
        """날짜 구간을 그것이 걸친 월 구간으로 바꾼다.

        수집 단위가 월이라 하루라도 걸친 달은 통째로 받는다. 잘라 받으면 그 달의 누계 행이 빠진다.
        """
        return cls(
            dataset=dataset,
            start_month=start.strftime("%Y%m"),
            end_month=end.strftime("%Y%m"),
        )


class KcsObservation(BaseModel):
    """정규화한 관측값 1건."""

    model_config = ConfigDict(frozen=True)

    series_id: str
    observation_date: date
    value: Decimal

    @field_validator("value")
    @classmethod
    def require_finite(cls, value: Decimal) -> Decimal:
        # Decimal은 "NaN"과 "Infinity"도 받아들인다. 지표 값으로 저장하면 이후 집계가 전부 오염된다.
        if not value.is_finite():
            raise ValueError("observation value must be a finite number")
        return value


class KcsResponse(BaseModel):
    """한 번의 호출 결과와 그 호출을 재현하는 데 필요한 메타데이터."""

    model_config = ConfigDict(frozen=True)

    request: KcsRequest
    body: bytes
    status: int
    started_at: AwareDatetime
    completed_at: AwareDatetime

    @field_validator("started_at", "completed_at")
    @classmethod
    def normalize_to_utc(cls, moment: datetime) -> datetime:
        # 저장·비교용 시각은 UTC로 정규화한다. naive datetime은 AwareDatetime이 이미 막는다.
        return moment.astimezone(UTC)


# 2xx가 아닌 응답의 본문에서 사유가 들어 있는 칸. 게이트웨이가 형식을 둘로 쓴다 —
# 인증 계열은 `errMsg`/`returnAuthMsg`, 서비스 계열은 `resultMsg`다. **키는 어디에도 없다.**
FAILURE_REASON_TAGS = ("errMsg", "returnAuthMsg", "resultMsg", "returnReasonCode")

# 사유 문자열의 상한. 게이트웨이가 HTML 오류 페이지를 돌려주는 경우가 있어 로그를 덮지 않게 자른다.
MAX_REASON_CHARS = 200


def _failure_reason(error: HTTPError) -> str | None:
    """오류 응답 본문에서 사유를 뽑는다. 못 읽으면 `None`이다.

    본문을 읽는 것 자체가 실패해도 원래의 HTTP 오류를 잃지 않아야 하므로 여기서는 넓게 잡는다.
    """
    try:
        body = error.read().decode("utf-8", "replace")
    except OSError:
        # 이미 닫힌 응답이거나 읽는 중 끊겼다. 상태 코드만으로 올린다.
        return None

    reasons = [
        found.strip()
        for tag in FAILURE_REASON_TAGS
        if (found := _tag_text(body, tag)) is not None and found.strip()
    ]
    if not reasons:
        return " ".join(body.split())[:MAX_REASON_CHARS] or None
    return " ".join(reasons)[:MAX_REASON_CHARS]


def _tag_text(body: str, tag: str) -> str | None:
    opening, closing = f"<{tag}>", f"</{tag}>"
    start = body.find(opening)
    if start == -1:
        return None
    end = body.find(closing, start)
    if end == -1:
        return None
    return body[start + len(opening) : end]


def _require_result_code(root: ElementTree.Element) -> None:
    """제공처가 본문에 담아 보낸 결과 코드를 본다."""
    code = root.findtext("./header/resultCode")
    if code is None:
        raise KcsPayloadError("KCS response has no result code")
    if code != SUCCESS_CODE:
        raise KcsResultError(code, root.findtext("./header/resultMsg") or "")


def _observation_date(period_month: str, period_days: str) -> date:
    """`priodMon`(`202607`)과 `priodDt`(`01~10`)를 관측일로 바꾼다.

    관측일은 누계 구간의 **끝**이다. 월 중간 마감은 10일과 20일이고 나머지 하나는 그 달의 말일이라
    달마다 다르다(2월은 `01~28`, 윤년은 `01~29`). **그 달에 없는 날이 오면 실패시킨다** — 조용히
    엉뚱한 날짜로 저장하는 것보다 멈추는 편이 낫다.
    """
    match = PERIOD_PATTERN.match(period_days)
    if match is None:
        raise KcsPayloadError(f"KCS returned an unexpected period: {period_days!r}")

    year, month = int(period_month[:4]), int(period_month[4:])
    day = int(match.group(1))
    last_day = monthrange(year, month)[1]
    if day not in INTERIM_PERIOD_DAYS and day != last_day:
        raise KcsPayloadError(f"KCS returned period {period_days!r} for {period_month}")
    return date(year, month, day)


def _amount(raw: str | None, field: str) -> Decimal:
    """`"          29,827,757"`을 수로 바꾼다. 빈 칸과 숫자 아닌 값은 전체를 실패시킨다."""
    if raw is None:
        raise KcsPayloadError(f"KCS response has no {field}")
    try:
        return Decimal(raw.replace(",", "").strip())
    except ArithmeticError as error:
        raise KcsPayloadError(f"KCS returned a non-numeric {field}") from error


def _require_every_column(item: ElementTree.Element) -> None:
    """금액 칸이 열한 개 그대로인지 본다.

    **저장하지 않는 칸까지 센다.** 국가별은 `00`(전체)을 버리지만 그 칸이 사라진 것도 응답 모양이
    바뀐 것이다. 칸이 하나 줄면 그 뒤가 전부 한 칸씩 밀려 다른 나라 값이 저장된다.
    """
    missing = [index for index in range(COLUMN_COUNT) if item.find(f"itemUsdAmt{index:02d}") is None]
    if missing:
        raise KcsPayloadError(f"KCS row is missing columns {missing}")


def parse_items(body: bytes, request: KcsRequest) -> tuple[KcsObservation, ...]:
    """유효 관측값을 뽑는다. 형식이 깨진 항목 하나가 전체를 실패시킨다.

    검사가 넷이다.

    - **결과 코드.** 오류가 HTTP 200 본문으로 오므로 여기서 먼저 본다.
    - **잘림.** 제공처가 알려 준 `totalCount`와 받은 행 수를 대조한다. 조용히 잘린 응답은
      조회 구간에 구멍을 남긴다.
    - **구간.** 우리가 묻지 않은 달이 섞여 오면 실패시킨다. 응답에 요청하지 않은 식별자가 섞이는
      것을 막는 규칙이 여기서는 달이다.
    - **칸 수.** 열이 늘거나 줄면 값이 옆 칸으로 밀린다.
    """
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as error:
        raise KcsPayloadError("KCS response is not valid XML") from error

    _require_result_code(root)

    items = list(root.iter("item"))
    total = root.findtext("./body/totalCount")
    if total is None:
        raise KcsPayloadError("KCS response has no total count")
    if int(total) != len(items):
        raise KcsPayloadError(f"KCS said {total} rows but sent {len(items)}")

    observations: list[KcsObservation] = []
    for item in items:
        period_month = item.findtext("priodMon")
        period_days = item.findtext("priodDt")
        if period_month is None or period_days is None:
            raise KcsPayloadError("KCS row has no period")
        if not request.start_month <= period_month <= request.end_month:
            raise KcsPayloadError(f"KCS returned {period_month} outside the requested window")

        _require_every_column(item)
        observation_date = _observation_date(period_month, period_days)
        for series in request.dataset.series:
            observations.append(
                KcsObservation(
                    series_id=series.series_id,
                    observation_date=observation_date,
                    value=_amount(item.findtext(series.field), series.field),
                )
            )
    return tuple(observations)


# 쿼리는 `sql/` 볼륨에 둔다. 배포 Airflow가 `/opt/airflow/sql`로 마운트하는 폴더다.
SOURCE_RECORD_INSERT = read_sql("postgres", "source_record", "insert.sql")
OBSERVATION_UPSERT = read_sql("postgres", "indicator_observation", "upsert.sql")


class KcsTradeCollector:
    """관세청 수출입 수집기. 서비스키를 쥐고 데이터셋·구간마다 조회·저장한다.

    한 실행이 객체 하나다. 키는 이 객체가 사는 동안 안 변하는 값이라 생성자가 받고, 데이터셋과
    구간은 `KcsRequest`로 호출마다 들어온다. 파싱은 밖에 둔다(`parse_items`).
    """

    def __init__(self, service_key: SecretStr) -> None:
        if not service_key.get_secret_value():
            raise ValueError("KCS service key is required")
        self._service_key = service_key

    def build_url(self, request: KcsRequest) -> str:
        """호출 URL. 반환값에 서비스키가 들어가므로 로그나 예외 메시지에 넣지 않는다.

        **키는 디코딩(원문) 값을 담고 인코딩은 `urlencode`에 맡긴다.** 포털이 Encoding·Decoding 두
        형태로 키를 발급하는데, 이미 인코딩된 값을 다시 인코딩하면 `%2F`가 `%252F`가 되어 등록되지
        않은 키로 거절된다.
        """
        query = urlencode(
            {
                "serviceKey": self._service_key.get_secret_value(),
                "strtYymm": request.start_month,
                "endYymm": request.end_month,
            }
        )
        return f"{request.dataset.url}?{query}"

    def fetch_trade(self, request: KcsRequest) -> KcsResponse:
        url = self.build_url(request)
        started_at = datetime.now(UTC)
        try:
            with _OPENER.open(url, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                body = response.read()
                status = response.status
        # `from None`은 여기서 의도적이다. URL에 서비스키가 들어 있고 `HTTPError`는 그 URL을
        # `filename`에 담는다. 체인을 남기면 Sentry나 Airflow 로그가 원인 예외를 붙잡을 때 키가
        # 함께 실린다. `fred.py`가 같은 이유로 같은 형태다.
        except HTTPError as error:
            raise KcsHTTPError(error.code, error.headers.get("Retry-After"), _failure_reason(error)) from None
        except URLError as error:
            # 타임아웃과 DNS·연결 실패는 재시도 가능한 오류로 올린다.
            raise ConnectionError(f"KCS request failed: {error.reason}") from None

        return KcsResponse(
            request=request,
            body=body,
            status=status,
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )

    def store_observations(self, connection: Connection, response: KcsResponse) -> int:
        """원본 1건과 유효 관측값을 저장하고 저장한 관측값 수를 돌려준다.

        파싱을 먼저 해서 형식 오류면 아무 것도 쓰지 않는다. `(provider, series_id, observation_date)`가
        멱등 키라서 같은 구간을 다시 수집해도 행이 늘지 않고 최신 값으로 갱신된다. 제공처가 전월까지의
        값을 정정으로 현행화하므로 그 갱신이 이 수집의 목적 중 하나다.

        **`payload`를 채우지 않는다.** 컬럼 타입이 `jsonb`인데 원본이 XML이다. 어느 데이터셋의 어느
        구간을 물었는지는 `metadata`가 갖는다.

        ORM 대신 문자열 SQL을 쓴다. Airflow 이미지에는 SQLAlchemy와 이 프로젝트의 DB 설정이
        없기 때문이다. 컬럼 이름은 `tests/collectors/test_kcs.py`가 모델 metadata와 맞춰 둔다.
        """
        dataset = response.request.dataset
        observations = parse_items(response.body, response.request)
        request_metadata = json.dumps(
            {
                "http_status": response.status,
                "dataset": dataset.value,
                "service": dataset.stem,
                "start_month": response.request.start_month,
                "end_month": response.request.end_month,
                "series_count": len(dataset.series),
            }
        )

        with connection.cursor() as cursor:
            cursor.execute(
                SOURCE_RECORD_INSERT,
                (
                    "api",
                    SOURCE,
                    dataset.value,
                    response.started_at,
                    response.completed_at,
                    "succeeded",
                    len(observations),
                    None,
                    request_metadata,
                ),
            )
            source_record_id = cursor.fetchone()[0]
            for observation in observations:
                cursor.execute(
                    OBSERVATION_UPSERT,
                    (
                        SOURCE,
                        observation.series_id,
                        observation.observation_date,
                        observation.value,
                        UNIT,
                        source_record_id,
                    ),
                )
        return len(observations)
