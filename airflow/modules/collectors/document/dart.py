"""OpenDART에서 삼성전자·SK하이닉스의 공시와 실적을 수집한다.

저장 대상은 `disclosure_event`와 `earnings_fact`다. 정의의 원본은 백엔드의
`apps/models/market.py`이며 여기 SQL의 컬럼 이름은 `tests/collectors/test_dart.py`가 그 모델
metadata와 대조한다.

아래 계약은 2026-08-12에 실제 응답으로 확인했다.

## 공시 이벤트와 실적 추출을 분리한다

공시 이벤트 저장이 먼저고 숫자 추출은 그다음이다. 잠정실적 표 형식이 바뀌어 숫자를 못
읽어도 **공시 이벤트까지 버리지 않는다.** 그래서 `earnings_fact`는 `disclosure_event`로
외래키를 걸지 않고 `rcept_no`로만 이어진다. 아직 숫자를 못 얻은 공시는 다음 폴링이
다시 시도하므로 별도 작업 큐가 필요 없다.

## 시각 셋이 다른 것을 뜻한다

- `rcept_dt`는 `YYYYMMDD` 날짜뿐이다. 시·분이 없다. 자정으로 꾸며 저장하지 않는다.
- `detected_at`은 우리가 처음 본 시각이고 재수집해도 갱신하지 않는다. 2분 폴링이라 공시
  시각의 상한이며, 오차는 폴링 주기와 DART 목록 반영 지연의 합이다.
- **분 단위 접수 시각은 수집하지 않는다.** 그 값은 공식 RSS(`dart.fss.or.kr`)에만 있는데
  전 상장사 최신 50건뿐이라 실측에서 1시간 35분치만 덮었고, 우리가 저장한 공시 52건과
  겹치는 접수번호가 하나도 없었다. 과거는 원리적으로 채울 수 없고 현재는 `detected_at`이
  이미 2분 해상도를 준다. 매 폴링에 호출 하나와 계보 행 하나를 더할 값어치가 없다.
  DART 목록 반영 지연 자체를 재야 할 때 회사별 RSS를 그때 붙인다.

## 잠정실적 원문 (실측)

`document.xml`은 ZIP이고 안에 `<접수번호>.xml` 하나가 들어 있다. HTML 표다.

- 본문 표는 **`XFormD1_Form0_RepeatTable0`**이고 실적기간 표는 `XFormD1_Form0_Table0`다.
  **정정 공시에는 `XFormD8_*` 표가 앞에 더 붙는다.** 그 표는 정정 사유와 정정 전후 요약이라
  숫자를 거기서 읽으면 안 된다. 그래서 표를 id로 집는다. 위치로 찾으면 정정 공시에서 조용히
  다른 숫자를 읽는다.
- 지표 행은 `매출액`·`영업이익`·`당기순이익`이고 각각 `당해실적`(그 분기)과 `누계실적`
  (사업연도 누계) 두 줄이다. 이어지는 값 일곱 칸은
  `[당기실적, 전기실적, 전기대비 증감율, 흑자적자전환, 전년동기실적, 전년동기 증감율, 흑자적자전환]`이다.
- **`당기순이익` 바로 아래 `지배기업 소유주지분 순이익` 행이 있다.** 앞부분만 맞춰 찾으면
  그 행을 순이익으로 읽는다. 라벨은 정확히 일치시킨다.
- **단위가 공시마다 다르다.** 실측에서 같은 분기에 삼성전자 원본이 `억원`, 그 정정이 `조원`,
  SK하이닉스가 `백만원`이었다. 표 안의 `단위 : ...` 칸을 읽어 원 단위로 정규화한다.
  정정본이 조원으로 반올림해 원본보다 정밀도가 낮아지는 일이 있는데, 그건 공시가 그런 것이라
  그대로 저장한다.
- 값에 쉼표가 들어가고 없는 값은 `-`다. `-`를 0으로 바꾸지 않는다.

## 정기 재무제표 (실측)

`fnlttSinglAcntAll.json`은 접수번호가 아니라 회사·사업연도·보고서코드로 조회한다. 응답
`rcept_no`가 처리 중인 공시와 다르면 저장하지 않는다.

- 손익계산서는 `sj_div='IS'`다. `CIS`에도 순이익이 중복으로 오므로 `IS`만 읽는다.
- **계정은 이름이 아니라 `account_id`로 잡는다.** 순이익의 `account_nm`은 보고서에 따라
  `반기순이익`·`분기순이익`·`당기순이익`으로 바뀌지만 id는 `ifrs-full_ProfitLoss`로 같다.
- `thstrm_amount`가 그 분기 금액이고 `thstrm_add_amount`가 사업연도 누계다.
- 전년 동기는 누계(`frmtrm_add_amount`)만 오고 분기값(`frmtrm_amount`)은 비어 있었다.
- 금액 단위는 원이고 `currency`는 `KRW`다. 배수를 곱하지 않는다.

`report_nm`이 `분기보고서 (2026.03)` 꼴이라 사업연도와 보고서코드를 여기서 뽑는다.
`[기재정정]` 같은 접두사는 떼고 판정하되 원문 이름은 그대로 저장한다.
"""

