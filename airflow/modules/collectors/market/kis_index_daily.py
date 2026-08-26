"""KIS 지수 확정 일봉 수집기.

분봉(`kis_quote.py`)과 나눠 둔다. 같은 KIS 전송층을 쓰지만 조회 단위가 다르고
(구간 대 시각), 이어받기 규칙과 잘림 판정이 이 API에만 있다. 분봉 쪽을 고칠 때
읽지 않아도 되는 코드다.

전송(`send_get`)과 식별자(`DomesticIndex`)는 공용 층인 `collectors/kis.py`에 있다.
"""

import json
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from time import sleep as wait_seconds
from typing import Self

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

from modules.collectors.kis import (
    SOURCE,
    SOURCE_RECORD_INSERT,
    DomesticIndex,
    KisPayloadError,
    result_error,
    send_get,
)
from modules.db import Connection
from modules.sql import read_sql
from modules.upsert import execute_upserts

INDEX_DAILY_UPSERT = read_sql("postgres", "index_daily", "upsert.sql")


# 지수 일봉(국내주식업종기간별시세). 기술지표 계산의 원천이다
# (docs/analysis/market-technical-indicators.md 4절).
INDEX_DAILY_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice"
INDEX_DAILY_TR_ID = "FHKUP03500100"
INDEX_DAILY_SOURCE_KEY = "inquire_daily_indexchartprice"
# 연속조회 여부는 응답 헤더 `tr_cont`로 온다. 달력 수집기와 같은 값이다.
INDEX_DAILY_CONTINUE_FLAGS = frozenset({"M", "F"})
# 한 심볼에 허용하는 최대 장 수. 200달력일 구간이 이 안에 들어오지 않으면 계약이 깨진 것이다.
INDEX_DAILY_MAX_PAGES = 10
# 페이지 사이 대기. 달력 수집기의 PAGE_DELAY_SECONDS와 같은 이유다.
INDEX_DAILY_PAGE_DELAY_SECONDS = 0.5


class KisDailyIndexRow(BaseModel):
    """지수 일봉 `output2` 한 건. 값은 전부 문자열이고 공백 패딩이 붙는다."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    business_date: str = Field(alias="stck_bsop_date")
    open: str = Field(alias="bstp_nmix_oprc")
    high: str = Field(alias="bstp_nmix_hgpr")
    low: str = Field(alias="bstp_nmix_lwpr")
    close: str = Field(alias="bstp_nmix_prpr")
    volume: str = Field(alias="acml_vol")


class DailyIndexBar(BaseModel):
    """정규화한 지수 일봉 1건."""

    model_config = ConfigDict(frozen=True)

    business_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int = Field(ge=0)

    @field_validator("open", "high", "low", "close")
    @classmethod
    def require_positive_and_finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= 0:
            raise ValueError("index daily price must be a finite positive number")
        return value

    @model_validator(mode="after")
    def require_a_consistent_range(self) -> Self:
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("high must be at least open, close, and low")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("low must be at most open, close, and high")
        return self


class DailyIndexFetch(BaseModel):
    """한 지수·한 구간의 일봉 수집 결과. `bars`는 거래일 오름차순이다."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    start_date: date
    end_date: date
    bars: tuple[DailyIndexBar, ...]
    page_count: int
    started_at: AwareDatetime
    completed_at: AwareDatetime


def _daily_index_rows(body: bytes) -> tuple[KisDailyIndexRow, ...]:
    """일봉 응답 본문을 검증해 원시 행을 꺼낸다. `rt_cd`가 0이 아니면 `KisResultError`다."""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as error:
        raise KisPayloadError(f"KIS returned a non-JSON index daily body: {error}") from None
    if not isinstance(payload, dict):
        raise KisPayloadError("KIS returned an index daily body that is not an object")

    code = str(payload.get("rt_cd", ""))
    if code != "0":
        raise result_error(code, str(payload.get("msg1", "")).strip())

    output = payload.get("output2")
    if not isinstance(output, list):
        raise KisPayloadError("KIS index daily response has no output2 list")
    try:
        return tuple(KisDailyIndexRow.model_validate(row) for row in output)
    except ValidationError as error:
        raise KisPayloadError("KIS index daily row is malformed") from error


def _daily_index_bar(row: KisDailyIndexRow) -> DailyIndexBar:
    raw_date = row.business_date.strip()
    if not re.fullmatch(r"\d{8}", raw_date):
        raise KisPayloadError(f"KIS index daily date is malformed: {raw_date!r}")
    try:
        return DailyIndexBar(
            business_date=date(int(raw_date[:4]), int(raw_date[4:6]), int(raw_date[6:])),
            open=Decimal(row.open.strip()),
            high=Decimal(row.high.strip()),
            low=Decimal(row.low.strip()),
            close=Decimal(row.close.strip()),
            volume=int(row.volume.strip()),
        )
    except (InvalidOperation, ValueError, ValidationError) as error:
        raise KisPayloadError(f"KIS index daily bar is malformed: {error}") from None


