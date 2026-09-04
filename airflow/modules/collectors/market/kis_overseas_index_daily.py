"""KIS 해외 현물지수 확정 일봉 수집기. 미국(`OverseasIndex`)과 아시아(`AsiaIndex`)가 같은 수집기를 쓴다.

마감 분봉(`kis_overseas_index.py`)과 나눠 둔다. 저기는 브리핑용으로 마감 부근 102봉을 받고
`index_bar`에 넣는다. 여기는 상관 분석용으로 구간의 확정 일봉을 받아 `index_daily`에 넣는다.
조회 단위도 저장 테이블도 백필 범위도 다르다.

분봉을 다시 집계하지 않고 제공처의 확정 일봉을 받는다. 분봉 집계는 수집 구간이 비거나
정산 봉이 섞이면 공식 OHLC와 달라진다.

API 계약은 2026-08-27 운영 앱키 실측이다
(`docs/collection/kis-index-daily-collection.md` 6.3절). 실측이 정한 것 넷.

- **`COMP`(나스닥 종합)가 일봉에서 된다.** 공식 예제의 `FID_INPUT_ISCD` 설명은
  "다우30, 나스닥100, S&P500만 가능"으로 좁게 적혀 있지만 실제로는 온다.
- **거래량이 계열마다 갈린다.** `SPX`는 0, `COMP`는 실제 값이다. 0을 결측으로 읽지 않는다.
- **`output1.stck_shrn_iscd`가 없을 수 있다.** `.DJI`에는 그 칸이 아예 없었다. 그래서
  요청·응답 코드 대조는 "있으면 대조"다.
- **`tr_cont` 헤더가 오지 않는다.** 공식 예제가 연속조회를 구현해 두었는데도 빈 문자열이었다.
  행 상한은 100이고 창 걷기가 유일한 페이지 수단이다.

식별자(`OverseasIndex`)와 전송(`send_get`)은 마감 분봉 수집기와 공용 층이 갖는다.
"""

import json
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from time import sleep as wait_seconds

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
)

from modules.collectors.kis import (
    SOURCE,
    SOURCE_RECORD_INSERT,
    KisPayloadError,
    result_error,
    send_get,
)
from modules.collectors.market.kis_index_daily import DailyIndexBar
from modules.collectors.market.kis_overseas_index import AsiaIndex, OverseasIndex
from modules.db import Connection
from modules.sql import read_sql
from modules.upsert import execute_upserts

INDEX_DAILY_UPSERT = read_sql("postgres", "index_daily", "upsert.sql")

# 해외주식 종목·지수·환율 기간별시세. 마감 분봉과 다른 엔드포인트다.
OVERSEAS_DAILY_PATH = "/uapi/overseas-price/v1/quotations/inquire-daily-chartprice"
OVERSEAS_DAILY_TR_ID = "FHKST03030100"
OVERSEAS_DAILY_SOURCE_KEY = "inquire_daily_chartprice"
# 한 심볼에 허용하는 최대 장 수. 200달력일이 두 장이라 넉넉하다(실측: 한 장 100행).
OVERSEAS_DAILY_MAX_PAGES = 10
# 페이지 사이 대기. 국내 일봉 수집기와 같은 이유다.
OVERSEAS_DAILY_PAGE_DELAY_SECONDS = 0.5

_DATE_PATTERN = re.compile(r"\d{8}")