import hashlib
import io
import json
import logging
import re
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from http.client import HTTPException
from typing import Any, Protocol, Self
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, SecretStr
from scrapling import Selector

from modules.sql import read_sql
from modules.upsert import execute_upserts

logger = logging.getLogger(__name__)

OPENDART_BASE_URL = "https://opendart.fss.or.kr/api"

SOURCE = "dart"
SOURCE_KEY_LIST = "disclosure_list"
SOURCE_KEY_DOCUMENT = "disclosure_document"
SOURCE_KEY_FINANCIALS = "financial_statement"

REQUEST_TIMEOUT_SECONDS = 30

# OpenDART(`opendart.fss.or.kr`)는 기본 User-Agent로도 답한다.
USER_AGENT = "news-collector/1.0 (+https://opendart.fss.or.kr/)"

# 목록 한 장의 최대 건수와 장 수 상한. 7일 창에서는 한 장이면 끝나지만 정정이 몰리면 늘어난다.
# 상한은 백필이 정한다. 삼성전자의 1년치가 2,886건(실측)이라 열 장으로는 절반도 못 받는다.
PAGE_COUNT = 100
MAX_PAGES = 40

# OpenDART가 본문 `status`로 알리는 값 중 우리가 뜻을 아는 것.
STATUS_OK = "000"
STATUS_NO_DATA = "013"
STATUS_NO_FILE = "014"
STATUS_RATE_LIMIT = "020"

# 원문 표의 id. 위치가 아니라 id로 집는다. 정정 공시에는 앞에 다른 표가 더 붙는다.
PERIOD_TABLE_ID = "XFormD1_Form0_Table0"
RESULT_TABLE_ID = "XFormD1_Form0_RepeatTable0"

# 지표 행 라벨. `당기순이익` 아래 `지배기업 소유주지분 순이익`이 있어 정확히 일치시킨다.
PROVISIONAL_METRICS: dict[str, str] = {
    "매출액": "revenue",
    "영업이익": "operating_profit",
    "당기순이익": "net_income",
}

# 지표 행 다음에 오는 기간 기준 라벨.
BASIS_LABELS: dict[str, str] = {"당해실적": "period", "누계실적": "cumulative"}

# 라벨 뒤에 이어지는 값 칸 수. 사이트가 열을 늘리면 값이 옆으로 밀리므로 개수를 검증한다.
PROVISIONAL_VALUE_COLUMNS = 7
CURRENT_VALUE_INDEX = 0
PRIOR_YEAR_VALUE_INDEX = 4

# 원문이 밝히는 금액 단위. 원 단위로 정규화할 때 쓴다.
UNIT_MULTIPLIERS: dict[str, Decimal] = {
    "원": Decimal(1),
    "천원": Decimal(1_000),
    "백만원": Decimal(1_000_000),
    "십억원": Decimal(1_000_000_000),
    "억원": Decimal(100_000_000),
    "조원": Decimal(1_000_000_000_000),
}

# 정기 재무제표 계정. 이름이 아니라 id로 잡는다.
FINANCIAL_ACCOUNTS: dict[str, str] = {
    "ifrs-full_Revenue": "revenue",
    "dart_OperatingIncomeLoss": "operating_profit",
    "ifrs-full_ProfitLoss": "net_income",
}

