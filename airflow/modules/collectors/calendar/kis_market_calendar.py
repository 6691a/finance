"""한국투자증권 API에서 국내 휴장일과 해외 결제일을 수집한다.

저장 대상은 `market_session` 테이블이다. 정의의 원본은 백엔드의 `apps/models/market.py`이며
여기 SQL의 컬럼 이름은 `tests/collectors/test_kis_market_calendar.py`가 그 모델 metadata와
대조한다.

토큰 발급과 HTTP는 `kis.py`가 이미 갖고 있어 그대로 쓴다. 여기 있는 것은 이 두 조회에만
있는 규칙이다. 자격 증명과 토큰은 `KisMarketCalendarCollector`가 쥐고, 기준일처럼 호출마다
바뀌는 것은 메서드 인자다.

## 두 조회의 성격이 다르다

| | 국내휴장일조회 | 해외결제일자조회 |
| --- | --- | --- |
| 응답 단위 | 기준일부터 앞으로 하루 1행 | 기준일 하루의 국가·시장별 행 |
| 미래 | **1년 넘게 준다** | **주지 않는다.** 당일까지다 |
| 개장 여부 | `opnd_yn`에 있다 | **없다.** 행의 존재가 신호다 |
| 페이징 | 24행마다 다음 장 | 실측에서는 한 장 |

**해외 응답으로 개장 여부를 판정하지 않는다.** 휴장한 나라는 행이 오지 않고(2026-07-03
미국 독립기념일 대체휴장에 US 행 0개, 다른 나라는 정상), 미래를 조회하면 값 없음과 같은
0행 응답이 온다. 그래서 미국 판정은 `nyse_calendar.py`가 소유하고 이 모듈은 결제일만 채운다.

## 연속조회

**총 건수를 주지 않는다.** 다음 장이 있는지는 응답 **헤더** `tr_cont`(`M`/`F`)로 알리고,
`ctx_area_fk`/`ctx_area_nk`를 다음 요청에 되먹인다. 받은 행 수를 대조할 상대가 없으므로
잘림을 감지할 수 없다. 대신 두 가지로 무한 루프와 과다 호출을 막는다.

- **페이지 상한(`MAX_PAGES`)에서 멈춘다.** 국내는 실측에서 12장(288행, 2027-05-26까지)에도
  끝나지 않았다. 끝이 오기를 기다리는 것이 아니라 필요한 만큼 받고 그만두는 것이므로
  상한 도달은 정상이다.
- `ctx_area_nk`가 직전 장과 같은데 계속 이어지면 실패시킨다. 커서가 멈춘 것이고 더 돌면
  같은 행만 쌓인다.
"""

import json
import logging
import time
from datetime import UTC, date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

from modules.collectors.kis import (
    SOURCE,
    Connection,
    KisPayloadError,
    KisResultError,
    send_get,
)
from modules.sql import read_sql
from modules.upsert import execute_upserts

logger = logging.getLogger(__name__)

DOMESTIC_PATH = "/uapi/domestic-stock/v1/quotations/chk-holiday"
DOMESTIC_TR_ID = "CTCA0903R"
DOMESTIC_SOURCE_KEY = "domestic_holiday"

OVERSEAS_PATH = "/uapi/overseas-stock/v1/quotations/countries-holiday"
OVERSEAS_TR_ID = "CTOS5011R"
OVERSEAS_SOURCE_KEY = "overseas_settlement"

# 국내는 한 장에 24행(=24일)이 온다. 16장이면 1년치가 조금 넘는다. 그 이상은 쓰는 곳이 없다.
MAX_PAGES = 16

# KIS 공식 예제도 장 사이에 지연을 둔다. 유량 제한에 걸리지 않게 한다.
PAGE_DELAY_SECONDS = 0.5

# 다음 장이 있다는 뜻의 헤더 값.
CONTINUE_FLAGS = frozenset({"M", "F"})

# 미국 현물시장 식별자. `market_session.market_code`와 같은 값이다.
US_EQUITY = "US_EQUITY"
US_COUNTRY_CODE = "US"

MARKET_SESSION_DOMESTIC_UPSERT = read_sql("postgres", "market_session", "upsert_domestic.sql")
MARKET_SESSION_SETTLEMENT_UPDATE = read_sql("postgres", "market_session", "update_settlement.sql")
SOURCE_RECORD_INSERT = read_sql("postgres", "source_record", "insert.sql")


class KisCursorError(RuntimeError):
    """연속조회 커서가 멈췄다. 계속 이어진다고 하면서 같은 커서를 준다."""


