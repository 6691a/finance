"""DTCC 공개 체결 파일에서 미국 빅테크·반도체 CDS 프리미엄을 수집한다.

설계는 `docs/collection/us-tech-cds-spread.md`다.

미국 SEC 규정(Regulation SBSR)에 따라 미국인이 낀 단일 종목 CDS 거래는 공개되고, DTCC가 그것을
UTC 하루치 zip 파일 하나로 묶어 **다음 날 00:15 UTC쯤** 올린다(2026-10-03 실측). 인증이 없다.

    https://kgc0418-tdw-data-0.s3.amazonaws.com/sec/eod/SEC_CUMULATIVE_CREDITS_YYYY_MM_DD.zip

`mof.py`와 같은 "파일 하나 = 수집 하나" 수집기다. 다른 점은 셋이다.

- **시세가 아니라 체결 기록이다.** 같은 날 같은 회사·만기에 체결이 여럿이면 가운데 값(중앙값)
  하나를 저장하고, 체결이 없으면 그날 값이 없다. 거래 규모는 `5,000,000+`로 가려져 있어 규모로
  가중할 수 없다.
- **보험료가 적힌 체결만 쓴다.** 대부분의 체결은 선지급 달러(`Other payment amount`)만 있고 거래
  규모가 가려져 보험료로 되돌릴 수 없다(대상 열 곳의 새 체결 892건 중 보험료 적힌 것 171건).
- **회사 이름 철자가 제각각이다.** `ORACLE CORPORATION`·`Oracle Corporation`·`Oracle Cop`이 같은
  회사다. 허용 철자를 글자 그대로 대조하고, 열쇠말은 있는데 목록에 없는 철자는 실패시킨다 —
  새 철자 하나로 그 회사 값이 조용히 빠지는 것을 막는다.

오래된 파일은 DTCC가 지운다(2024-09-03은 있고 2024-08-15는 403). 그래서 체결 건별 원본을
저장하지 않는 것은 "언제든 다시 받을 수 있어서"가 아니라 "가운데 값 외에는 쓸 곳이 없어서"다.
"""

import csv
import io
import json
import statistics
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Self
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import AwareDatetime, BaseModel, ConfigDict, field_validator

from modules.db import Connection
from modules.sql import read_sql
from modules.utility import normalize_to_utc

DTCC_URL = "https://kgc0418-tdw-data-0.s3.amazonaws.com/sec/eod"
SOURCE = "dtcc"

USER_AGENT = "news-collector/1.0 (+https://pddata.dtcc.com/ppd/secdashboard)"
REQUEST_TIMEOUT_SECONDS = 60

# 보험료는 소수(0.0215 = 215bp)로 오고 퍼센트로 저장한다. `fred`의 `HY_OAS`와 같은 눈금이라
# 한 쿼리로 비교된다.
SERIES_UNIT = "Percent"
PERCENT = Decimal(100)
VALUE_QUANTUM = Decimal("0.0001")

# 보험료 표기. `3`이 소수다. 대상 회사 체결에서 다른 표기가 오면 실패시킨다 — 다른 표기를
# 소수로 읽으면 값이 백 배·만 배 틀린다.
DECIMAL_NOTATION = "3"

# 보험료 단위가 일관된 체결 장소. 2026-09-22~10-02 실측에서 GSBS 528건(5~445bp)과 TPSB 344건
# (29~800bp)은 소수 표기가 맞았다. 상대 거래 `BILT`는 같은 날 오라클 5년물을 0.0249와 0.000223으로
# 적어 단위가 100배씩 흔들리고, `XOFF`는 보험료 칸에 가격(0.99)을 적는다. 이 둘과 그 밖의 장소는
# 쓰지 않고 건수만 남긴다.
QUOTED_VENUES = frozenset({"GSBS", "TPSB"})

# 행의 종류. 새 체결과 그 정정·취소만 그날의 가격이다. 해지·변경·부활 등은 이미 있는 계약의 일이다.
NEW_TRADE = "NEWT"
CORRECTION = "CORR"
CANCELLATION = "EROR"
KNOWN_ACTIONS = frozenset({NEW_TRADE, CORRECTION, CANCELLATION, "MODI", "TERM", "REVI", "PRTO", "VALU", "POSC"})

