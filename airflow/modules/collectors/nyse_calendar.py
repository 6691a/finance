"""NYSE 공식 페이지에서 미국 현물시장의 휴장일을 수집한다.

`market_session`의 `US_EQUITY` 행은 **이 모듈이 만든다.** KIS 해외결제일자조회는 휴장한
나라의 행을 아예 주지 않고 미래도 주지 않아서(`kis_market_calendar` 문서 참고) 개장 판정을
맡길 수 없다. 이 페이지는 3년치를 미리 고시한다.

## 페이지 계약 (2026-08-12 실측)

- `https://www.nyse.com/trade/hours-calendars`, 정적 HTML, 108KB. 표가 그대로 들어 있어
  scrapling `Fetcher`로 충분하다. 브라우저를 띄우는 `DynamicFetcher`는 필요 없다.
- 페이지에 `<table>`이 하나뿐이고 그게 휴장일 표다.
- 첫 행이 `['Holiday', '2026', '2027', '2028']`이다. **지원 연도는 열 헤더가 준다.**
- 셀은 `'Thursday, January 1'`이고 **연도가 없다.** 연도는 열이 준다.
- 섞이는 변형이 셋이다.
  - `'—*'` — 그 해에는 지키지 않는 휴일(2028년 신정이 토요일이다)
  - `'Friday, July 3 (Independence Day observed)'` — 괄호 주석
  - `'Thursday, November 26***'` — 조기 폐장 각주 마커
- 조기 폐장 날짜는 표가 아니라 각주 본문에 있다. 이번 범위는 조기 폐장을 개장으로 보므로
  각주를 읽지 않는다.

## 로케일

`strptime`의 `%B`/`%A`는 실행 환경의 `LC_TIME`을 탄다. 컨테이너 로케일이 바뀌면 조용히
실패하므로 월 이름과 요일 이름 표를 직접 둔다. `boe.py`와 같은 이유다.

셀의 요일 이름은 버리지 않고 **계산한 날짜의 요일과 대조한다.** 열과 값이 어긋나면(연도를
잘못 붙이면) 요일이 먼저 틀어진다.
"""

import json
import logging
import re
from datetime import UTC, date, datetime, timedelta

from pydantic import BaseModel, ConfigDict
from scrapling import Selector
from scrapling.fetchers import Fetcher

from modules.collectors.kis import Connection
from modules.sql import read_sql
from modules.upsert import execute_upserts

logger = logging.getLogger(__name__)

CALENDAR_URL = "https://www.nyse.com/trade/hours-calendars"
SOURCE = "nyse"
SOURCE_KEY = "hours_calendars"

US_EQUITY = "US_EQUITY"

MONTH_NAMES: tuple[str, ...] = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
MONTH_NUMBERS: dict[str, int] = {name: number for number, name in enumerate(MONTH_NAMES, start=1)}

# `date.weekday()`와 같은 순서다. 월요일이 0이다.
WEEKDAY_NAMES: tuple[str, ...] = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)

# 그 해에 지키지 않는 휴일 칸. em dash와 hyphen을 모두 받는다.
EMPTY_CELL_MARKS = ("—", "–", "-")

MARKET_SESSION_US_UPSERT = read_sql("postgres", "market_session", "upsert_us_session.sql")
SOURCE_RECORD_INSERT = read_sql("postgres", "source_record", "insert.sql")

REQUEST_TIMEOUT_SECONDS = 30


class NyseParseError(ValueError):
    """페이지가 예상한 표 계약을 지키지 않았다. 재시도해도 같은 결과다."""


class NyseFetch(BaseModel):
    """페이지 한 버전."""

    model_config = ConfigDict(frozen=True)

    url: str
    html: str
    status: int
    started_at: datetime
    completed_at: datetime


class NyseCalendar(BaseModel):
    """표에서 읽은 지원 연도와 완전 휴장일."""

    model_config = ConfigDict(frozen=True)

    years: tuple[int, ...]
    holidays: tuple[date, ...]