def _flag(value: str, field: str) -> bool:
    """KIS의 `Y`/`N`. 모르는 값은 조용히 넘기지 않는다."""
    if value == "Y":
        return True
    if value == "N":
        return False
    raise ValueError(f"{field} must be 'Y' or 'N', got {value!r}")


def _day(value: str, field: str) -> date:
    """`YYYYMMDD`. 빈 값이나 다른 모양은 실패시킨다.

    `strptime`을 쓰지 않는 이유는 그쪽이 naive datetime을 만들기 때문이다. 여기서 필요한
    것은 날짜뿐이고, 이 날짜의 기준 시간대는 시장 현지다.
    """
    if len(value) != 8 or not value.isdigit():
        raise ValueError(f"{field} must be YYYYMMDD, got {value!r}")
    try:
        return date(int(value[:4]), int(value[4:6]), int(value[6:]))
    except ValueError:
        raise ValueError(f"{field} is not a real date: {value!r}") from None


class DomesticDay(BaseModel):
    """국내휴장일조회의 한 행. 하루가 한 행이다."""

    model_config = ConfigDict(frozen=True)

    session_date: date
    weekday_code: str
    business_day: bool
    trading_day: bool
    open_day: bool
    settlement_day: bool

    @classmethod
    def from_payload(cls, row: dict[str, Any]) -> "DomesticDay":
        return cls(
            session_date=_day(row["bass_dt"], "bass_dt"),
            weekday_code=row["wday_dvsn_cd"],
            business_day=_flag(row["bzdy_yn"], "bzdy_yn"),
            trading_day=_flag(row["tr_day_yn"], "tr_day_yn"),
            open_day=_flag(row["opnd_yn"], "opnd_yn"),
            settlement_day=_flag(row["sttl_day_yn"], "sttl_day_yn"),
        )


class OverseasRow(BaseModel):
    """해외결제일자조회의 한 행. 국가·시장 하나가 한 행이다."""

    model_config = ConfigDict(frozen=True)

    product_type_code: str
    country_code: str
    country_name: str
    country_abbreviation: str
    market_code: str
    market_name: str
    local_settlement_date: date
    domestic_settlement_date: date

    @classmethod
    def from_payload(cls, row: dict[str, Any]) -> "OverseasRow":
        return cls(
            product_type_code=row["prdt_type_cd"],
            country_code=row["tr_natn_cd"],
            # 공식 문서 목록에는 없지만 실제 응답에 온다. 없으면 빈 문자열로 둔다.
            country_name=row.get("tr_natn_name", ""),
            country_abbreviation=row["natn_eng_abrv_cd"],
            market_code=row["tr_mket_cd"],
            market_name=row["tr_mket_name"],
            local_settlement_date=_day(row["acpl_sttl_dt"], "acpl_sttl_dt"),
            domestic_settlement_date=_day(row["dmst_sttl_dt"], "dmst_sttl_dt"),
        )


class UsSettlement(BaseModel):
    """미국 시장별 행을 한 벌로 접은 결과."""

    model_config = ConfigDict(frozen=True)

    session_date: date
    local_settlement_date: date
    domestic_settlement_date: date
    market_count: int


class DomesticFetch(BaseModel):
    """국내 조회 한 번. 저장에 필요한 값과 계보에 남길 값을 함께 담는다."""

    model_config = ConfigDict(frozen=True)

    base_date: date
    days: tuple[DomesticDay, ...]
    page_count: int
    started_at: datetime
    completed_at: datetime


class OverseasFetch(BaseModel):
    model_config = ConfigDict(frozen=True)

    trade_date: date
    rows: tuple[OverseasRow, ...]
    payload: str
    page_count: int
    started_at: datetime
    completed_at: datetime


def _parse_body(body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as error:
        raise KisPayloadError(f"KIS returned a non-JSON body: {error}") from None
    if not isinstance(payload, dict):
        raise KisPayloadError("KIS returned a JSON body that is not an object")

    code = str(payload.get("rt_cd", ""))
    if code != "0":
        raise KisResultError(code, str(payload.get("msg1", "")).strip())
    return payload


def _rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    output = payload.get("output") or []
    if isinstance(output, dict):
        return [output]
    if not isinstance(output, list):
        raise KisPayloadError("KIS returned an output that is neither a list nor an object")
    return output


def fold_us_settlement(fetch: OverseasFetch) -> UsSettlement | None:
    """미국 시장별 행을 한 벌로 접는다. 미국 행이 없으면 `None`이다.

    실측에서 나스닥·뉴욕거래소·아멕스 등 5행의 결제일이 모두 같았다. 그 전제가 깨지면
    한 행으로 접을 수 없으므로 실패시킨다.
    """
    us_rows = [row for row in fetch.rows if row.country_abbreviation == US_COUNTRY_CODE]
    if not us_rows:
        return None

    local = {row.local_settlement_date for row in us_rows}
    domestic = {row.domestic_settlement_date for row in us_rows}
    if len(local) > 1 or len(domestic) > 1:
        raise KisPayloadError(
            f"US settlement dates differ across markets on {fetch.trade_date}: "
            f"local={sorted(local)} domestic={sorted(domestic)}"
        )

    return UsSettlement(
        session_date=fetch.trade_date,
        local_settlement_date=us_rows[0].local_settlement_date,
        domestic_settlement_date=us_rows[0].domestic_settlement_date,
        market_count=len(us_rows),
    )


def _insert_source_record(
    cursor: Any,
    source_key: str,
    started_at: datetime,
    completed_at: datetime,
    status: str,
    record_count: int,
    payload: str | None,
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
            payload,
            json.dumps(metadata, ensure_ascii=False),
        ),
    )
    return cursor.fetchone()[0]