# 이름 자리에 회사명 대신 `ORACLE CDS USD SR 5.25Y D14` 같은 약식 상품명이 오는 행이 있다(9일에
# 일곱 건). `ADVANCED CDS …`처럼 어느 회사인지 안 갈리는 것도 있어 쓰지 않고 건수만 남긴다.
SHORT_NAME_MARKER = " CDS USD "


class CdsTenor(StrEnum):
    """표준 만기. 값은 시계열 ID의 꼬리이고 `years`가 만기 계산에 쓰인다."""

    years: int

    def __new__(cls, suffix: str, years: int) -> Self:
        member = str.__new__(cls, suffix)
        member._value_ = suffix
        member.years = years
        return member

    Y1 = ("1Y", 1)
    Y3 = ("3Y", 3)
    Y5 = ("5Y", 5)

    @property
    def maturity_months(self) -> int:
        return self.years * 12


class CdsCompany(StrEnum):
    """수집 대상 회사. 값은 티커이고 시계열 ID `CDS_<티커>_<만기>`의 가운데가 된다.

    - `aliases`: `normalize_name`을 거친 허용 철자. 글자 그대로 대조한다.
    - `keywords`: 이 낱말이 다 들어간 이름은 이 회사일 수 있다. 허용 철자에 없으면 실패시킨다.
      **부분 문자열이 아니라 낱말 단위다** — `META`가 `METLEN`·`METAL`에 걸리지 않게.

    철자는 2026-09-22~10-02 파일 9개에서 실제로 나온 것 전부다. 회사를 늘리면
    `indicator_series` 시드를 같은 커밋에서 늘린다(`tests/migrations/test_indicator_series_catalog.py`).
    """

    label: str
    aliases: frozenset[str]
    keywords: tuple[frozenset[str], ...]

    def __new__(cls, ticker: str, label: str, aliases: tuple[str, ...], keywords: tuple[tuple[str, ...], ...]) -> Self:
        member = str.__new__(cls, ticker)
        member._value_ = ticker
        member.label = label
        member.aliases = frozenset(aliases)
        member.keywords = tuple(frozenset(words) for words in keywords)
        return member

    ORCL = ("ORCL", "오라클", ("ORACLE CORPORATION", "ORACLE COP"), (("ORACLE",),))
    NVDA = ("NVDA", "엔비디아", ("NVIDIA CORPORATION", "NVIDIA CORP"), (("NVIDIA",),))
    GOOGL = ("GOOGL", "알파벳(구글)", ("ALPHABET INC",), (("ALPHABET",), ("GOOGLE",)))
    AMZN = ("AMZN", "아마존", ("AMAZON COM INC",), (("AMAZON",),))
    AVGO = ("AVGO", "브로드컴", ("BROADCOM INC",), (("BROADCOM",),))
    AMD = ("AMD", "AMD", ("ADVANCED MICRO DEVICES INC",), (("ADVANCED", "MICRO"),))
    META = ("META", "메타", ("META PLATFORMS INC",), (("META",),))
    MSFT = ("MSFT", "마이크로소프트", ("MICROSOFT CORPORATION", "MICROSOFT CORP"), (("MICROSOFT",),))
    AAPL = ("AAPL", "애플", ("APPLE INC",), (("APPLE",),))
    INTC = ("INTC", "인텔", ("INTEL CORPORATION", "INTEL CORP"), (("INTEL",),))


def series_id(company: CdsCompany, tenor: CdsTenor) -> str:
    return f"CDS_{company.value}_{tenor.value}"


CDS_SERIES: tuple[str, ...] = tuple(series_id(company, tenor) for company in CdsCompany for tenor in CdsTenor)

