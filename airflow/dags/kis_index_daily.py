"""KOSPI·KOSDAQ 확정 일봉 수집 DAG.

분봉(`kis_quote_intraday`)은 당일 흐름용이고, 이 DAG는 기술적 보조지표(SMA·RSI·MACD)의
원천이 되는 **확정 일봉**을 받는다. 설계는 docs/analysis/market-technical-indicators.md 4절이다.

## 왜 200달력일인가

SMA60과 EMA 안정화에 120거래일이 필요하다. 연휴가 포함된 구간에서도 그만큼을 확보하는
고정 수집 창이 200달력일이다. 매일 같은 창을 다시 받으므로 실패한 날이 저절로 메워지고,
`index_daily/upsert.sql`이 (provider, symbol, business_date)로 멱등 갱신한다.

## 페이지 이어받기

응답 헤더 `tr_cont`가 오면 연속조회로, 헤더 없이 요청 구간의 시작에 못 닿은 응답이 오면
(확정 수급 API의 행태) 날짜 창을 뒤로 옮겨 받는다. 판단은 `KisIndexDailyCollector.fetch`가 한다.
한 장의 봉 수로 잘림을 재지 않는다 — 그 상한은 문서에 없고 제공처가 바꿔도 알려 주지 않는다.
마지막 장까지 받고도 남았으면 그 심볼은 저장하지 않고 실패한다 — 잘린 구간은 지표 계산
창에 구멍을 남긴다.

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `end_date` | `null` | 구간의 끝(YYYY-MM-DD). 비우면 실행일(KST) |
| `start_date` | `null` | 구간의 시작(YYYY-MM-DD). 비우면 `end_date`에서 200달력일 앞 |

## 이력 백필

`start_date`를 주면 그 날짜까지 거슬러 올라간다. 조회는 200달력일씩 창을 끊어 반복한다 —
`KisIndexDailyCollector.fetch`가 한 심볼에 허용하는 `INDEX_DAILY_MAX_PAGES`(10)를 넘지
않기 위해서다. 한 장이 50봉이라 200달력일(약 135거래일)은 3장 안쪽이다.

상한 상수를 올려 한 번에 다 받게 만들지 않는다. 그 값은 "200달력일 구간이 이 안에 들어오지
않으면 계약이 깨진 것"이라는 검사라, 백필 편의로 올리면 일상 실행의 검사가 함께 약해진다.

```bash
airflow dags trigger kis_index_daily \
  --conf '{"start_date": "2016-08-15", "end_date": "2026-08-25"}'
```

`stock_investor_trade_daily`와 달리 지수에는 수정주가가 없다. 소급 조정으로 과거 값이
바뀌는 일이 없으므로 이 DAG에는 대조 가드가 없다.

## 실패와 재시도

- **한 지수가 실패해도 다른 지수는 저장한다.** 심볼 하나가 트랜잭션 하나다. 마지막에
  실패 목록이 있으면 태스크를 죽인다.
- HTTP 400/403/404: 설정 오류라 즉시 실패한다.
- 응답 계약 위반(`KisPayloadError`)·본문 오류(`KisResultError`): 재시도해도 같아 그 심볼만
  실패로 모은다.
- 자동 실행은 KRX 휴장일이면 skip한다. `end_date`를 준 수동 실행은 휴장일 여부와 관계없이
  그 날짜까지 조회한다 — 구간에 담긴 과거 거래일이 목적이다.

## 필요한 환경

- `KIS_APP_KEY`, `KIS_APP_SECRET`. 토큰은 다른 KIS DAG와 같은 Airflow Variable 캐시를 공유한다.
- `CONNECTION_ID`가 가리키는 Airflow 연결.
"""

import logging
import os
import re
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pendulum
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Param, Variable, dag, get_current_context, task
from airflow.sdk.exceptions import AirflowFailException, AirflowSkipException
from pydantic import SecretStr