# 정기보고서 이름의 월 → OpenDART 보고서코드와 기간 종료월.
PERIODIC_REPORTS: dict[int, str] = {3: "11013", 6: "11012", 9: "11014", 12: "11011"}
QUARTER_END_DAYS: dict[int, int] = {3: 31, 6: 30, 9: 30, 12: 31}

# 잠정실적 공시를 알아보는 이름. 정정 접두사를 떼고 비교한다.
PROVISIONAL_REPORT_NAME = "연결재무제표기준영업(잠정)실적(공정공시)"
PERIODIC_REPORT_PATTERN = re.compile(r"^(사업보고서|반기보고서|분기보고서)\s*\((\d{4})\.(\d{2})\)")
CORRECTION_PREFIX_PATTERN = re.compile(r"^\[[^\]]*\]\s*")

DISCLOSURE_EVENT_UPSERT = read_sql("postgres", "disclosure_event", "upsert.sql")
DISCLOSURE_EVENT_PENDING_SELECT = read_sql("postgres", "disclosure_event", "select_pending_earnings.sql")
EARNINGS_FACT_UPSERT = read_sql("postgres", "earnings_fact", "upsert.sql")
SOURCE_RECORD_INSERT = read_sql("postgres", "source_record", "insert.sql")


class DartCompany(StrEnum):
    """수집 대상. 종목코드, DART 회사 고유번호, 회사명을 한 줄에 묶는다.

    회사가 둘뿐이라 마스터 테이블을 만들지 않는다. 고유번호는 `corpCode.xml`에서 확인했다
    (실측 2026-08-12). 대상이 설정으로 늘어날 때 마스터로 옮긴다.
    """

    corp_code: str
    label: str

    def __new__(cls, stock_code: str, corp_code: str, label: str) -> Self:
        member = str.__new__(cls, stock_code)
        member._value_ = stock_code
        member.corp_code = corp_code
        member.label = label
        return member

    SAMSUNG_ELECTRONICS = ("005930", "00126380", "삼성전자")
    SK_HYNIX = ("000660", "00164779", "SK하이닉스")