# 헤더 줄의 열 110개(2026-10-03 실측). 저장하지 않는 열까지 전부 둔다 — DTCC가 열을 추가하거나
# 빼면 값이 조용히 옆 칸으로 밀리므로, 쓰는 열만 확인해서는 그 사고를 잡을 수 없다.
EXPECTED_HEADER: tuple[str, ...] = (
    "Dissemination Identifier",
    "Original Dissemination Identifier",
    "Action type",
    "Event type",
    "Event timestamp",
    "Amendment indicator",
    "Asset Class",
    "Product name",
    "Cleared",
    "Mandatory clearing indicator",
    "Execution Timestamp",
    "Effective Date",
    "Expiration Date",
    "Maturity date of the underlier",
    "Non-standardized term indicator",
    "Platform identifier",
    "Prime brokerage transaction indicator",
    "Block trade election indicator",
    "Large notional off-facility swap election indicator",
    "Notional amount-Leg 1",
    "Notional amount-Leg 2",
    "Notional currency-Leg 1",
    "Notional currency-Leg 2",
    "Notional quantity-Leg 1",
    "Notional quantity-Leg 2",
    "Total notional quantity-Leg 1",
    "Total notional quantity-Leg 2",
    "Quantity frequency multiplier-Leg 1",
    "Quantity frequency multiplier-Leg 2",
    "Quantity unit of measure-Leg 1",
    "Quantity unit of measure-Leg 2",
    "Quantity frequency-Leg 1",
    "Quantity frequency-Leg 2",
    "Notional amount in effect on associated effective date-Leg 1",
    "Notional amount in effect on associated effective date-Leg 2",
    "Effective date of the notional amount-Leg 1",
    "Effective date of the notional amount-Leg 2",
    "End date of the notional amount-Leg 1",
    "End date of the notional amount-Leg 2",
    "Call amount",
    "Call currency",
    "Put amount",
    "Put currency",
    "Exchange rate",
    "Exchange rate basis",
    "First exercise date",
    "Fixed rate-Leg 1",
    "Fixed rate-Leg 2",
    "Option Premium Amount",
    "Option Premium Currency",
    "Price",
    "Price unit of measure",
    "Spread-Leg 1",
    "Spread-Leg 2",
    "Spread currency-Leg 1",
    "Spread currency-Leg 2",
    "Strike Price",
    "Strike price currency/currency pair",
    "Post-priced swap indicator",
    "Price currency",
    "Price notation",
    "Spread notation-Leg 1",
    "Spread notation-Leg 2",
    "Strike price notation",
    "Fixed rate day count convention-leg 1",
    "Fixed rate day count convention-leg 2",
    "Floating rate day count convention-leg 1",
    "Floating rate day count convention-leg 2",
    "Floating rate reset frequency period-leg 1",
    "Floating rate reset frequency period-leg 2",
    "Floating rate reset frequency period multiplier-leg 1",
    "Floating rate reset frequency period multiplier-leg 2",
    "Other payment amount",
    "Fixed rate payment frequency period-Leg 1",
    "Floating rate payment frequency period-Leg 1",
    "Fixed rate payment frequency period-Leg 2",
    "Floating rate payment frequency period-Leg 2",
    "Fixed rate payment frequency period multiplier-Leg 1",
    "Floating rate payment frequency period multiplier-Leg 1",
    "Fixed rate payment frequency period multiplier-Leg 2",
    "Floating rate payment frequency period multiplier-Leg 2",
    "Other payment type",
    "Other payment currency",
    "Settlement currency-Leg 1",
    "Settlement currency-Leg 2",
    "Settlement location",
    "Collateralisation category",
    "Custom basket indicator",
    "Index factor",
    "Underlier ID-Leg 1",
    "Underlier ID-Leg 2",
    "Underlier ID source-Leg 1",
    "Underlying Asset Name",
    "Underlying asset subtype or underlying contract subtype-Leg 1",
    "Underlying asset subtype or underlying contract subtype-Leg 2",
    "Embedded Option type",
    "Option Type",
    "Option Style",
    "Package indicator",
    "Package transaction price",
    "Package transaction price currency",
    "Package transaction price notation",
    "Package transaction spread",
    "Package transaction spread currency",
    "Package transaction spread notation",
    "Physical delivery location-Leg 1",
    "Delivery Type",
    "Unique Product Identifier",
    "UPI FISN",
    "UPI Underlier Name",
)
COLUMN_COUNT = len(EXPECTED_HEADER)


def _column(name: str) -> int:
    return EXPECTED_HEADER.index(name)


