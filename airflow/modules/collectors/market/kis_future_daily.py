"""KIS 국내 지수선물 확정 일봉 수집기.

현물 지수 일봉(`kis_index_daily.py`)과 나눠 둔다. 응답 칸 이름이 다르고(`futs_*` 대
`bstp_nmix_*`), 무엇보다 **조회 축이 하나 더 있다.** 현물은 심볼 하나가 시계열 하나지만
선물은 `KOSPI200_FUT` 한 시계열이 분기마다 다른 계약의 값으로 이어진다.

API 계약은 2026-08-27 운영 앱키 실측이다
(`docs/collection/kis-index-daily-collection.md` 4.4절). 실측이 뒤집은 것 둘.

- **코드 변환기가 필요 없다.** 공식 예제는 `101W09` 꼴 단축코드를 예시로 드는데 그 코드는
  실제로 0행을 돌려주고, 분봉이 쓰는 `A01609`가 일봉에서도 그대로 먹는다.
- **`tr_cont` 헤더가 오지 않는다.** 행 상한은 100이고 헤더는 늘 빈 문자열이었다. 그래서
  이어받기 분기를 두지 않고 창 걷기만 한다. 나중에 KIS가 헤더를 주기 시작해도 창 걷기는
  그대로 맞는 답을 낸다.

전송(`send_get`)과 식별자(`DomesticFuture`), 만기일 규칙(`expiry_date`)은 공용 층인
`collectors/kis.py`에 있다.
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
    CONTRACT_MONTHS,
    SOURCE,
    SOURCE_RECORD_INSERT,
    DomesticFuture,
    KisPayloadError,
    expiry_date,
    result_error,
    send_get,
)
from modules.collectors.market.kis_index_daily import DailyIndexBar
from modules.db import Connection
from modules.sql import read_sql
from modules.upsert import execute_upserts

INDEX_FUTURE_DAILY_UPSERT = read_sql("postgres", "index_future_daily", "upsert.sql")

# 선물옵션기간별시세. 현물 지수와 다른 엔드포인트다.
FUTURE_DAILY_PATH = "/uapi/domestic-futureoption/v1/quotations/inquire-daily-fuopchartprice"
FUTURE_DAILY_TR_ID = "FHKIF03020100"
FUTURE_DAILY_SOURCE_KEY = "inquire_daily_fuopchartprice"
# 한 월물 창에 허용하는 최대 장 수. 200달력일이 두 장이라 넉넉하다(실측: 한 장 100행).
FUTURE_DAILY_MAX_PAGES = 10
# 페이지 사이 대기. 지수 일봉 수집기와 같은 이유다.
FUTURE_DAILY_PAGE_DELAY_SECONDS = 0.5

_DATE_PATTERN = re.compile(r"\d{8}")


class KisDailyFutureRow(BaseModel):
    """`output2` 한 건. 값은 전부 문자열이고 공백 패딩이 붙는다.

    거래대금(`acml_tr_pbmn`)과 수정 여부(`mod_yn`)도 오지만 이 계약에 넣지 않는다.
    `extra="ignore"`가 그것들을 흘린다.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    business_date: str = Field(alias="stck_bsop_date")
    open: str = Field(alias="futs_oprc")
    high: str = Field(alias="futs_hgpr")
    low: str = Field(alias="futs_lwpr")
    close: str = Field(alias="futs_prpr")
    volume: str = Field(alias="acml_vol")


class DailyFutureBar(DailyIndexBar):
    """정규화한 선물 일봉 1건. 현물 봉에 **실제 월물**이 하나 붙는다.

    `DailyIndexBar`를 상속한다. 양수·유한 OHLC 검사와 고저 일관성 검사가 글자 그대로 같아서,
    복사해 두면 언젠가 한쪽만 고쳐진다.
    """

    contract_code: str


class FutureContractWindow(BaseModel):
    """한 월물이 책임지는 조회 구간. 만기일(포함)까지가 그 월물 몫이다."""

    model_config = ConfigDict(frozen=True)

    future: DomesticFuture
    contract_code: str
    start_date: date
    end_date: date


