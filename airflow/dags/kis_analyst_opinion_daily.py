"""증권사 애널리스트 투자의견·목표주가 일별 수집 DAG.

뉴스가 "무슨 일이 있었다"라면 투자의견은 **"그래서 이 종목을 어떻게 보나"**다. 목표주가가
올랐는지, 의견이 바뀌었는지는 지수·분봉·수급만 봐서는 안 보이는 축이고, 장전 추론이
`analyst_opinions` 툴로 읽는다.

투자의견은 **당일 아침 사건**이다. 증권사 리포트가 장 시작 전에 나오므로 장전 추론(08:35 KST)
직전에 한 번 받는다. `kis_market_positioning_daily`가 전 영업일 확정치를 화~토에 받는 것과
달리 월~금에 돈다. 재시도 간격도 짧다 — 한 시간을 기다리면 장전 추론을 넘긴다.

수집 규칙은 `modules/collectors/analyst/kis_opinion.py`의 `KisAnalystOpinionCollector`에 있다.

## 종목은 DB가 정한다

`instrument.is_watched`를 읽는다. 추론 대상(`thesis.subjects`)과 같은 목록이라 추적 종목이
늘 때 이 DAG를 고치지 않는다.

## 실패를 어떻게 다루는가

**호출 하나가 트랜잭션 하나다.** 종목 하나의 응답은 즉시 커밋하고 다음 종목으로 넘어간다.
루프가 끝난 뒤 실패가 하나라도 있으면 태스크를 실패시켜 재시도한다. 자연키 upsert라 재시도가
중복 행을 만들지 않는다. 설정·권한 문제(HTTP 4xx)는 재시도해도 같으므로 즉시 실패다.

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `observation_start` | `null` | 조회 시작일. 주면 `lookback_days`를 무시한다 |
| `observation_end` | `null` | 조회 종료일. 비우면 이 run 시각의 KST 날짜 |
| `lookback_days` | `7` | 구간을 지정하지 않을 때 되돌아볼 일수 |

    airflow dags trigger kis_analyst_opinion_daily \\
      --conf '{"observation_start": "2026-06-01", "observation_end": "2026-08-21"}'

구간이 길면 KIS가 연속조회(`tr_cont`)로 자르고 수집기는 그것을 실패로 만든다. 백필은 한 달
단위로 나눠 돌린다.

## 필요한 환경

- `KIS_APP_KEY`, `KIS_APP_SECRET`. Airflow가 읽는 건 `compose/local/airflow/.env`다.
- `CONNECTION_ID`가 가리키는 Airflow 연결. 접속 정보는 `AIRFLOW_CONN_FINANCE`가 갖는다.

토큰은 다른 KIS DAG와 같은 Airflow Variable 캐시를 공유한다. 발급 횟수 제한이 있어 DAG마다
따로 받지 않는다.
"""

import logging
import os
from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import Any

import pendulum
from airflow.exceptions import AirflowFailException, AirflowSkipException
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Param, Variable, dag, get_current_context, task
from pydantic import SecretStr