DISSEMINATION_ID = _column("Dissemination Identifier")
ORIGINAL_DISSEMINATION_ID = _column("Original Dissemination Identifier")
ACTION_TYPE = _column("Action type")
EVENT_TIMESTAMP = _column("Event timestamp")
EXECUTION_TIMESTAMP = _column("Execution Timestamp")
EXPIRATION_DATE = _column("Expiration Date")
SPREAD = _column("Spread-Leg 1")
SPREAD_NOTATION = _column("Spread notation-Leg 1")
PLATFORM = _column("Platform identifier")
ASSET_NAME = _column("Underlying Asset Name")


class DtccHTTPError(RuntimeError):
    """DTCC가 2xx가 아닌 상태로 응답했다.

    **403은 "아직 안 올라왔다"와 "지워졌다"가 같은 응답이다**(S3). 재시도 여부는 DAG가 정한다.
    """

    def __init__(self, status: int, url: str) -> None:
        # 인증이 없어 URL에 비밀이 없다. 그대로 남긴다.
        super().__init__(f"DTCC request failed with HTTP {status}: {url}")
        self.status = status


class DtccPayloadError(ValueError):
    """파일이 계약을 지키지 않았다. 재시도해도 같은 결과다."""


class CdsObservation(BaseModel):
    """회사·만기·체결일 하나의 가운데 보험료(퍼센트)."""

    model_config = ConfigDict(frozen=True)

    company: CdsCompany
    tenor: CdsTenor
    observation_date: date
    value: Decimal
    print_count: int

    @property
    def series_id(self) -> str:
        return series_id(self.company, self.tenor)


class CdsDay(BaseModel):
    """파일 하나를 정규화한 결과와, 무엇을 왜 버렸는지의 건수."""

    model_config = ConfigDict(frozen=True)

    observations: tuple[CdsObservation, ...]
    file_row_count: int
    # 대상 회사의 보험료 적힌 체결 중 체결일이 파일 날짜와 다른 것. 저장하면 그날 값을 그 한 건으로
    # 덮어쓴다. 실측 0건이다.
    late_print_count: int
    # 대상 회사의 보험료 적힌 체결 중 `QUOTED_VENUES` 밖에서 체결된 것.
    off_venue_print_count: int
    # 대상 회사의 보험료 적힌 체결 중 표준 1·3·5년 만기가 아닌 것(반년 전 5년물 등).
    off_tenor_print_count: int
    short_name_row_count: int


class DtccResponse(BaseModel):
    """파일 하나를 받은 결과."""

    model_config = ConfigDict(frozen=True)

    trade_date: date
    body: bytes
    status: int
    started_at: AwareDatetime
    completed_at: AwareDatetime

    @field_validator("started_at", "completed_at")
    @classmethod
    def normalize_to_utc(cls, moment: datetime) -> datetime:
        return normalize_to_utc(moment)


def file_name(trade_date: date) -> str:
    return f"SEC_CUMULATIVE_CREDITS_{trade_date:%Y_%m_%d}"


def build_url(trade_date: date) -> str:
    return f"{DTCC_URL}/{file_name(trade_date)}.zip"


def standard_maturity(trade_date: date, tenor: CdsTenor) -> date:
    """그날 "지금 거래되는 N년물"의 만기일.

    표준 CDS는 3월 20일과 9월 20일에 만기가 반년씩 밀린다. 9/20~이듬해 3/19는 12월 20일,
    3/20~9/19는 6월 20일 만기다. 2026-09-22의 5년물은 2031-12-20이다.
    """
    if (trade_date.month, trade_date.day) >= (9, 20):
        return date(trade_date.year + tenor.years, 12, 20)
    if (trade_date.month, trade_date.day) >= (3, 20):
        return date(trade_date.year + tenor.years, 6, 20)
    return date(trade_date.year - 1 + tenor.years, 12, 20)


def normalize_name(name: str) -> str:
    """대문자로 바꾸고 `;`·`,`·`.`을 공백으로 바꾼 뒤 공백을 하나로 줄인다.

    `AMAZON.COM;INC.`와 `Amazon.com Inc`가 둘 다 `AMAZON COM INC`가 된다.
    """
    for mark in ";,.":
        name = name.replace(mark, " ")
    return " ".join(name.upper().split())