class Cursor(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(self, *args: object) -> bool | None: ...

    def execute(self, statement: str, parameters: Any) -> object: ...

    def executemany(self, statement: str, parameters: Any) -> object: ...

    def fetchone(self) -> Any: ...

    def fetchall(self) -> Any: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...


class DartHTTPError(RuntimeError):
    """OpenDART가 2xx가 아닌 상태로 응답했다. 재시도 여부는 호출자가 `status`로 정한다."""

    def __init__(self, status: int) -> None:
        super().__init__(f"DART request failed with HTTP {status}")
        self.status = status


class DartStatusError(RuntimeError):
    """OpenDART가 본문 `status`로 실패를 알렸다.

    HTTP는 200이다. 재시도 여부는 DAG가 `code`로 정한다. 데이터 없음(`013`)과 원문 없음
    (`014`)은 여기까지 오지 않고 수집기가 빈 결과로 바꾼다.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"DART returned {code}: {message}")
        self.code = code
        self.message = message


class DartPayloadError(ValueError):
    """응답이 계약을 지키지 않았다. 재시도해도 같은 결과다."""


class Disclosure(BaseModel):
    """공시 목록의 한 행."""

    model_config = ConfigDict(frozen=True)

    corp_code: str
    corp_name: str
    stock_code: str
    corp_class: str
    report_name: str
    rcept_no: str
    filer_name: str
    receipt_date: date
    remarks: str | None

    @classmethod
    def from_payload(cls, row: dict[str, Any]) -> "Disclosure":
        remarks = str(row.get("rm", "")).strip()
        return cls(
            corp_code=row["corp_code"],
            corp_name=str(row["corp_name"]).strip(),
            stock_code=str(row["stock_code"]).strip(),
            corp_class=str(row["corp_cls"]).strip(),
            # 원문 이름은 뒤에 공백 패딩이 붙어 온다. 의미가 아니라 표기라 잘라 낸다.
            report_name=str(row["report_nm"]).strip(),
            rcept_no=str(row["rcept_no"]).strip(),
            filer_name=str(row["flr_nm"]).strip(),
            receipt_date=_day(row["rcept_dt"]),
            remarks=remarks or None,
        )


class DisclosureFetch(BaseModel):
    model_config = ConfigDict(frozen=True)

    company: str
    corp_code: str
    begin_date: date
    end_date: date
    disclosures: tuple[Disclosure, ...]
    page_count: int
    total_count: int
    started_at: datetime
    completed_at: datetime


class StatementScope(StrEnum):
    """재무제표 범위. 연결과 별도를 합치거나 서로 대체하지 않는다.

    `apps/models/market.py`의 같은 이름 Enum과 값이 같아야 한다. Airflow 트리는 `apps/`를
    보지 못해 한 벌을 더 두고, `tests/collectors/test_dart.py`가 둘을 대조한다.
    """

    CFS = "CFS"
    OFS = "OFS"


class ProvisionalMeta(BaseModel):
    """잠정실적 원문을 어떻게 읽었는지. `source_record.metadata`로 남아 재현의 근거가 된다."""

    model_config = ConfigDict(frozen=True)

    unit_multiplier: str
    statement_scope: StatementScope


class EarningsValue(BaseModel):
    """실적 한 칸. `earnings_fact` 한 행이 된다."""

    model_config = ConfigDict(frozen=True)

    metric: str
    amount_basis: str
    statement_scope: str
    period_end: date
    current_amount: Decimal
    prior_year_amount: Decimal | None
    currency: str
    source_account_id: str | None
    source_account_name: str


class EarningsFetch(BaseModel):
    """실적 조회 한 번. 계보에 남길 값과 추출한 지표를 함께 담는다."""

    model_config = ConfigDict(frozen=True)

    rcept_no: str
    stock_code: str
    release_type: str
    values: tuple[EarningsValue, ...]
    metadata: dict[str, Any]
    started_at: datetime
    completed_at: datetime


def _day(value: str) -> date:
    """`YYYYMMDD`. `strptime`은 naive datetime을 만들어 쓰지 않는다."""
    text = str(value).strip()
    if len(text) != 8 or not text.isdigit():
        raise DartPayloadError(f"rcept_dt must be YYYYMMDD, got {value!r}")
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:]))
    except ValueError:
        raise DartPayloadError(f"rcept_dt is not a real date: {value!r}") from None


def _amount(text: str, multiplier: Decimal = Decimal(1)) -> Decimal | None:
    """원문 금액 한 칸. 값이 없으면 `None`이다.

    `-`는 값 없음이고 0이 아니다. 괄호는 음수 표기이며 쉼표는 자릿수 구분이다.
    """
    cleaned = str(text).strip().replace(",", "").replace(" ", "")
    if cleaned in ("", "-", "–", "—"):
        return None
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = cleaned[1:-1]
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        raise DartPayloadError(f"amount is not a number: {text!r}") from None
    return -(amount * multiplier) if negative else amount * multiplier


def _get(url: str, params: dict[str, str] | None = None) -> bytes:
    """GET 한 번. **API 키가 질의 문자열에 들어가므로 URL을 예외나 로그에 남기지 않는다.**"""
    target = f"{url}?{urlencode(params)}" if params else url
    request = Request(target, headers={"user-agent": USER_AGENT})
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return response.read()
    except HTTPError as error:
        raise DartHTTPError(error.code) from None
    except (URLError, HTTPException, OSError) as error:
        # 끊긴 연결(`RemoteDisconnected`)은 `URLError`가 아니라 그대로 올라온다. 재시도
        # 분류가 어긋나지 않게 여기서 한 종류로 모은다.
        raise ConnectionError(f"DART request failed: {error}") from None


def _json(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise DartPayloadError(f"DART returned a non-JSON body: {error}") from None
    if not isinstance(payload, dict):
        raise DartPayloadError("DART returned a JSON body that is not an object")
    return payload


def normalized_report_name(report_name: str) -> str:
    """`[기재정정]` 같은 접두사를 뗀 이름. 원문 이름은 저장할 때 그대로 쓴다."""
    return CORRECTION_PREFIX_PATTERN.sub("", report_name).strip()


def is_provisional(report_name: str) -> bool:
    return normalized_report_name(report_name) == PROVISIONAL_REPORT_NAME


def periodic_report(report_name: str) -> tuple[int, str, date] | None:
    """정기보고서면 (사업연도, 보고서코드, 기간 종료일). 아니면 `None`.

    `분기보고서 (2026.03)` 꼴이다. 1분기와 3분기가 같은 이름이라 월로 가른다.
    """
    match = PERIODIC_REPORT_PATTERN.match(normalized_report_name(report_name))
    if match is None:
        return None
    year, month = int(match.group(2)), int(match.group(3))
    code = PERIODIC_REPORTS.get(month)
    if code is None:
        return None
    return year, code, date(year, month, QUARTER_END_DAYS[month])


def _table_cells(document: Selector, table_id: str) -> list[str]:
    tables = document.css(f"table#{table_id}")
    if not tables:
        raise DartPayloadError(f"provisional document has no table #{table_id}")
    return [cell.get_all_text(strip=True) for cell in tables[0].css("td")]


def _unit_multiplier(cells: list[str]) -> Decimal:
    """표가 밝힌 단위. `단위 : 억원, %`처럼 온다."""
    for cell in cells:
        if not cell.startswith("단위"):
            continue
        # **긴 이름부터 본다.** `조원`·`억원`이 모두 `원`을 품고 있어 짧은 것을 먼저 맞추면
        # 1조가 1원이 된다.
        for name in sorted(UNIT_MULTIPLIERS, key=len, reverse=True):
            if name in cell:
                return UNIT_MULTIPLIERS[name]
        raise DartPayloadError(f"provisional document has an unknown unit: {cell!r}")
    raise DartPayloadError("provisional document has no unit cell")


def _period_ends(cells: list[str]) -> dict[str, date]:
    """실적기간 표에서 기간 기준별 종료일을 읽는다."""
    labels = {"당기실적": "period", "당기누계실적": "cumulative"}
    ends: dict[str, date] = {}
    for index, cell in enumerate(cells):
        basis = labels.get(cell)
        if basis is None or index + 3 >= len(cells):
            continue
        try:
            ends[basis] = date.fromisoformat(cells[index + 3])
        except ValueError:
            raise DartPayloadError(f"provisional period is not a date: {cells[index + 3]!r}") from None
    missing = set(labels.values()) - set(ends)
    if missing:
        raise DartPayloadError(f"provisional document has no period for {sorted(missing)}")
    return ends


def parse_provisional(xml: str) -> tuple[tuple[EarningsValue, ...], ProvisionalMeta]:
    """잠정실적 원문에서 세 지표를 읽는다.

    표를 **id로 집는다.** 정정 공시에는 정정 요약 표(`XFormD8_*`)가 앞에 붙어 있어서
    위치로 찾으면 조용히 다른 숫자를 읽는다.
    """
    document = Selector(content=xml)
    result_cells = _table_cells(document, RESULT_TABLE_ID)
    multiplier = _unit_multiplier(result_cells)
    period_ends = _period_ends(_table_cells(document, PERIOD_TABLE_ID))

    scope_cell = next((cell for cell in result_cells if "실적내용" in cell), "")
    # `1. 연결실적내용` / `2. 별도실적내용`. 연결이 없으면 별도다.
    scope = "OFS" if "별도" in scope_cell else "CFS"

    values: list[EarningsValue] = []
    metric_name = ""
    for index, cell in enumerate(result_cells):
        if cell in PROVISIONAL_METRICS:
            metric_name = cell
            continue
        basis = BASIS_LABELS.get(cell)
        if basis is None or not metric_name:
            continue

        window = result_cells[index + 1 : index + 1 + PROVISIONAL_VALUE_COLUMNS]
        if len(window) < PROVISIONAL_VALUE_COLUMNS:
            raise DartPayloadError(f"provisional row {metric_name}/{cell} has {len(window)} value cells")

        label = metric_name
        if basis == "cumulative":
            # 지표 하나가 당해실적·누계실적 두 줄을 쓰고 끝난다. 여기서 지표를 놓지 않으면
            # 바로 아래 `지배기업 소유주지분 순이익` 행이 당기순이익으로 이어 읽힌다.
            metric_name = ""

        current = _amount(window[CURRENT_VALUE_INDEX], multiplier)
        if current is None:
            # 공시에 없는 값이다. 0으로 바꾸지 않고 행을 만들지 않는다.
            continue
        values.append(
            EarningsValue(
                metric=PROVISIONAL_METRICS[label],
                amount_basis=basis,
                statement_scope=scope,
                period_end=period_ends[basis],
                current_amount=current,
                prior_year_amount=_amount(window[PRIOR_YEAR_VALUE_INDEX], multiplier),
                currency="KRW",
                source_account_id=None,
                source_account_name=label,
            )
        )

    if not values:
        raise DartPayloadError("provisional document produced no metrics")

    return tuple(values), ProvisionalMeta(unit_multiplier=str(multiplier), statement_scope=scope)


def parse_financials(payload: dict[str, Any], period_end: date, scope: str) -> tuple[EarningsValue, ...]:
    """재무제표 응답에서 세 지표를 읽는다. 계정은 이름이 아니라 `account_id`로 잡는다."""
    values: list[EarningsValue] = []
    for row in payload.get("list") or []:
        if row.get("sj_div") != "IS":
            # 포괄손익계산서(CIS)에도 순이익이 중복으로 온다. 손익계산서만 읽는다.
            continue
        metric = FINANCIAL_ACCOUNTS.get(str(row.get("account_id", "")).strip())
        if metric is None:
            continue

        currency = str(row.get("currency", "")).strip() or "KRW"
        account_name = str(row.get("account_nm", "")).strip()
        for basis, current_key, prior_key in (
            ("period", "thstrm_amount", "frmtrm_amount"),
            ("cumulative", "thstrm_add_amount", "frmtrm_add_amount"),
        ):
            current = _amount(row.get(current_key) or "")
            if current is None:
                continue
            values.append(
                EarningsValue(
                    metric=metric,
                    amount_basis=basis,
                    statement_scope=scope,
                    period_end=period_end,
                    current_amount=current,
                    prior_year_amount=_amount(row.get(prior_key) or ""),
                    currency=currency,
                    source_account_id=str(row["account_id"]).strip(),
                    source_account_name=account_name,
                )
            )
    return tuple(values)


def _company_of(disclosure: Disclosure) -> DartCompany:
    for company in DartCompany:
        if company.value == disclosure.stock_code:
            return company
    raise DartPayloadError(f"disclosure belongs to an unexpected company: {disclosure.stock_code}")


def _insert_source_record(
    cursor: Cursor,
    source_key: str,
    started_at: datetime,
    completed_at: datetime,
    status: str,
    record_count: int,
    metadata: dict[str, Any],
) -> int:
    cursor.execute(
        SOURCE_RECORD_INSERT,
        (
            "api",
            SOURCE,
            source_key,
            started_at,
            completed_at,
            status,
            record_count,
            # 원본은 남기지 않는다. API 키가 URL에 있으므로 요청도 남기지 않는다.
            None,
            json.dumps(metadata, ensure_ascii=False),
        ),
    )
    return cursor.fetchone()[0]


def pending_earnings(connection: Connection, stock_codes: tuple[str, ...], since: date) -> tuple[Disclosure, ...]:
    """실적 숫자를 아직 못 얻은 공시. 별도 작업 큐 없이 이 조회가 재시도 목록이다."""
    with connection.cursor() as cursor:
        cursor.execute(DISCLOSURE_EVENT_PENDING_SELECT, (list(stock_codes), since))
        rows = cursor.fetchall() or []
    return tuple(
        Disclosure(
            corp_code=row[0],
            corp_name=row[1],
            stock_code=row[2],
            corp_class=row[3],
            report_name=row[4],
            rcept_no=row[5],
            filer_name=row[6],
            receipt_date=row[7],
            remarks=row[8],
        )
        for row in rows
    )


class DartCollector:
    """OpenDART 수집기. API 키를 쥐고 공시 목록·원문·재무제표를 조회하고 저장한다.

    한 실행이 객체 하나다. 키는 이 객체가 사는 동안 안 변하는 값이라 생성자가 받고, 회사·구간·
    공시처럼 호출마다 바뀌는 것은 메서드 인자다. 전송(`_get`)과 파싱(`parse_provisional`·
    `parse_financials`), 그리고 DART와 무관하게 DB만 보는 `pending_earnings`는 밖에 둔다.
    """

    def __init__(self, api_key: SecretStr) -> None:
        if not api_key.get_secret_value():
            raise ValueError("DART API key is required")
        self._api_key = api_key

    def _call(self, path: str, params: dict[str, str]) -> bytes:
        """키를 붙여 한 번 부른다. **키가 질의 문자열에 들어가므로 URL을 예외나 로그에 남기지 않는다.**"""
        return _get(f"{OPENDART_BASE_URL}/{path}", {"crtfc_key": self._api_key.get_secret_value(), **params})

    def fetch_disclosures(
        self,
        company: DartCompany,
        begin_date: date,
        end_date: date,
    ) -> DisclosureFetch:
        """한 회사의 공시 목록을 받는다. 데이터 없음은 0건 성공이다."""
        started_at = datetime.now(UTC)
        rows: list[dict[str, Any]] = []
        total_count = 0
        page = 0

        while page < MAX_PAGES:
            page += 1
            payload = _json(
                self._call(
                    "list.json",
                    {
                        "corp_code": company.corp_code,
                        "bgn_de": begin_date.strftime("%Y%m%d"),
                        "end_de": end_date.strftime("%Y%m%d"),
                        "sort": "date",
                        "sort_mth": "desc",
                        "last_reprt_at": "N",
                        "page_no": str(page),
                        "page_count": str(PAGE_COUNT),
                    },
                )
            )
            status = str(payload.get("status", ""))
            if status == STATUS_NO_DATA:
                break
            if status != STATUS_OK:
                raise DartStatusError(status, str(payload.get("message", "")).strip())

            rows.extend(payload.get("list") or [])
            total_count = int(payload.get("total_count") or 0)
            if page >= int(payload.get("total_page") or 1):
                break

        # 장 상한에서 멈추면 조회 구간에 구멍이 남는다. 조용히 잘린 목록보다 실패가 낫다.
        if total_count > len(rows):
            raise DartPayloadError(
                f"DART returned {total_count} disclosures for {company.value} "
                f"but only {len(rows)} were read in {page} pages; narrow the window or raise MAX_PAGES"
            )

        try:
            disclosures = tuple(Disclosure.from_payload(row) for row in rows)
        except (KeyError, TypeError) as error:
            raise DartPayloadError(f"DART disclosure row is malformed: {error}") from None

        return DisclosureFetch(
            company=company.value,
            corp_code=company.corp_code,
            begin_date=begin_date,
            end_date=end_date,
            disclosures=disclosures,
            page_count=page,
            total_count=total_count,
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )

    def fetch_provisional(self, disclosure: Disclosure) -> EarningsFetch | None:
        """잠정실적 공시 원문을 받아 세 지표를 뽑는다. 원문이 아직 없으면 `None`이다."""
        started_at = datetime.now(UTC)
        raw = self._call("document.xml", {"rcept_no": disclosure.rcept_no})

        # ZIP이 아니면 오류 XML이다. HTTP는 200으로 온다.
        if raw[:2] != b"PK":
            text = raw.decode("utf-8", errors="replace")
            code = re.search(r"<status>(\d+)</status>", text)
            status = code.group(1) if code else ""
            if status == STATUS_NO_FILE:
                logger.info("Document for %s is not available yet", disclosure.rcept_no)
                return None
            raise DartStatusError(status, "document.xml did not return a ZIP")

        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = archive.namelist()
            if not names:
                raise DartPayloadError(f"document ZIP for {disclosure.rcept_no} is empty")
            content = archive.read(names[0])

        values, metadata = parse_provisional(content.decode("utf-8", errors="replace"))
        return EarningsFetch(
            rcept_no=disclosure.rcept_no,
            stock_code=disclosure.stock_code,
            release_type="provisional",
            values=values,
            metadata={
                "rcept_no": disclosure.rcept_no,
                "file_name": names[0],
                # 같은 접수번호의 첨부가 바뀌면 이 해시가 달라진다.
                "sha256": hashlib.sha256(raw).hexdigest(),
                **metadata.model_dump(mode="json"),
            },
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )

    def fetch_financials(self, disclosure: Disclosure) -> EarningsFetch | None:
        """정기보고서의 재무제표를 받는다.

        이 API는 접수번호가 아니라 회사·사업연도·보고서코드로 조회한다. **응답 `rcept_no`가
        처리 중인 공시와 다르면 저장하지 않는다.** 아직 반영 전이라는 뜻이고 다음 run이 다시 본다.

        연결(`CFS`)을 먼저 보고 없을 때만 별도(`OFS`)를 쓴다. 둘을 합치거나 대체하지 않는다.
        """
        periodic = periodic_report(disclosure.report_name)
        if periodic is None:
            return None
        year, report_code, period_end = periodic

        started_at = datetime.now(UTC)
        for scope in ("CFS", "OFS"):
            payload = _json(
                self._call(
                    "fnlttSinglAcntAll.json",
                    {
                        "corp_code": _company_of(disclosure).corp_code,
                        "bsns_year": str(year),
                        "reprt_code": report_code,
                        "fs_div": scope,
                    },
                )
            )
            status = str(payload.get("status", ""))
            if status == STATUS_NO_DATA:
                continue
            if status != STATUS_OK:
                raise DartStatusError(status, str(payload.get("message", "")).strip())

            rows = payload.get("list") or []
            answered = {str(row.get("rcept_no", "")).strip() for row in rows}
            if disclosure.rcept_no not in answered:
                logger.info(
                    "Financial statements for %s still answer with %s; retrying next run",
                    disclosure.rcept_no,
                    sorted(answered)[:1],
                )
                return None

            values = parse_financials(payload, period_end, scope)
            if not values:
                continue
            return EarningsFetch(
                rcept_no=disclosure.rcept_no,
                stock_code=disclosure.stock_code,
                release_type="periodic",
                values=values,
                metadata={
                    "rcept_no": disclosure.rcept_no,
                    "business_year": year,
                    "report_code": report_code,
                    "statement_scope": scope,
                    "row_count": len(rows),
                    # 재무제표 금액은 원 단위로 온다. 곱하지 않는다.
                    "unit_multiplier": "1",
                },
                started_at=started_at,
                completed_at=datetime.now(UTC),
            )

        return None

    def store_disclosures(
        self, connection: Connection, fetch: DisclosureFetch, detected_at: datetime | None = None
    ) -> int:
        """공시 이벤트를 저장하고 저장한 건수를 돌려준다.

        `detected_at`은 새 행에만 들어간다. 이미 있는 접수번호는 최초값을 지킨다.
        """
        detected_at = detected_at or fetch.completed_at
        with connection.cursor() as cursor:
            source_record_id = _insert_source_record(
                cursor,
                SOURCE_KEY_LIST,
                fetch.started_at,
                fetch.completed_at,
                "succeeded",
                len(fetch.disclosures),
                {
                    "company": fetch.company,
                    "corp_code": fetch.corp_code,
                    "begin_date": fetch.begin_date.isoformat(),
                    "end_date": fetch.end_date.isoformat(),
                    "page_count": fetch.page_count,
                    "total_count": fetch.total_count,
                },
            )
            execute_upserts(
                cursor,
                DISCLOSURE_EVENT_UPSERT,
                [
                    (
                        disclosure.corp_code,
                        disclosure.stock_code,
                        disclosure.corp_name,
                        disclosure.rcept_no,
                        disclosure.report_name,
                        disclosure.filer_name,
                        disclosure.corp_class,
                        disclosure.receipt_date,
                        detected_at,
                        disclosure.remarks,
                        source_record_id,
                    )
                    for disclosure in fetch.disclosures
                ],
            )
        return len(fetch.disclosures)

    def store_earnings(self, connection: Connection, fetch: EarningsFetch) -> int:
        """실적 지표를 저장하고 저장한 행 수를 돌려준다."""
        source_key = SOURCE_KEY_DOCUMENT if fetch.release_type == "provisional" else SOURCE_KEY_FINANCIALS
        with connection.cursor() as cursor:
            source_record_id = _insert_source_record(
                cursor,
                source_key,
                fetch.started_at,
                fetch.completed_at,
                "succeeded",
                len(fetch.values),
                fetch.metadata,
            )
            execute_upserts(
                cursor,
                EARNINGS_FACT_UPSERT,
                [
                    (
                        fetch.stock_code,
                        fetch.rcept_no,
                        fetch.release_type,
                        value.period_end,
                        value.statement_scope,
                        value.amount_basis,
                        value.metric,
                        value.current_amount,
                        value.prior_year_amount,
                        value.currency,
                        value.source_account_id,
                        value.source_account_name,
                        source_record_id,
                    )
                    for value in fetch.values
                ],
            )
        return len(fetch.values)