from modules.collectors.kis import (
    KisHTTPError,
    KisPayloadError,
    KisResultError,
    KisTimeWindowError,
    access_token,
)
from modules.collectors.market.kis_index_daily import KisIndexDailyCollector
from modules.collectors.market.kis_quote import MOVEMENT_INDEXES
from modules.market_session import krx_open_day
from modules.utility import CONNECTION_ID, KIS_UNRECOVERABLE_STATUSES, KST_TIMEZONE, atomic

logger = logging.getLogger(__name__)

# 달력 하루만 받는다. ISO 주 표기(2026-W34)와 기본형(20260821)을 걸러 내는 그물이다.
CALENDAR_DAY_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")

END_DATE_PARAM = "end_date"
START_DATE_PARAM = "start_date"

# SMA60과 EMA 안정화에 필요한 120거래일을 연휴 포함 구간에서도 확보하는 고정 창(4.4절).
# 백필의 창 크기이기도 하다 — 200달력일이 `INDEX_DAILY_MAX_PAGES` 안에 들어오는 것이
# 일상 실행에서 이미 보장되므로 백필용 크기를 따로 정할 이유가 없다.
SPAN_CALENDAR_DAYS = 200


def _credentials() -> tuple[SecretStr, SecretStr]:
    app_key = os.environ.get("KIS_APP_KEY")
    app_secret = os.environ.get("KIS_APP_SECRET")
    if not app_key or not app_secret:
        raise AirflowFailException("KIS_APP_KEY and KIS_APP_SECRET are required")
    return SecretStr(app_key), SecretStr(app_secret)


def _connection() -> Any:
    return PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()


def _calendar_day(given: Any, name: str) -> date:
    """`YYYY-MM-DD` 하나를 읽는다. 모양을 먼저 본다(`kis_investor_trade_daily`와 같은 이유)."""
    text = str(given).strip()
    if not CALENDAR_DAY_PATTERN.fullmatch(text):
        raise AirflowFailException(f"{name} must be YYYY-MM-DD, got {given!r}")
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise AirflowFailException(f"{name} must be YYYY-MM-DD, got {given!r}") from None


def requested_end_date(now_kst: datetime, params: dict[str, Any]) -> date:
    """이 run이 구간의 끝으로 쓸 날짜."""
    given = params.get(END_DATE_PARAM)
    if not given:
        return now_kst.date()
    return _calendar_day(given, END_DATE_PARAM)


def requested_start_date(end_date: date, params: dict[str, Any]) -> date:
    """이 run이 구간의 시작으로 쓸 날짜. 비우면 200달력일 앞이다.

    끝보다 뒤인 시작은 조용히 빈 구간이 되므로 막는다.
    """
    given = params.get(START_DATE_PARAM)
    if not given:
        return span_start(end_date)
    start_date = _calendar_day(given, START_DATE_PARAM)
    if start_date > end_date:
        raise AirflowFailException(f"{START_DATE_PARAM} {start_date} must not be after {END_DATE_PARAM} {end_date}")
    return start_date


def span_start(end_date: date) -> date:
    return end_date - timedelta(days=SPAN_CALENDAR_DAYS)


def fetch_windows(start_date: date, end_date: date) -> list[tuple[date, date]]:
    """조회 구간을 `SPAN_CALENDAR_DAYS`씩 끊는다. 오래된 창이 먼저다.

    한 심볼의 페이지 상한(`INDEX_DAILY_MAX_PAGES`)을 넘지 않으려고 나눈다. 일상 실행은
    구간이 정확히 200달력일이라 창 하나가 나오고 동작이 바뀌지 않는다.
    """
    windows: list[tuple[date, date]] = []
    cursor = start_date
    while cursor <= end_date:
        window_end = min(cursor + timedelta(days=SPAN_CALENDAR_DAYS), end_date)
        windows.append((cursor, window_end))
        cursor = window_end + timedelta(days=1)
    return windows