class KisIndexDailyCollector:
    """지수 확정 일봉 수집기. 자격 증명과 토큰을 들고 구간으로 조회·저장한다.

    한 실행이 객체 하나다. 토큰은 발급 횟수 제한이 있어 DAG이 한 번 받아 넘긴다.
    """

    def __init__(self, token: SecretStr, app_key: SecretStr, app_secret: SecretStr) -> None:
        self._token = token
        self._app_key = app_key
        self._app_secret = app_secret

    def fetch(
        self,
        index: DomesticIndex,
        start_date: date,
        end_date: date,
        *,
        sleep: float = INDEX_DAILY_PAGE_DELAY_SECONDS,
    ) -> DailyIndexFetch:
        """한 지수의 확정 일봉을 구간으로 받는다.

        페이지 이어받기는 두 행태를 다 다룬다(문서 4.1절). 응답 헤더 `tr_cont`가 `M`/`F`면
        같은 구간을 `N`으로 다시 묻고, 헤더 없이 **구간의 시작에 못 닿은** 응답이 오면 가장
        오래된 날짜 하루 전으로 창을 옮긴다. 마지막 장까지 받고도 남았으면 부분 저장 대신
        실패시킨다 — 잘린 구간은 지표 계산 창에 구멍을 남긴다.

        **잘림 판정에 행 수를 세지 않는다.** 확정 수급 일별 API처럼 KIS는 연속조회 표식
        없이 응답을 자르는데, 한 장의 상한은 문서에 없고 제공처가 바꿔도 알려 주지 않는다.
        그 상한을 상수로 들고 있다가 실제보다 크게 적어 두면 잘린 응답을 "구간을 다 줬다"로
        읽는다(2026-08-24: 100봉으로 가정, 실제 50봉, 지수 일봉이 50봉에 묶임). 요청한
        구간의 시작에 닿았는지로 판정하면 상한을 몰라도 된다. 이력이 구간보다 짧은
        심볼에서 빈 응답 한 번을 더 받는 것이 그 대가다.
        """
        started_at = datetime.now(UTC)
        seen: dict[date, DailyIndexBar] = {}
        window_end = end_date
        tr_cont = ""
        page_count = 0

        for page_count in range(1, INDEX_DAILY_MAX_PAGES + 1):
            body, _, headers = send_get(
                self._token,
                self._app_key,
                self._app_secret,
                INDEX_DAILY_PATH,
                INDEX_DAILY_TR_ID,
                {
                    "FID_COND_MRKT_DIV_CODE": "U",  # U = 업종. 분봉 조회와 같은 구분이다
                    "FID_INPUT_ISCD": index.index_code,
                    "FID_INPUT_DATE_1": start_date.strftime("%Y%m%d"),
                    "FID_INPUT_DATE_2": window_end.strftime("%Y%m%d"),
                    "FID_PERIOD_DIV_CODE": "D",
                },
                tr_cont,
            )
            rows = _daily_index_rows(body)
            for row in rows:
                bar = _daily_index_bar(row)
                if not start_date <= bar.business_date <= end_date:
                    raise KisPayloadError(
                        f"KIS gave a bar outside the requested span: {bar.business_date} for {index.value}"
                    )
                if bar.business_date in seen:
                    raise KisPayloadError(f"KIS gave a duplicate date {bar.business_date} for {index.value}")
                seen[bar.business_date] = bar

            if headers.get("tr_cont", "") in INDEX_DAILY_CONTINUE_FLAGS:
                tr_cont = "N"
                wait_seconds(sleep)
                continue
            if not rows:
                break
            oldest = min(seen)
            if oldest <= start_date:
                break
            # 구간의 시작에 못 닿았는데 이어받기 표식이 없다 — 조용히 잘린 응답이다.
            # 창을 뒤로 옮긴다. 요청 구간의 시작은 그대로 두므로 제공처가 거기서 멈춰 준다.
            window_end = oldest - timedelta(days=1)
            tr_cont = ""
            wait_seconds(sleep)
        else:
            raise KisPayloadError(f"KIS still had more to give after {INDEX_DAILY_MAX_PAGES} pages for {index.value}")

        if not seen:
            raise KisPayloadError(f"KIS returned no daily bars for {index.value} between {start_date} and {end_date}")

        return DailyIndexFetch(
            symbol=index.value,
            start_date=start_date,
            end_date=end_date,
            bars=tuple(seen[day] for day in sorted(seen)),
            page_count=page_count,
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )

    def store(self, connection: Connection, fetch: DailyIndexFetch) -> int:
        """한 지수·한 구간의 일봉을 저장한다. 겹치는 날짜는 확정값으로 갱신된다."""
        with connection.cursor() as cursor:
            cursor.execute(
                SOURCE_RECORD_INSERT,
                (
                    "api",
                    SOURCE,
                    INDEX_DAILY_SOURCE_KEY,
                    fetch.started_at,
                    fetch.completed_at,
                    "succeeded",
                    len(fetch.bars),
                    # 원본은 남기지 않는다. 어느 구간을 몇 장으로 받았는지면 재현에 충분하다.
                    None,
                    json.dumps(
                        {
                            "symbol": fetch.symbol,
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
                        fetch.symbol,
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