def match_company(name: str) -> CdsCompany | None:
    """이름이 대상 회사면 그 회사, 아니면 None. 모르는 철자면 실패시킨다."""
    normalized = normalize_name(name)
    words = set(normalized.split())
    for company in CdsCompany:
        if normalized in company.aliases:
            return company
    for company in CdsCompany:
        if any(keyword <= words for keyword in company.keywords):
            raise DtccPayloadError(
                f"unknown spelling {name!r} looks like {company.value}; add it to CdsCompany aliases or rule it out"
            )
    return None


def _rows(body: bytes, trade_date: date) -> list[list[str]]:
    """zip을 풀고 헤더를 검증한 뒤 데이터 행만 돌려준다."""
    expected_member = f"{file_name(trade_date)}.csv"
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            members = archive.namelist()
            if members != [expected_member]:
                raise DtccPayloadError(f"DTCC zip holds {members!r}, expected [{expected_member!r}]")
            text = archive.read(expected_member).decode("utf-8-sig")
    except zipfile.BadZipFile as error:
        raise DtccPayloadError(f"DTCC response for {trade_date} is not a zip file") from error
    except UnicodeDecodeError as error:
        raise DtccPayloadError(f"DTCC CSV for {trade_date} is not UTF-8") from error

    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise DtccPayloadError(f"DTCC CSV for {trade_date} is empty")
    if tuple(rows[0]) != EXPECTED_HEADER:
        # 열이 늘거나 이름이 바뀌면 값이 옆 칸으로 밀린다. 저장하지 않는 열까지 전부 대조한다.
        raise DtccPayloadError(f"DTCC changed the CSV header for {trade_date}")

    data = rows[1:]
    if not data:
        # 조용한 날이 아니라 빈 파일이다. 대상 회사 체결이 0인 것과 다르다.
        raise DtccPayloadError(f"DTCC CSV for {trade_date} has no rows")
    for row in data:
        if len(row) != COLUMN_COUNT:
            raise DtccPayloadError(f"DTCC row has {len(row)} cells, expected {COLUMN_COUNT}")
        if row[ACTION_TYPE] not in KNOWN_ACTIONS:
            raise DtccPayloadError(f"unknown action type {row[ACTION_TYPE]!r}")
    return data


def _effective_trades(rows: list[list[str]]) -> list[list[str]]:
    """그날의 새 체결에 정정·취소를 반영한다.

    `EROR`가 가리키는 체결은 버리고, `CORR`는 원래 체결을 대신한다(여럿이면 마지막 사건 시각).
    원래 체결이 이 파일에 없는 정정은 지난날의 것이라 쓰지 않는다.
    """
    cancelled = {row[ORIGINAL_DISSEMINATION_ID] for row in rows if row[ACTION_TYPE] == CANCELLATION}
    corrections: dict[str, list[str]] = {}
    for row in sorted((row for row in rows if row[ACTION_TYPE] == CORRECTION), key=lambda row: row[EVENT_TIMESTAMP]):
        corrections[row[ORIGINAL_DISSEMINATION_ID]] = row

    return [
        corrections.get(row[DISSEMINATION_ID], row)
        for row in rows
        if row[ACTION_TYPE] == NEW_TRADE and row[DISSEMINATION_ID] not in cancelled
    ]


def _spread_percent(row: list[str]) -> Decimal:
    if row[SPREAD_NOTATION] != DECIMAL_NOTATION:
        raise DtccPayloadError(f"unknown spread notation {row[SPREAD_NOTATION]!r} for {row[ASSET_NAME]!r}")
    try:
        spread = Decimal(row[SPREAD])
    except InvalidOperation as error:
        raise DtccPayloadError(f"non-numeric spread {row[SPREAD]!r} for {row[ASSET_NAME]!r}") from error
    if not spread.is_finite() or spread < 0:
        raise DtccPayloadError(f"spread {row[SPREAD]!r} for {row[ASSET_NAME]!r} is not a finite non-negative number")
    return spread * PERCENT