class SessionDay(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_date: date
    open_day: bool


def fetch_calendar(url: str = CALENDAR_URL) -> NyseFetch:
    """공식 페이지를 받는다. 파싱은 하지 않는다."""
    started_at = datetime.now(UTC)
    page = Fetcher.get(url, stealthy_headers=True, timeout=REQUEST_TIMEOUT_SECONDS)
    if page.status != 200:
        raise NyseParseError(f"NYSE returned HTTP {page.status} for {url}")
    return NyseFetch(
        url=url,
        html=page.html_content,
        status=page.status,
        started_at=started_at,
        completed_at=datetime.now(UTC),
    )


def _clean(cell: str) -> str:
    """각주 마커와 괄호 주석을 벗긴다."""
    return re.sub(r"\([^)]*\)", "", cell).replace("*", "").strip()


def parse_cell(cell: str, year: int) -> date | None:
    """`'Thursday, January 1'`을 날짜로 바꾼다. 그 해에 없는 휴일이면 `None`이다."""
    text = _clean(cell)
    if not text or text.startswith(EMPTY_CELL_MARKS):
        return None

    weekday_name, separator, remainder = text.partition(",")
    if not separator:
        raise NyseParseError(f"NYSE holiday cell has no weekday: {cell!r}")

    parts = remainder.split()
    if len(parts) != 2:
        raise NyseParseError(f"NYSE holiday cell is not 'Weekday, Month Day': {cell!r}")

    month_name, day_text = parts
    month = MONTH_NUMBERS.get(month_name)
    if month is None:
        raise NyseParseError(f"NYSE holiday cell has an unknown month: {cell!r}")
    if not day_text.isdigit():
        raise NyseParseError(f"NYSE holiday cell has a non-numeric day: {cell!r}")

    try:
        day = date(year, month, int(day_text))
    except ValueError:
        raise NyseParseError(f"NYSE holiday cell is not a real date in {year}: {cell!r}") from None

    # 열(연도)과 값이 어긋나면 요일이 먼저 틀어진다. 조용히 하루 밀리는 것보다 멈추는 게 낫다.
    expected = WEEKDAY_NAMES[day.weekday()]
    if weekday_name.strip() != expected:
        raise NyseParseError(f"NYSE holiday cell says {weekday_name.strip()} but {day} is a {expected}: {cell!r}")
    return day


def parse_calendar(html: str) -> NyseCalendar:
    """페이지에서 지원 연도와 완전 휴장일을 뽑는다."""
    tables = Selector(content=html).css("table")
    if len(tables) != 1:
        raise NyseParseError(f"NYSE page must have exactly one table, found {len(tables)}")

    rows = tables[0].css("tr")
    if len(rows) < 2:
        raise NyseParseError("NYSE holiday table has no data rows")

    header = [cell.get_all_text(strip=True) for cell in rows[0].css("th, td")]
    if len(header) < 2:
        raise NyseParseError(f"NYSE holiday table header is too short: {header}")

    years: list[int] = []
    for label in header[1:]:
        if not label.isdigit():
            raise NyseParseError(f"NYSE holiday table header is not a year: {label!r}")
        years.append(int(label))

    holidays: set[date] = set()
    for row in rows[1:]:
        cells = [cell.get_all_text(strip=True) for cell in row.css("th, td")]
        if len(cells) != len(header):
            raise NyseParseError(f"NYSE holiday row has {len(cells)} cells, expected {len(header)}")
        for year, cell in zip(years, cells[1:], strict=True):
            day = parse_cell(cell, year)
            if day is not None:
                holidays.add(day)

    if not holidays:
        raise NyseParseError("NYSE holiday table produced no holidays")

    return NyseCalendar(years=tuple(years), holidays=tuple(sorted(holidays)))


def session_days(calendar: NyseCalendar) -> tuple[SessionDay, ...]:
    """지원 연도의 **모든 날짜**에 개장 여부를 붙인다.

    휴장일만 저장하지 않는 이유는 조회하는 쪽 때문이다. 행이 없다는 것이 "개장"인지 "아직
    모른다"인지 구분되어야 하고, 그러려면 아는 날짜를 전부 적어 두는 편이 단순하다.
    연도 셋이면 1,096행 남짓이다.
    """
    holidays = set(calendar.holidays)
    days: list[SessionDay] = []
    for year in calendar.years:
        day = date(year, 1, 1)
        while day.year == year:
            # `weekday()`는 월요일이 0이라 5·6이 토·일이다.
            weekend = day.weekday() >= 5
            days.append(SessionDay(session_date=day, open_day=not weekend and day not in holidays))
            day += timedelta(days=1)
    return tuple(days)


def store_calendar(connection: Connection, fetch: NyseFetch, calendar: NyseCalendar) -> int:
    """미국 행을 저장하고 저장한 날짜 수를 돌려준다.

    **결제일 컬럼과 `verification_source_record_id`는 건드리지 않는다.** 그 값은 KIS
    해외결제일자조회가 채운다.
    """
    days = session_days(calendar)
    verified_at = fetch.completed_at

    with connection.cursor() as cursor:
        cursor.execute(
            SOURCE_RECORD_INSERT,
            (
                "crawl",
                SOURCE,
                SOURCE_KEY,
                fetch.started_at,
                fetch.completed_at,
                "succeeded",
                len(days),
                # HTML은 jsonb에 넣을 수 없다. 어느 페이지의 어느 연도를 읽었는지만 남긴다.
                None,
                json.dumps(
                    {
                        "url": fetch.url,
                        "status": fetch.status,
                        "years": list(calendar.years),
                        "holiday_count": len(calendar.holidays),
                        "holidays": [day.isoformat() for day in calendar.holidays],
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        source_record_id = cursor.fetchone()[0]
        execute_upserts(
            cursor,
            MARKET_SESSION_US_UPSERT,
            [(day.session_date, day.open_day, verified_at, source_record_id) for day in days],
        )
    return len(days)