from modules.collectors.analyst.kis_opinion import KisAnalystOpinionCollector, watched_stocks
from modules.collectors.kis import (
    KisHTTPError,
    KisPayloadError,
    KisResultError,
    access_token,
)
from modules.market_session import krx_open_day
from modules.period import (
    LOOKBACK_DAYS,
    LOOKBACK_DAYS_PARAM,
    OBSERVATION_END_PARAM,
    OBSERVATION_START_PARAM,
    PeriodError,
    resolve_observation_period,
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


def _skip_when_closed(connection: Any, today_kst) -> None:
    """확정 휴장일이면 태스크를 건너뛴다. 행이 없거나 아직 판정하지 않았으면 그대로 수집한다."""
    if krx_open_day(connection, today_kst) is False:
        raise AirflowSkipException(f"KRX is closed on {today_kst}")


@dag(
    dag_id="kis_analyst_opinion_daily",
    dag_display_name="🎯 종목 투자의견·목표주가 (KIS)",
    description="평일 장전에 추적 종목의 증권사 투자의견·목표주가를 받아 저장한다. 장전 추론의 analyst_opinions 툴이 읽는다.",
    schedule="20 8 * * 1-5",  # KST 평일 08:20 = UTC 일~목 23:20
    start_date=pendulum.datetime(2026, 8, 22, tz=KST_TIMEZONE),  # KST 2026-08-22 00:00 = UTC 2026-08-21 15:00
    catchup=False,
    max_active_runs=1,
    # 장전 추론(08:35)을 넘기지 않도록 짧게 두 번 시도한다.
    default_args={"retries": 2, "retry_delay": timedelta(minutes=5)},
    params={
        OBSERVATION_START_PARAM: Param(
            None,
            type=["null", "string"],
            format="date",
            title="조회 시작일",
            description="비우면 observation_end에서 lookback_days만큼 뺀 날. 주면 lookback_days를 무시한다.",
        ),
        OBSERVATION_END_PARAM: Param(
            None,
            type=["null", "string"],
            format="date",
            title="조회 종료일",
            description="비우면 이 run 시각의 KST 날짜. 과거 구간을 넣을 때 직접 넘긴다.",
        ),
        LOOKBACK_DAYS_PARAM: Param(
            LOOKBACK_DAYS,
            type="integer",
            minimum=1,
            title="되돌아볼 일수",
            description="구간을 지정하지 않을 때만 쓴다. 의견은 드문드문 나오므로 한 주를 다시 받아도 upsert라 무해하다.",
        ),
    },
    doc_md=__doc__,
    tags=["kis", "stock", "daily", "korea", "analyst"],
)
def kis_analyst_opinion_daily():
    @task(task_display_name="종목 투자의견")
    def collect_opinions() -> int:
        context = get_current_context()
        try:
            observation_start, observation_end = resolve_observation_period(context)
        except PeriodError as error:
            raise AirflowFailException(str(error)) from error

        app_key, app_secret = _credentials()
        collector = KisAnalystOpinionCollector(access_token(Variable, app_key, app_secret), app_key, app_secret)

        stored = 0
        failures: list[str] = []
        with closing(_connection()) as connection:
            _skip_when_closed(connection, datetime.now(UTC).astimezone(KST_TIMEZONE).date())
            stocks = watched_stocks(connection)
            if not stocks:
                raise AirflowFailException("instrument has no watched stock; nothing to collect")

            # 호출 하나가 트랜잭션 하나다. 앞의 성공을 뒤의 실패가 되돌리지 않는다.
            for stock_code, name in stocks:
                label = f"opinion:{stock_code}"
                try:
                    fetch = collector.fetch(stock_code, observation_start, observation_end)
                except KisHTTPError as error:
                    if error.status in KIS_UNRECOVERABLE_STATUSES:
                        raise AirflowFailException(f"{label}: {error}") from error
                    logger.warning("%s failed with HTTP %s", label, error.status)
                    failures.append(label)
                    continue
                except (KisResultError, KisPayloadError) as error:
                    logger.warning("%s failed: %s", label, error)
                    failures.append(label)
                    continue
                except ConnectionError as error:
                    logger.warning("%s failed to connect: %s", label, error)
                    failures.append(label)
                    continue

                with atomic(connection):
                    rows = collector.store(connection, fetch)

                stored += rows
                logger.info("Stored %s opinion rows for %s (%s)", rows, name, stock_code)

        if failures:
            raise AirflowFailException(f"{len(failures)} of {len(stocks)} KIS calls failed: {', '.join(failures)}")

        logger.info("Stored %s analyst opinion rows for %s..%s", stored, observation_start, observation_end)
        return stored

    collect_opinions()


kis_analyst_opinion_daily = kis_analyst_opinion_daily()