@dag(
    dag_id="kis_index_daily",
    dag_display_name="📅 국내 지수 확정 일봉 (KIS)",
    description="KOSPI·KOSDAQ 확정 일봉을 최근 200달력일 창으로 받아 기술지표의 원천으로 저장한다.",
    schedule="20 18 * * 1-5",  # KST 월~금 18:20 = UTC 월~금 09:20
    start_date=pendulum.datetime(2026, 8, 24, tz=KST_TIMEZONE),  # KST 2026-08-24 00:00 = UTC 2026-08-23 15:00
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=10)},
    params={
        END_DATE_PARAM: Param(
            None,
            type=["null", "string"],
            title="구간의 끝",
            description="YYYY-MM-DD. 비우면 실행일(KST). 이 날짜를 끝으로 최근 200달력일을 받는다.",
        ),
        START_DATE_PARAM: Param(
            None,
            type=["null", "string"],
            title="구간의 시작",
            description=(
                f"YYYY-MM-DD. 비우면 구간의 끝에서 {SPAN_CALENDAR_DAYS}달력일 앞. "
                f"이력 백필에 쓴다 — 구간을 {SPAN_CALENDAR_DAYS}달력일씩 끊어 반복 조회한다."
            ),
        ),
    },
    doc_md=__doc__,
    tags=["kis", "market", "daily", "korea", "index"],
)
def kis_index_daily():
    @task(task_display_name="지수 일봉 수집·저장")
    def collect() -> int:
        context = get_current_context()
        params = dict(context.get("params") or {})

        now_kst = datetime.now(UTC).astimezone(KST_TIMEZONE)
        end_date = requested_end_date(now_kst, params)
        start_date = requested_start_date(end_date, params)
        windows = fetch_windows(start_date, end_date)

        # 자동 실행만 휴장일을 건너뛴다. 백필은 끝 날짜가 휴장일이어도 구간 안의 거래일이
        # 목적이므로 막을 이유가 없다.
        if not params.get(END_DATE_PARAM) and not params.get(START_DATE_PARAM):
            connection = _connection()
            try:
                closed = krx_open_day(connection, end_date) is False
            finally:
                connection.close()
            if closed:
                raise AirflowSkipException(f"KRX is closed on {end_date}")

        app_key, app_secret = _credentials()
        collector = KisIndexDailyCollector(access_token(Variable, app_key, app_secret), app_key, app_secret)

        stored = 0
        failures: list[str] = []
        with closing(_connection()) as connection:
            for index in MOVEMENT_INDEXES:
                # 창 하나가 실패하면 그 심볼의 남은 창은 건너뛴다. 구멍 난 구간 위에 나머지를
                # 얹어 봐야 지표 계산이 그 구멍에서 멈춘다.
                for window_start, window_end in windows:
                    try:
                        fetch = collector.fetch(index, window_start, window_end)
                    except KisHTTPError as error:
                        if error.status in KIS_UNRECOVERABLE_STATUSES:
                            raise AirflowFailException(f"{index.value}: {error}") from error
                        logger.warning("%s failed with HTTP %s", index.value, error.status)
                        failures.append(f"{index.value} {window_start}~{window_end}({error})")
                        break
                    except KisTimeWindowError as error:
                        # 제공처가 지금은 이 조회를 받지 않는다(응답 본문이 창을 말해 준다). 재시도는 같은
                        # 답을 받으며 예산만 태우므로 즉시 죽인다. 사람이 시각을 맞춰 다시 트리거한다.
                        raise AirflowFailException(f"{index.value}: {error}. 제한 시각 뒤에 다시 트리거한다.") from error
                    except (KisResultError, KisPayloadError) as error:
                        logger.warning("%s %s~%s failed: %s", index.value, window_start, window_end, error)
                        failures.append(f"{index.value} {window_start}~{window_end}({error})")
                        break
                    except ConnectionError as error:
                        logger.warning("%s %s~%s failed to connect: %s", index.value, window_start, window_end, error)
                        failures.append(f"{index.value} {window_start}~{window_end}({error})")
                        break

                    with atomic(connection):
                        rows = collector.store(connection, fetch)
                    stored += rows
                    logger.info(
                        "Stored %s daily bars for %s %s~%s in %s pages",
                        rows,
                        index.value,
                        window_start,
                        window_end,
                        fetch.page_count,
                    )

        if failures:
            raise AirflowFailException(f"Index daily collection failed for: {'; '.join(failures)}")
        return stored

    collect()


kis_index_daily = kis_index_daily()