def parse_day(body: bytes, trade_date: date) -> CdsDay:
    """파일 하나에서 회사·만기마다 그날의 가운데 보험료를 낸다."""
    rows = _rows(body, trade_date)
    maturities = {standard_maturity(trade_date, tenor).isoformat(): tenor for tenor in CdsTenor}

    prints: dict[tuple[CdsCompany, CdsTenor], list[Decimal]] = {}
    late = off_venue = off_tenor = 0
    short_names = sum(1 for row in rows if SHORT_NAME_MARKER in f" {normalize_name(row[ASSET_NAME])} ")

    for row in _effective_trades(rows):
        if SHORT_NAME_MARKER in f" {normalize_name(row[ASSET_NAME])} ":
            continue
        company = match_company(row[ASSET_NAME])
        if company is None or not row[SPREAD]:
            continue
        if row[PLATFORM] not in QUOTED_VENUES:
            off_venue += 1
            continue
        value = _spread_percent(row)
        if row[EXECUTION_TIMESTAMP][:10] != trade_date.isoformat():
            late += 1
            continue
        tenor = maturities.get(row[EXPIRATION_DATE])
        if tenor is None:
            off_tenor += 1
            continue
        prints.setdefault((company, tenor), []).append(value)

    observations = tuple(
        CdsObservation(
            company=company,
            tenor=tenor,
            observation_date=trade_date,
            value=Decimal(statistics.median(values)).quantize(VALUE_QUANTUM),
            print_count=len(values),
        )
        for (company, tenor), values in sorted(prints.items())
    )
    return CdsDay(
        observations=observations,
        file_row_count=len(rows),
        late_print_count=late,
        off_venue_print_count=off_venue,
        off_tenor_print_count=off_tenor,
        short_name_row_count=short_names,
    )


def fetch_day(trade_date: date) -> DtccResponse:
    url = build_url(trade_date)
    started_at = datetime.now(UTC)
    try:
        with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=REQUEST_TIMEOUT_SECONDS) as response:
            body = response.read()
            status = response.status
    except HTTPError as error:
        raise DtccHTTPError(error.code, url) from error
    except URLError as error:
        raise ConnectionError(f"DTCC request failed: {error.reason}") from error

    return DtccResponse(
        trade_date=trade_date,
        body=body,
        status=status,
        started_at=started_at,
        completed_at=datetime.now(UTC),
    )


SOURCE_RECORD_INSERT = read_sql("postgres", "source_record", "insert.sql")
OBSERVATION_UPSERT = read_sql("postgres", "indicator_observation", "upsert.sql")


def store_day(connection: Connection, response: DtccResponse) -> int:
    """파일 하나와 그 파일의 가운데 보험료를 저장하고 관측값 수를 돌려준다.

    파싱을 먼저 해서 형식 오류면 아무 것도 쓰지 않는다. 관측값이 0건이어도(미국 공휴일, 조용한 날)
    `source_record`는 남긴다 — "받았는데 없었다"와 "안 받았다"를 가른다.

    `payload`는 비운다. 원본이 zip이라 jsonb에 들어가지 않는다. 시계열마다 그날 체결 수는
    `metadata.print_counts`에 남긴다 — 한 건짜리 값과 열 건짜리 값을 가리는 자리는 거기뿐이다.
    """
    day = parse_day(response.body, response.trade_date)
    metadata = json.dumps(
        {
            "http_status": response.status,
            "url": build_url(response.trade_date),
            "trade_date": response.trade_date.isoformat(),
            "source_unit_name": "decimal spread",
            "file_row_count": day.file_row_count,
            "late_print_count": day.late_print_count,
            "off_venue_print_count": day.off_venue_print_count,
            "off_tenor_print_count": day.off_tenor_print_count,
            "short_name_row_count": day.short_name_row_count,
            "print_counts": {observation.series_id: observation.print_count for observation in day.observations},
        },
        ensure_ascii=False,
    )

    with connection.cursor() as cursor:
        cursor.execute(
            SOURCE_RECORD_INSERT,
            (
                "api",
                SOURCE,
                file_name(response.trade_date),
                response.started_at,
                response.completed_at,
                "succeeded",
                len(day.observations),
                None,
                metadata,
            ),
        )
        source_record_id = cursor.fetchone()[0]
        for observation in day.observations:
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
    return len(day.observations)
