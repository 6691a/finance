"""종목 추정 수급 장중 수집 DAG.

KIS가 개별 종목의 외국인·기관 **추정** 순매수를 하루 몇 차례만 집계해 고시한다. 시장 누적
수급(`kis_investor_flow_intraday`)과 같은 화면에 실리지만 조회도 실패 판정도 다르다.

| | 시장 누적 | 종목 추정(이 DAG) |
| --- | --- | --- |
| 갱신 | 장중 내내 | 하루 다섯 번 |
| 0행의 뜻 | 시장 코드 오류라 **실패** | 갱신 전이라 **정상** |
| 놓쳤을 때 | 5분 뒤 run 이 따라잡는다 | 다음 슬롯이 한두 시간 뒤 |

셋째 줄이 재시도 정책을 가른다. 시장 누적은 누적값이라 한 번 걸러도 다음 run 이 같은 값을
싣지만, 여기는 그 시점 추정치가 그 슬롯에만 있다. 그래서 재시도를 더 준다.

## 왜 시장 누적과 한 DAG 가 아닌가

전에는 한 DAG 였고, `include_stock_estimates` 파라미터가 비면 **벽시계**로 "지금이 갱신
시각인가"를 판단했다. 갱신 시각이 아닐 때 UI 의 Trigger 를 누르면 종목 추정이 조용히 빠진
채 태스크가 성공했다. 저장소 규칙(`.claude/CLAUDE.md`의 "슬롯·모드로 갈리는 DAG 는
나눈다")이 금지하는 형태라 갈랐다. 이제 이 DAG 는 언제 눌러도 조회한다.

가른 뒤 따라온 것이 하나 더 있다. 전에는 갱신 시각(09:35·10:05·11:25·13:25·14:35)이 전부
5의 배수라서 시장 쪽 `*/5` 스케줄에 얹혀 돌았다. 그 주기를 건드리면 추정 조회가 조용히
죽는 결합이었는데, 이제 이 DAG 가 자기 시각을 직접 갖는다.

## 갱신 시각

공식 예제 기준으로 외국인은 09:30·11:20·13:20·14:30, 기관은 10:00·11:20·13:20·14:30에
갱신된다. 스케줄은 그 시각을 조금 지나서 부른다.

**시각은 변동될 수 있다.** 그래서 슬롯 코드(`bsop_hour_gb`)를 자연키로 쓰고 우리가 시각을
지어내지 않는다. 갱신 시각이 바뀌어도 슬롯이 늘면 행이 늘 뿐이고, 여기 스케줄은 "언제
부를지"만 정한다. 목록이 낡아도 값이 틀리지는 않고 늦게 들어올 뿐이다.

수집 규칙은 `modules/collectors/market/kis_investor_flow.py`에 있다.

## 실패와 재시도

- **한 종목이 실패해도 다른 종목은 저장한다.** 호출 하나가 트랜잭션 하나다.
- 하나라도 실패하면 태스크를 죽인다. 다른 `kis_*` DAG 와 같다.
- HTTP 400/403/404: 설정 오류라 즉시 실패한다.
- **0행은 정상이다.** 갱신 전이면 슬롯이 없다. 시장 누적의 all-zero 가드에 해당하는 것이
  여기에는 없다 — 종목 코드는 Enum 이 막는다.

## 필요한 환경

- `KIS_APP_KEY`, `KIS_APP_SECRET`. Airflow가 읽는 건 `compose/local/airflow/.env`다.
- `CONNECTION_ID`가 가리키는 Airflow 연결. 접속 정보는 `AIRFLOW_CONN_FINANCE`가 갖는다.

토큰은 다른 KIS DAG와 같은 Airflow Variable 캐시를 공유한다.
"""

import logging
import os
from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import Any

import pendulum
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Variable, dag, task
from airflow.sdk.exceptions import AirflowFailException, AirflowSkipException
from airflow.timetables.trigger import MultipleCronTriggerTimetable
from pydantic import SecretStr

from modules.collectors.kis import (
    KisHTTPError,
    KisPayloadError,
    KisResultError,
    KisTimeWindowError,
    access_token,
)
from modules.collectors.market.kis_investor_flow import (
    InvestorFlowStock,
    KisInvestorFlowCollector,
)
from modules.market_session import krx_open_day
from modules.utility import CONNECTION_ID, KIS_UNRECOVERABLE_STATUSES, KST_TIMEZONE, atomic

logger = logging.getLogger(__name__)