class KisMarketCalendarCollector:
    """KIS 캘린더 수집기. 자격 증명과 토큰을 들고 국내 휴장일과 해외 결제일을 조회·저장한다.

    한 실행이 객체 하나다. 토큰은 발급 횟수 제한이 있어 DAG이 한 번 받아 넘긴다. 토큰은 이
    객체가 사는 동안 안 변하는 값이라 갈아 끼우지 않는다 — 401로 다시 받았으면 DAG이 새
    토큰으로 객체를 다시 만든다.

    파싱(`DomesticDay.from_payload`·`OverseasRow.from_payload`)과 접기(`fold_us_settlement`)는
    밖에 둔다. 자격 증명도 연결도 보지 않는 순수 변환이다.
    """

    def __init__(self, token: SecretStr, app_key: SecretStr, app_secret: SecretStr) -> None:
        self._token = token
        self._app_key = app_key
        self._app_secret = app_secret

    def _paged(
        self,
        path: str,
        tr_id: str,
        query: dict[str, str],
        sleep: float = PAGE_DELAY_SECONDS,
    ) -> tuple[list[dict[str, Any]], int]:
        """연속조회를 돌며 행을 모은다. (행 목록, 받은 장 수)를 돌려준다.

        **페이지 상한은 정지 조건이지 오류가 아니다.** 국내 조회는 미래를 끝없이 주므로
        (실측: 12장 288행에도 계속) 언젠가 끝나기를 기다릴 수 없다. 필요한 만큼 받고 멈춘다.
        """
        parameters = dict(query) | {"CTX_AREA_FK": "", "CTX_AREA_NK": ""}
        tr_cont = ""
        rows: list[dict[str, Any]] = []
        previous_cursor: str | None = None

        for page in range(1, MAX_PAGES + 1):
            body, _, headers = send_get(self._token, self._app_key, self._app_secret, path, tr_id, parameters, tr_cont)
            payload = _parse_body(body)
            rows.extend(_rows(payload))

            if headers.get("tr_cont", "") not in CONTINUE_FLAGS:
                return rows, page

            cursor = str(payload.get("ctx_area_nk", ""))
            if cursor == previous_cursor:
                # 커서가 멈췄는데 계속 이어진다고 한다. 더 돌면 같은 행만 쌓인다.
                raise KisCursorError(f"KIS kept the same continuation cursor after page {page}")
            previous_cursor = cursor
            parameters["CTX_AREA_FK"] = str(payload.get("ctx_area_fk", ""))
            parameters["CTX_AREA_NK"] = cursor
            tr_cont = "N"
            time.sleep(sleep)

        logger.info("Stopping at the %s page cap; KIS still had more to give", MAX_PAGES)
        return rows, MAX_PAGES


    def fetch_domestic_calendar(self, base_date: date, sleep: float = PAGE_DELAY_SECONDS) -> DomesticFetch:
        """`base_date`부터 앞으로의 국내 거래일을 받는다."""
        started_at = datetime.now(UTC)
        rows, page_count = self._paged(
            DOMESTIC_PATH,
            DOMESTIC_TR_ID,
            {"BASS_DT": base_date.strftime("%Y%m%d")},
            sleep,
        )
        try:
            days = tuple(DomesticDay.from_payload(row) for row in rows)
        except (KeyError, ValueError, ValidationError) as error:
            raise KisPayloadError(f"KIS domestic holiday row is malformed: {error}") from None

        return DomesticFetch(
            base_date=base_date,
            days=days,
            page_count=page_count,
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )


    def fetch_overseas_settlement(self, trade_date: date, sleep: float = PAGE_DELAY_SECONDS) -> OverseasFetch:
        """`trade_date` 하루의 국가·시장별 결제일을 받는다.

        미래 날짜는 값 없음과 같은 0행으로 오므로 여기서는 성공으로 다룬다. 판정은 저장 쪽이 한다.
        """
        started_at = datetime.now(UTC)
        raw, page_count = self._paged(
            OVERSEAS_PATH,
            OVERSEAS_TR_ID,
            {"TRAD_DT": trade_date.strftime("%Y%m%d")},
            sleep,
        )
        try:
            rows = tuple(OverseasRow.from_payload(row) for row in raw)
        except (KeyError, ValueError, ValidationError) as error:
            raise KisPayloadError(f"KIS overseas settlement row is malformed: {error}") from None

        return OverseasFetch(
            trade_date=trade_date,
            rows=rows,
            # 3.5KB 남짓이라 원본을 그대로 남긴다. 미국 외 나라를 나중에 행으로 승격할 때
            # 과거를 재구성할 근거가 된다.
            payload=json.dumps(raw, ensure_ascii=False),
            page_count=page_count,
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )

    def store_domestic(self, connection: Connection, fetch: DomesticFetch) -> int:
        """국내 거래일을 저장하고 저장한 날짜 수를 돌려준다.

        국내는 KIS가 판정의 주인이므로 `effective_open_day`를 `opnd_yn`으로 함께 채운다.
        """
        verified_at = fetch.completed_at
        with connection.cursor() as cursor:
            source_record_id = _insert_source_record(
                cursor,
                DOMESTIC_SOURCE_KEY,
                fetch.started_at,
                fetch.completed_at,
                "succeeded" if fetch.days else "failed",
                len(fetch.days),
                # 한 번에 수백 행이라 payload에 넣지 않는다. 매일 쌓으면 계보가 캘린더보다 커진다.
                None,
                {
                    "base_date": fetch.base_date.isoformat(),
                    "page_count": fetch.page_count,
                    "day_count": len(fetch.days),
                    "last_date": fetch.days[-1].session_date.isoformat() if fetch.days else None,
                    "closed_count": sum(1 for day in fetch.days if not day.open_day),
                },
            )
            execute_upserts(
                cursor,
                MARKET_SESSION_DOMESTIC_UPSERT,
                [
                    (
                        day.session_date,
                        day.weekday_code,
                        day.business_day,
                        day.trading_day,
                        day.open_day,
                        day.settlement_day,
                        day.open_day,
                        verified_at,
                        source_record_id,
                    )
                    for day in fetch.days
                ],
            )
        return len(fetch.days)


    def store_overseas(self, connection: Connection, fetch: OverseasFetch) -> UsSettlement | None:
        """미국 행의 결제일만 채운다. 개장 판정은 건드리지 않는다.

        미국 행이 응답에 없으면 아무 것도 갱신하지 않는다. NYSE가 이미 그 날짜를 판정했으므로
        부재를 휴장으로 해석할 필요가 없다. 다만 NYSE가 개장으로 본 날에 미국 행이 없으면
        둘 중 하나가 틀렸다는 뜻이라 경고를 남긴다.
        """
        settlement = fold_us_settlement(fetch)

        with connection.cursor() as cursor:
            source_record_id = _insert_source_record(
                cursor,
                OVERSEAS_SOURCE_KEY,
                fetch.started_at,
                fetch.completed_at,
                "succeeded",
                1 if settlement else 0,
                fetch.payload,
                {
                    "trade_date": fetch.trade_date.isoformat(),
                    "page_count": fetch.page_count,
                    "row_count": len(fetch.rows),
                    "countries": sorted({row.country_abbreviation for row in fetch.rows}),
                    "us_market_count": settlement.market_count if settlement else 0,
                },
            )

            if settlement is None:
                if fetch.rows:
                    logger.warning(
                        "KIS returned %s rows for %s but none for the US; leaving the NYSE verdict alone",
                        len(fetch.rows),
                        fetch.trade_date,
                    )
                else:
                    # 주말·미래·장애가 모두 0행이다. 응답만으로는 가를 수 없어 판정하지 않는다.
                    logger.info("KIS returned no settlement rows for %s", fetch.trade_date)
                return None

            cursor.execute(
                MARKET_SESSION_SETTLEMENT_UPDATE,
                (
                    settlement.local_settlement_date,
                    settlement.domestic_settlement_date,
                    source_record_id,
                    settlement.session_date,
                ),
            )
            updated = cursor.fetchone()
            if updated is None:
                logger.warning(
                    "No US_EQUITY row for %s; NYSE has not covered that year yet",
                    settlement.session_date,
                )
        return settlement
