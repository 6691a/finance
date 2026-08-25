"""KOSPI·KOSDAQ 확정 일봉 수집 DAG.

분봉(`kis_quote_intraday`)은 당일 흐름용이고, 이 DAG는 기술적 보조지표(SMA·RSI·MACD)의
원천이 되는 **확정 일봉**을 받는다. 설계는 docs/market-technical-indicators.md 4절이다.

## 왜 200달력일인가

SMA60과 EMA 안정화에 120거래일이 필요하다. 연휴가 포함된 구간에서도 그만큼을 확보하는
고정 수집 창이 200달력일이다. 매일 같은 창을 다시 받으므로 실패한 날이 저절로 메워지고,
`index_daily/upsert.sql`이 (provider, symbol, business_date)로 멱등 갱신한다.

## 페이지 이어받기

응답 헤더 `tr_cont`가 오면 연속조회로, 헤더 없이 요청 구간의 시작에 못 닿은 응답이 오면
(확정 수급 API의 행태) 날짜 창을 뒤로 옮겨 받는다. 판단은 `fetch_index_daily`가 한다.
한 장의 봉 수로 잘림을 재지 않는다 — 그 상한은 문서에 없고 제공처가 바꿔도 알려 주지 않는다.
마지막 장까지 받고도 남았으면 그 심볼은 저장하지 않고 실패한다 — 잘린 구간은 지표 계산
창에 구멍을 남긴다.

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `end_date` | `null` | 구간의 끝(YYYY-MM-DD). 비우면 실행일(KST) |

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
from airflow.exceptions import AirflowFailException, AirflowSkipException
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Param, Variable, dag, get_current_context, task
from pydantic import SecretStr

from modules.collectors.kis import (
    KisHTTPError,
    KisPayloadError,
    KisResultError,
    access_token,
)
from modules.collectors.market.kis_quote import (
    MOVEMENT_INDEXES,
    KisQuoteCollector,
)
from modules.market_session import krx_open_day
from modules.utility import CONNECTION_ID, KIS_UNRECOVERABLE_STATUSES, KST_TIMEZONE, atomic

logger = logging.getLogger(__name__)

# 달력 하루만 받는다. ISO 주 표기(2026-W34)와 기본형(20260821)을 걸러 내는 그물이다.
CALENDAR_DAY_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")

END_DATE_PARAM = "end_date"

# SMA60과 EMA 안정화에 필요한 120거래일을 연휴 포함 구간에서도 확보하는 고정 창(4.4절).
SPAN_CALENDAR_DAYS = 200


def _credentials() -> tuple[SecretStr, SecretStr]:
    app_key = os.environ.get("KIS_APP_KEY")
    app_secret = os.environ.get("KIS_APP_SECRET")
    if not app_key or not app_secret:
        raise AirflowFailException("KIS_APP_KEY and KIS_APP_SECRET are required")
    return SecretStr(app_key), SecretStr(app_secret)


def _connection() -> Any:
    return PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()


def requested_end_date(now_kst: datetime, params: dict[str, Any]) -> date:
    """이 run이 구간의 끝으로 쓸 날짜. 모양을 먼저 본다(`kis_investor_trade_daily`와 같은 이유)."""
    given = params.get(END_DATE_PARAM)
    if not given:
        return now_kst.date()
    text = str(given).strip()
    if not CALENDAR_DAY_PATTERN.fullmatch(text):
        raise AirflowFailException(f"{END_DATE_PARAM} must be YYYY-MM-DD, got {given!r}")
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise AirflowFailException(f"{END_DATE_PARAM} must be YYYY-MM-DD, got {given!r}") from None


def span_start(end_date: date) -> date:
    return end_date - timedelta(days=SPAN_CALENDAR_DAYS)


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
        start_date = span_start(end_date)

        # 자동 실행만 휴장일을 건너뛴다. 백필은 끝 날짜가 휴장일이어도 구간 안의 거래일이
        # 목적이므로 막을 이유가 없다.
        if not params.get(END_DATE_PARAM):
            connection = _connection()
            try:
                closed = krx_open_day(connection, end_date) is False
            finally:
                connection.close()
            if closed:
                raise AirflowSkipException(f"KRX is closed on {end_date}")

        app_key, app_secret = _credentials()
        collector = KisQuoteCollector(access_token(Variable, app_key, app_secret), app_key, app_secret)

        stored = 0
        failures: list[str] = []
        with closing(_connection()) as connection:
            for index in MOVEMENT_INDEXES:
                try:
                    fetch = collector.fetch_index_daily(index, start_date, end_date)
                except KisHTTPError as error:
                    if error.status in KIS_UNRECOVERABLE_STATUSES:
                        raise AirflowFailException(f"{index.value}: {error}") from error
                    logger.warning("%s failed with HTTP %s", index.value, error.status)
                    failures.append(index.value)
                    continue
                except (KisResultError, KisPayloadError) as error:
                    logger.warning("%s failed: %s", index.value, error)
                    failures.append(index.value)
                    continue
                except ConnectionError as error:
                    logger.warning("%s failed to connect: %s", index.value, error)
                    failures.append(index.value)
                    continue

                with atomic(connection):
                    rows = collector.store_index_daily(connection, fetch)
                stored += rows
                logger.info("Stored %s daily bars for %s in %s pages", rows, index.value, fetch.page_count)

        if failures:
            raise AirflowFailException(f"Index daily collection failed for: {', '.join(failures)}")
        return stored

    collect()


kis_index_daily = kis_index_daily()