# 공식 갱신 시각을 조금 지난 뒤에 부른다. 분이 제각각이라 cron 하나로 못 적는다.
# 시각을 바꾸려면 이 목록만 고친다.
SCHEDULE = MultipleCronTriggerTimetable(
    "35 9 * * 1-5",  # KST 평일 09:35 외국인 1차 = UTC 00:35
    "5 10 * * 1-5",  # KST 평일 10:05 기관 1차 = UTC 01:05
    "25 11 * * 1-5",  # KST 평일 11:25 = UTC 02:25
    "25 13 * * 1-5",  # KST 평일 13:25 = UTC 04:25
    "35 14 * * 1-5",  # KST 평일 14:35 = UTC 05:35
    timezone=KST_TIMEZONE,
)


def _credentials() -> tuple[SecretStr, SecretStr]:
    app_key = os.environ.get("KIS_APP_KEY")
    app_secret = os.environ.get("KIS_APP_SECRET")
    if not app_key or not app_secret:
        raise AirflowFailException("KIS_APP_KEY and KIS_APP_SECRET are required")
    return SecretStr(app_key), SecretStr(app_secret)


def _connection() -> Any:
    return PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()


@dag(
    dag_id="kis_investor_estimate_intraday",
    dag_display_name="🔍 종목 추정 수급 (KIS)",
    description="KIS가 하루 다섯 번 갱신하는 종목별 외국인·기관 추정 순매수를 그 직후에 받아 저장한다.",
    schedule=SCHEDULE,
    start_date=pendulum.datetime(2026, 8, 15, tz=KST_TIMEZONE),  # KST 2026-08-15 00:00 = UTC 2026-08-14 15:00
    catchup=False,
    max_active_runs=1,
    # 슬롯을 놓치면 다음이 한두 시간 뒤라 재시도가 값어치 있다. 시장 누적 쪽과 다른 점이다.
    default_args={"retries": 2, "retry_delay": timedelta(minutes=5)},
    doc_md=__doc__,
    tags=["kis", "market", "intraday", "korea", "investor"],
)
def kis_investor_estimate_intraday():
    @task(task_display_name="종목 추정 수급 수집·저장")
    def collect() -> int:
        now_kst = datetime.now(UTC).astimezone(KST_TIMEZONE)

        connection = _connection()
        try:
            closed = krx_open_day(connection, now_kst.date()) is False
        finally:
            connection.close()
        if closed:
            raise AirflowSkipException(f"KRX is closed on {now_kst.date()}")

        app_key, app_secret = _credentials()
        collector = KisInvestorFlowCollector(access_token(Variable, app_key, app_secret), app_key, app_secret)

        # 응답에 날짜가 없어 호출자가 넘긴다. 정규장 안에서만 도니 KST 오늘이 영업일이다.
        business_date = now_kst.date()

        stored = 0
        failures: list[str] = []
        with closing(_connection()) as connection:
            for stock in InvestorFlowStock:
                try:
                    fetch = collector.fetch_stock_estimates(stock, business_date)
                except KisHTTPError as error:
                    if error.status in KIS_UNRECOVERABLE_STATUSES:
                        raise AirflowFailException(f"{stock.value}: {error}") from error
                    logger.warning("%s failed with HTTP %s", stock.value, error.status)
                    failures.append(f"{stock.value}({error})")
                    continue
                except KisTimeWindowError as error:
                    # 제공처가 지금은 이 조회를 받지 않는다(응답 본문이 창을 말해 준다). 재시도는 같은
                    # 답을 받으며 예산만 태우므로 즉시 죽인다. 사람이 시각을 맞춰 다시 트리거한다.
                    raise AirflowFailException(f"{stock.value}: {error}. 제한 시각 뒤에 다시 트리거한다.") from error
                except (KisResultError, KisPayloadError) as error:
                    logger.warning("%s failed: %s", stock.value, error)
                    failures.append(f"{stock.value}({error})")
                    continue
                except ConnectionError as error:
                    logger.warning("%s failed to connect: %s", stock.value, error)
                    failures.append(f"{stock.value}({error})")
                    continue

                with atomic(connection):
                    rows = collector.store_stock_estimates(connection, fetch)

                stored += rows
                logger.info("Stored %s rows for %s", rows, stock.value)

        if failures:
            raise AirflowFailException(
                f"{len(failures)} of {len(InvestorFlowStock)} KIS calls failed: {'; '.join(failures)}"
            )

        logger.info("Stored %s stock estimate rows for %s", stored, business_date.isoformat())
        return stored

    collect()


kis_investor_estimate_intraday = kis_investor_estimate_intraday()