class FutureDailyFetch(BaseModel):
    """한 심볼·한 구간의 일봉 수집 결과. `bars`는 거래일 오름차순이다."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    contracts: tuple[str, ...]
    start_date: date
    end_date: date
    bars: tuple[DailyFutureBar, ...]
    page_count: int
    started_at: AwareDatetime
    completed_at: AwareDatetime


def contract_code(future: DomesticFuture, year: int, month: int) -> str:
    """그 연·월물의 KIS 종목코드.

    `front_contract()`와 같은 형식이고 실측으로 일봉 API에서 확인했다. 다만 저기는 `today`를
    받아 **지금 거래되는** 계약 하나를 주고, 여기는 연·월을 받아 **아무 계약이나** 만든다.
    과거 구간을 조회하려면 뒤엣것이 필요하다.

    **연도가 한 자리다.** 10년을 넘기면 `A01609`가 2016년 9월물과 겹친다. 지금 백필 범위
    (2025년~)에서는 문제가 아니고, 넘길 때는 여기 한 줄만 바뀐다.
    """
    if month not in CONTRACT_MONTHS:
        raise ValueError(f"{month} is not a quarterly contract month for {future.value}")
    return f"A0{future.product_digit}{year % 10}{month:02d}"


def contract_windows(future: DomesticFuture, start_date: date, end_date: date) -> tuple[FutureContractWindow, ...]:
    """구간을 덮는 월물 창들. 오래된 것이 먼저이고 서로 겹치지 않는다.

    **`front_contract()`로 대신할 수 없다.** 그건 오늘 거래되는 계약 하나를 주므로, 만기를
    넘는 구간을 그 코드 하나로 물으면 과거 쪽이 조용히 빈다. 조회 창이 200달력일이라 만기를
    최소 하나 넘고 백필은 더 넘는다.

    만기일 봉은 만기월에 귀속한다(문서 4.3절). 실측에서 만기물 `A01606`의 마지막 행이 만기일
    20260611이었다.
    """
    if start_date > end_date:
        raise ValueError(f"start_date {start_date} must not be after end_date {end_date}")

    windows: list[FutureContractWindow] = []
    cursor = start_date
    while cursor <= end_date:
        year, month = _contract_month_for(cursor)
        window_end = min(expiry_date(year, month), end_date)
        windows.append(
            FutureContractWindow(
                future=future,
                contract_code=contract_code(future, year, month),
                start_date=cursor,
                end_date=window_end,
            )
        )
        cursor = window_end + timedelta(days=1)
    return tuple(windows)


def _contract_month_for(day: date) -> tuple[int, int]:
    """그날 거래되는 최근월물의 연·월. 만기 당일은 아직 그 월물이다."""
    for offset in range(5):
        month_index = (day.month - 1) + offset
        year = day.year + month_index // 12
        month = month_index % 12 + 1
        if month in CONTRACT_MONTHS and expiry_date(year, month) >= day:
            return year, month
    raise ValueError(f"No contract month found for {day}")


def _daily_future_rows(body: bytes, requested_code: str) -> tuple[KisDailyFutureRow, ...]:
    """일봉 응답 본문을 검증해 원시 행을 꺼낸다. `rt_cd`가 0이 아니면 `KisResultError`다.

    **응답이 코드를 되돌려 줄 때만 대조한다.** `output1`은 최근월물에서만 채워지고 만기물에서는
    빈 dict다(4.4절 실측). 필수로 만들면 백필 창이 통째로 실패한다.
    """
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as error:
        raise KisPayloadError(f"KIS returned a non-JSON future daily body: {error}") from None
    if not isinstance(payload, dict):
        raise KisPayloadError("KIS returned a future daily body that is not an object")

    code = str(payload.get("rt_cd", ""))
    if code != "0":
        raise result_error(code, str(payload.get("msg1", "")).strip())

    head = payload.get("output1")
    echoed = str(head.get("futs_shrn_iscd", "")).strip() if isinstance(head, dict) else ""
    if echoed and echoed != requested_code:
        raise KisPayloadError(f"KIS answered {echoed} for a request for {requested_code}")

    output = payload.get("output2")
    if not isinstance(output, list):
        raise KisPayloadError("KIS future daily response has no output2 list")
    try:
        return tuple(KisDailyFutureRow.model_validate(row) for row in output)
    except ValidationError as error:
        raise KisPayloadError("KIS future daily row is malformed") from error


def _daily_future_bar(row: KisDailyFutureRow, code: str) -> DailyFutureBar:
    raw_date = row.business_date.strip()
    if not _DATE_PATTERN.fullmatch(raw_date):
        raise KisPayloadError(f"KIS future daily date is malformed: {raw_date!r}")
    try:
        return DailyFutureBar(
            business_date=date(int(raw_date[:4]), int(raw_date[4:6]), int(raw_date[6:])),
            open=Decimal(row.open.strip()),
            high=Decimal(row.high.strip()),
            low=Decimal(row.low.strip()),
            close=Decimal(row.close.strip()),
            volume=int(row.volume.strip()),
            contract_code=code,
        )
    except (InvalidOperation, ValueError, ValidationError) as error:
        raise KisPayloadError(f"KIS future daily bar is malformed: {error}") from None


class KisFutureDailyCollector:
    """국내 지수선물 확정 일봉 수집기. 자격 증명과 토큰을 들고 구간으로 조회·저장한다.

    한 실행이 객체 하나다. 토큰은 발급 횟수 제한이 있어 DAG이 한 번 받아 넘긴다.
    """

    def __init__(self, token: SecretStr, app_key: SecretStr, app_secret: SecretStr) -> None:
        self._token = token
        self._app_key = app_key
        self._app_secret = app_secret

    def fetch(
        self,
        future: DomesticFuture,
        start_date: date,
        end_date: date,
        *,
        sleep: float = FUTURE_DAILY_PAGE_DELAY_SECONDS,
    ) -> FutureDailyFetch:
        """한 선물 심볼의 확정 일봉을 구간으로 받는다.

        구간을 월물 창으로 먼저 끊고(`contract_windows`) 창마다 따로 조회한다. 한 창 안에서는
        가장 오래된 날짜 하루 전으로 종료일을 옮겨 걷는다 — 응답 헤더 `tr_cont`는 실측에서 늘
        비어 있었고 행 상한이 100이라, 이 걷기가 유일한 페이지 수단이다.

        **잘림 판정에 행 수를 세지 않는다.** 창의 시작에 닿았는지로 본다. 상한을 상수로 들고
        있다가 실제보다 크게 적어 두면 잘린 응답을 "구간을 다 줬다"로 읽는다. 마지막 장까지
        받고도 창의 시작에 못 닿았으면 부분 저장 대신 실패시킨다 — 잘린 구간은 상관 분석의
        수익률 계산에 구멍을 남긴다.
        """
        started_at = datetime.now(UTC)
        seen: dict[date, DailyFutureBar] = {}
        page_count = 0
        windows = contract_windows(future, start_date, end_date)

        for window in windows:
            page_count += self._fetch_window(window, seen, sleep)

        if not seen:
            raise KisPayloadError(
                f"KIS returned no daily bars for {future.value} between {start_date} and {end_date}"
            )

        return FutureDailyFetch(
            symbol=future.value,
            contracts=tuple(window.contract_code for window in windows),
            start_date=start_date,
            end_date=end_date,
            bars=tuple(seen[day] for day in sorted(seen)),
            page_count=page_count,
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )

    def _fetch_window(
        self,
        window: FutureContractWindow,
        seen: dict[date, DailyFutureBar],
        sleep: float,
    ) -> int:
        """한 월물 창을 끝까지 걷는다. 받은 장 수를 준다.

        **빈 창은 실패가 아니다.** 백필 구간의 끝이 주말이면 창 하나가 통째로 비고, 그것은
        정상이다. 구간 전체가 비었는지는 부르는 쪽이 본다.
        """
        window_end = window.end_date
        for page in range(1, FUTURE_DAILY_MAX_PAGES + 1):
            body, _, _ = send_get(
                self._token,
                self._app_key,
                self._app_secret,
                FUTURE_DAILY_PATH,
                FUTURE_DAILY_TR_ID,
                {
                    "FID_COND_MRKT_DIV_CODE": "F",  # F = 지수선물
                    "FID_INPUT_ISCD": window.contract_code,
                    "FID_INPUT_DATE_1": window.start_date.strftime("%Y%m%d"),
                    "FID_INPUT_DATE_2": window_end.strftime("%Y%m%d"),
                    "FID_PERIOD_DIV_CODE": "D",
                },
            )
            rows = _daily_future_rows(body, window.contract_code)
            oldest = window_end
            for row in rows:
                bar = _daily_future_bar(row, window.contract_code)
                if not window.start_date <= bar.business_date <= window.end_date:
                    raise KisPayloadError(
                        f"KIS gave a bar outside the requested span: "
                        f"{bar.business_date} for {window.contract_code}"
                    )
                if bar.business_date in seen:
                    raise KisPayloadError(
                        f"KIS gave a duplicate date {bar.business_date} for {window.contract_code}"
                    )
                seen[bar.business_date] = bar
                oldest = min(oldest, bar.business_date)

            if not rows or oldest <= window.start_date:
                return page
            # 창의 시작에 못 닿았다 — 실측대로 100행에서 잘린 응답이다. 종료일만 뒤로 옮긴다.
            # 시작일은 그대로 두므로 제공처가 거기서 멈춰 준다.
            window_end = oldest - timedelta(days=1)
            wait_seconds(sleep)

        raise KisPayloadError(
            f"KIS still had more to give after {FUTURE_DAILY_MAX_PAGES} pages for {window.contract_code}"
        )

    def store(self, connection: Connection, fetch: FutureDailyFetch) -> int:
        """한 심볼·한 구간의 일봉을 저장한다. 겹치는 날짜는 확정값으로 갱신된다."""
        with connection.cursor() as cursor:
            cursor.execute(
                SOURCE_RECORD_INSERT,
                (
                    "api",
                    SOURCE,
                    FUTURE_DAILY_SOURCE_KEY,
                    fetch.started_at,
                    fetch.completed_at,
                    "succeeded",
                    len(fetch.bars),
                    # 원본은 남기지 않는다. 어느 구간을 어느 월물로 몇 장에 받았는지면 재현에 충분하다.
                    None,
                    json.dumps(
                        {
                            "symbol": fetch.symbol,
                            "contracts": list(fetch.contracts),
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
                INDEX_FUTURE_DAILY_UPSERT,
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
                        bar.contract_code,
                        source_record_id,
                    )
                    for bar in fetch.bars
                ],
            )
        return len(fetch.bars)
