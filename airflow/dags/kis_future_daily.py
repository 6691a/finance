"""국내 지수선물 확정 일봉 수집 DAG.

분봉(`kis_quote_intraday`)은 당일 흐름용이고, 이 DAG는 상관 분석의 원천이 되는 **확정 일봉**을
받는다. 설계는 docs/collection/kis-index-daily-collection.md 4절이다.

현물 지수 일봉(`kis_index_daily`)과 한 DAG으로 합치지 않는다. 같은 KRX 거래일을 보지만 조회
엔드포인트와 실패 원인이 다르고, 무엇보다 **선물에는 월물 축이 하나 더 있다.** 파생 조회가
막혔을 때 현물 일봉까지 세우면 기술지표가 함께 멈춘다.

## 왜 200달력일인가

현물 일봉과 같은 창이다. 매일 같은 창을 다시 받으므로 실패한 날이 저절로 메워지고,
`index_future_daily/upsert.sql`이 (provider, symbol, business_date)로 멱등 갱신한다.
계산은 `modules/period.py`에 한 벌 있다.

## 월물과 롤오버

저장 심볼은 `KOSPI200_FUT`·`KOSDAQ150_FUT`이고 월물과 무관하다. 실제로 조회한 계약은
행마다 `contract_code`에 남는다. 이 값이 없으면 월물이 바뀐 날의 갭이 시장 급변인지
롤오버인지 구분되지 않는다.

조회 구간은 먼저 월물 창으로 갈린다(`contract_windows`). 만기일 봉까지 그 월물이고 다음
거래일부터 차기월물이다. **가격 차이를 소급 조정하지 않는다** — 롤오버 수익률이 실제
기초자산 수익률이 아니라는 사실은 조회·분석 계층이 `contract_code` 변경일로 판단한다.

## 페이지 이어받기

응답 헤더 `tr_cont`는 오지 않는다(2026-08-27 실측). 한 응답이 100행이라 200달력일 창은
두 장이고, 가장 오래된 날짜의 전날로 종료일을 옮겨 걷는다. 판단은
`KisFutureDailyCollector.fetch`가 한다.

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `end_date` | `null` | 구간의 끝(YYYY-MM-DD). 비우면 실행일(KST) |
| `start_date` | `null` | 구간의 시작(YYYY-MM-DD). 비우면 `end_date`에서 200달력일 앞 |

## 이력 백필

`start_date`를 주면 그 날짜까지 거슬러 올라간다. **만기된 계약도 과거 날짜로 조회된다**
(실측: `A01606`이 만기일 20260611까지 69행). 그래서 백필에 하한이 없다.

```bash
airflow dags trigger kis_future_daily \
  --conf '{"start_date": "2025-01-01", "end_date": "2026-08-27"}'
```

**사용자가 계약 코드를 직접 넣는 파라미터는 두지 않는다.** 논리 시계열의 롤 규칙을 우회할
통로가 되고, 그렇게 들어간 봉은 나중에 어느 규칙으로 모였는지 알 수 없다.

월물 코드는 연도를 한 자리로 담는다(`A01609`). 10년을 넘는 백필은 `A01609`가 2016년 9월물과
겹치므로 그 전에 `contract_code()`의 형식을 넓혀야 한다.

## 실패와 재시도

- **한 심볼이 실패해도 다른 심볼은 저장한다.** 심볼 하나가 트랜잭션 하나다. 마지막에
  실패 목록이 있으면 태스크를 죽인다.
- **하루 한 번 도는 확정 수집이라 하나만 실패해도 죽인다.** 그날 값을 다시 집는 실행이
  없다. `kis_index_daily`와 같은 판단이다.
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
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pendulum
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Param, Variable, dag, get_current_context, task
from airflow.sdk.exceptions import AirflowFailException, AirflowSkipException
from pydantic import SecretStr

from modules.collectors.kis import (
    DomesticFuture,
    KisHTTPError,
    KisPayloadError,
    KisResultError,
    KisTimeWindowError,
    access_token,
)
from modules.collectors.market.kis_future_daily import KisFutureDailyCollector
from modules.market_session import krx_open_day
from modules.period import (
    END_DATE_PARAM,
    SPAN_CALENDAR_DAYS,
    START_DATE_PARAM,
    PeriodError,
    calendar_day,
    fetch_windows,
    span_start,
)
from modules.utility import CONNECTION_ID, KIS_UNRECOVERABLE_STATUSES, KST_TIMEZONE, atomic

logger = logging.getLogger(__name__)


def _credentials() -> tuple[SecretStr, SecretStr]:
    app_key = os.environ.get("KIS_APP_KEY")
    app_secret = os.environ.get("KIS_APP_SECRET")
    if not app_key or not app_secret:
        raise AirflowFailException("KIS_APP_KEY and KIS_APP_SECRET are required")
    return SecretStr(app_key), SecretStr(app_secret)


def _connection() -> Any:
    return PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()


def _calendar_day(given: Any, name: str) -> date:
    """`YYYY-MM-DD` 하나를 읽는다. 규칙은 `modules/period.py`에 한 벌 있다.

    여기 남는 것은 그 실패를 어떤 Airflow 예외로 올릴지뿐이다.
    """
    try:
        return calendar_day(given, name)
    except PeriodError as error:
        raise AirflowFailException(str(error)) from None


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


@dag(
    dag_id="kis_future_daily",
    dag_display_name="📅 국내 지수선물 확정 일봉 (KIS)",
    description="코스피200·코스닥150 선물 확정 일봉을 최근 200달력일 창으로 받아 실제 월물과 함께 저장한다.",
    schedule="30 18 * * 1-5",  # KST 월~금 18:30 = UTC 월~금 09:30
    start_date=pendulum.datetime(2026, 8, 28, tz=KST_TIMEZONE),  # KST 2026-08-28 00:00 = UTC 2026-08-27 15:00
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
                f"이력 백필에 쓴다 — 구간을 {SPAN_CALENDAR_DAYS}달력일씩 끊고, 그 안에서 다시 월물 창으로 나눈다."
            ),
        ),
    },
    doc_md=__doc__,
    tags=["kis", "market", "daily", "korea", "future"],
)
def kis_future_daily():
    @task(task_display_name="지수선물 일봉 수집·저장")
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
        collector = KisFutureDailyCollector(access_token(Variable, app_key, app_secret), app_key, app_secret)

        stored = 0
        failures: list[str] = []
        with closing(_connection()) as connection:
            for future in DomesticFuture:
                # 창 하나가 실패하면 그 심볼의 남은 창은 건너뛴다. 구멍 난 구간 위에 나머지를
                # 얹어 봐야 수익률 계산이 그 구멍에서 멈춘다.
                for window_start, window_end in windows:
                    try:
                        fetch = collector.fetch(future, window_start, window_end)
                    except KisHTTPError as error:
                        if error.status in KIS_UNRECOVERABLE_STATUSES:
                            raise AirflowFailException(f"{future.value}: {error}") from error
                        logger.warning("%s failed with HTTP %s", future.value, error.status)
                        failures.append(f"{future.value} {window_start}~{window_end}({error})")
                        break
                    except KisTimeWindowError as error:
                        # 제공처가 지금은 이 조회를 받지 않는다(응답 본문이 창을 말해 준다). 재시도는 같은
                        # 답을 받으며 예산만 태우므로 즉시 죽인다. 사람이 시각을 맞춰 다시 트리거한다.
                        raise AirflowFailException(
                            f"{future.value}: {error}. 제한 시각 뒤에 다시 트리거한다."
                        ) from error
                    except (KisResultError, KisPayloadError) as error:
                        logger.warning("%s %s~%s failed: %s", future.value, window_start, window_end, error)
                        failures.append(f"{future.value} {window_start}~{window_end}({error})")
                        break
                    except ConnectionError as error:
                        logger.warning("%s %s~%s failed to connect: %s", future.value, window_start, window_end, error)
                        failures.append(f"{future.value} {window_start}~{window_end}({error})")
                        break

                    with atomic(connection):
                        rows = collector.store(connection, fetch)
                    stored += rows
                    logger.info(
                        "Stored %s daily bars for %s %s~%s from %s in %s pages",
                        rows,
                        future.value,
                        window_start,
                        window_end,
                        ",".join(fetch.contracts),
                        fetch.page_count,
                    )

        if failures:
            raise AirflowFailException(f"Future daily collection failed for: {'; '.join(failures)}")
        return stored

    collect()


kis_future_daily = kis_future_daily()