class KisOverseasDailyRow(BaseModel):
    """`output2` 한 건. 값은 전부 문자열이고 공백 패딩이 붙는다.

    수정 여부(`mod_yn`)도 오지만 이 계약에 넣지 않는다. `extra="ignore"`가 그것을 흘린다.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    business_date: str = Field(alias="stck_bsop_date")
    open: str = Field(alias="ovrs_nmix_oprc")
    high: str = Field(alias="ovrs_nmix_hgpr")
    low: str = Field(alias="ovrs_nmix_lwpr")
    close: str = Field(alias="ovrs_nmix_prpr")
    volume: str = Field(alias="acml_vol")


class OverseasIndexDailyFetch(BaseModel):
    """한 지수·한 구간의 일봉 수집 결과. `bars`는 거래일 오름차순이다."""

    model_config = ConfigDict(frozen=True)

    # 미국 마감 DAG과 아시아 일봉 DAG이 같은 수집기를 쓴다. 코드와 저장 심볼만 있으면 된다.
    index: OverseasIndex | AsiaIndex
    start_date: date
    end_date: date
    bars: tuple[DailyIndexBar, ...]
    page_count: int
    started_at: AwareDatetime
    completed_at: AwareDatetime


def _daily_overseas_rows(body: bytes, requested_code: str) -> tuple[KisOverseasDailyRow, ...]:
    """일봉 응답 본문을 검증해 원시 행을 꺼낸다. `rt_cd`가 0이 아니면 `KisResultError`다.

    **응답이 코드를 되돌려 줄 때만 대조한다.** `.DJI`에는 `output1.stck_shrn_iscd`가 아예
    없었다(6.3절 실측). 필수로 만들면 그런 코드에서 조회가 통째로 실패한다.
    """
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as error:
        raise KisPayloadError(f"KIS returned a non-JSON overseas daily body: {error}") from None
    if not isinstance(payload, dict):
        raise KisPayloadError("KIS returned an overseas daily body that is not an object")

    code = str(payload.get("rt_cd", ""))
    if code != "0":
        raise result_error(code, str(payload.get("msg1", "")).strip())

    head = payload.get("output1")
    echoed = str(head.get("stck_shrn_iscd", "")).strip() if isinstance(head, dict) else ""
    if echoed and echoed != requested_code:
        raise KisPayloadError(f"KIS answered {echoed} for a request for {requested_code}")

    output = payload.get("output2")
    if not isinstance(output, list):
        raise KisPayloadError("KIS overseas daily response has no output2 list")
    try:
        return tuple(KisOverseasDailyRow.model_validate(row) for row in output)
    except ValidationError as error:
        raise KisPayloadError("KIS overseas daily row is malformed") from error


def _daily_overseas_bar(row: KisOverseasDailyRow) -> DailyIndexBar:
    """`stck_bsop_date`를 그 시장의 거래일로 그대로 읽는다(미국은 뉴욕, 아시아는 현지). UTC로 다시 계산하지 않는다."""
    raw_date = row.business_date.strip()
    if not _DATE_PATTERN.fullmatch(raw_date):
        raise KisPayloadError(f"KIS overseas daily date is malformed: {raw_date!r}")
    try:
        return DailyIndexBar(
            business_date=date(int(raw_date[:4]), int(raw_date[4:6]), int(raw_date[6:])),
            open=Decimal(row.open.strip()),
            high=Decimal(row.high.strip()),
            low=Decimal(row.low.strip()),
            close=Decimal(row.close.strip()),
            # 지수 거래량은 계열마다 0이거나 아니거나다(SPX는 0, COMP는 실값). 둘 다 정상이다.
            volume=int(row.volume.strip()),
        )
    except (InvalidOperation, ValueError, ValidationError) as error:
        raise KisPayloadError(f"KIS overseas daily bar is malformed: {error}") from None


class KisOverseasIndexDailyCollector:
    """해외 현물지수 확정 일봉 수집기. 자격 증명과 토큰을 들고 구간으로 조회·저장한다.

    한 실행이 객체 하나다. 토큰은 발급 횟수 제한이 있어 DAG이 한 번 받아 넘긴다.
    """

    def __init__(self, token: SecretStr, app_key: SecretStr, app_secret: SecretStr) -> None:
        self._token = token
        self._app_key = app_key
        self._app_secret = app_secret

    def fetch(
        self,
        index: OverseasIndex | AsiaIndex,
        start_date: date,
        end_date: date,
        *,
        sleep: float = OVERSEAS_DAILY_PAGE_DELAY_SECONDS,
    ) -> OverseasIndexDailyFetch:
        """한 지수의 확정 일봉을 구간으로 받는다.

        가장 오래된 날짜 하루 전으로 종료일을 옮겨 걷는다. 응답 헤더 `tr_cont`는 실측에서 늘
        비어 있었고 행 상한이 100이라 이 걷기가 유일한 페이지 수단이다.

        **잘림 판정에 행 수를 세지 않는다.** 구간의 시작에 닿았는지로 본다. 상한을 상수로 들고
        있다가 실제보다 크게 적어 두면 잘린 응답을 "구간을 다 줬다"로 읽는다. 마지막 장까지
        받고도 시작에 못 닿았으면 부분 저장 대신 실패시킨다.
        """
        started_at = datetime.now(UTC)
        seen: dict[date, DailyIndexBar] = {}
        window_end = end_date
        page_count = 0

        for page_count in range(1, OVERSEAS_DAILY_MAX_PAGES + 1):
            body, _, _ = send_get(
                self._token,
                self._app_key,
                self._app_secret,
                OVERSEAS_DAILY_PATH,
                OVERSEAS_DAILY_TR_ID,
                {
                    "FID_COND_MRKT_DIV_CODE": "N",  # N = 해외지수
                    "FID_INPUT_ISCD": index.kis_code,
                    "FID_INPUT_DATE_1": start_date.strftime("%Y%m%d"),
                    "FID_INPUT_DATE_2": window_end.strftime("%Y%m%d"),
                    "FID_PERIOD_DIV_CODE": "D",
                },
            )
            rows = _daily_overseas_rows(body, index.kis_code)
            for row in rows:
                bar = _daily_overseas_bar(row)
                if not start_date <= bar.business_date <= end_date:
                    raise KisPayloadError(
                        f"KIS gave a bar outside the requested span: {bar.business_date} for {index.value}"
                    )
                if bar.business_date in seen:
                    raise KisPayloadError(f"KIS gave a duplicate date {bar.business_date} for {index.value}")
                seen[bar.business_date] = bar

            if not rows:
                break
            oldest = min(seen)
            if oldest <= start_date:
                break
            # 구간의 시작에 못 닿았다 — 실측대로 100행에서 잘린 응답이다. 종료일만 뒤로 옮긴다.
            # 시작일은 그대로 두므로 제공처가 거기서 멈춰 준다.
            window_end = oldest - timedelta(days=1)
            wait_seconds(sleep)
        else:
            raise KisPayloadError(
                f"KIS still had more to give after {OVERSEAS_DAILY_MAX_PAGES} pages for {index.value}"
            )

        if not seen:
            raise KisPayloadError(f"KIS returned no daily bars for {index.value} between {start_date} and {end_date}")

        return OverseasIndexDailyFetch(
            index=index,
            start_date=start_date,
            end_date=end_date,
            bars=tuple(seen[day] for day in sorted(seen)),
            page_count=page_count,
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )

    def store(self, connection: Connection, fetch: OverseasIndexDailyFetch) -> int:
        """한 지수·한 구간의 일봉을 저장한다. 겹치는 날짜는 확정값으로 갱신된다."""
        with connection.cursor() as cursor:
            cursor.execute(
                SOURCE_RECORD_INSERT,
                (
                    "api",
                    SOURCE,
                    OVERSEAS_DAILY_SOURCE_KEY,
                    fetch.started_at,
                    fetch.completed_at,
                    "succeeded",
                    len(fetch.bars),
                    # 원본은 남기지 않는다. 어느 구간을 몇 장으로 받았는지면 재현에 충분하다.
                    None,
                    json.dumps(
                        {
                            "symbol": fetch.index.value,
                            # 저장 심볼과 KIS 코드가 다르다(NASDAQ 대 COMP). 둘 다 남겨야 재현된다.
                            "kis_code": fetch.index.kis_code,
                            "start_date": fetch.start_date.isoformat(),
                            "end_date": fetch.end_date.isoformat(),
                            "page_count": fetch.page_count,
                            "bar_count": len(fetch.bars),
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            source_record_id = cursor.fetchone()[0]
            execute_upserts(
                cursor,
                INDEX_DAILY_UPSERT,
                [
                    (
                        SOURCE,
                        fetch.index.value,
                        bar.business_date,
                        bar.open,
                        bar.high,
                        bar.low,
                        bar.close,
                        bar.volume,
                        source_record_id,
                    )
                    for bar in fetch.bars
                ],
            )
        return len(fetch.bars)
