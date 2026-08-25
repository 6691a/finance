"""외국인·기관·개인 수급 장중 수집 DAG.

가격이 "얼마에 거래됐나"이고 포지션이 "누가 들고 있나"라면, 수급은 **"지금 누가 사고 누가
파나"**다. 지수가 오르는데 외국인이 팔고 개인이 받는 장과, 외국인이 사는 장은 다음 날이
다르다.

수집 규칙은 `modules/collectors/market/kis_investor_flow.py`에 있다.

## 두 조회의 주기가 다르다

| 조회 | 주기 | 이유 |
| --- | --- | --- |
| 시장 누적 | 5분마다 × 7시장 | 장중 내내 갱신되는 누적 스냅샷이다 |
| 종목 추정 | **지정 시각에만** | KIS가 하루 몇 차례만 집계한다 |

종목 추정값은 실시간이 아니다. 공식 예제 기준으로 외국인은 09:30·11:20·13:20·14:30,
기관은 10:00·11:20·13:20·14:30에 갱신된다. 그 시각들을 지나 조금 뒤에만 부른다. 5분마다
불러도 같은 값이 반복될 뿐이고 호출만 늘어난다.

**시각은 변동될 수 있다.** 그래서 슬롯 코드(`bsop_hour_gb`)를 자연키로 쓰고 우리가 시각을
지어내지 않는다. 갱신 시각이 바뀌어도 슬롯이 늘면 행이 늘 뿐이다.

## 시장 코드는 문서에 적힌 것만 쓴다

잘못된 시장 코드는 오류가 아니라 값 0으로 온다(실측). 그래서 수집기 Enum에는 공식 postman
컬렉션에 적힌 코드만 있고, **모든 값이 0인 응답은 실패로 다룬다.** 코드표와 근거는
`modules/collectors/market/kis_investor_flow.py`의 "시장 코드는 문서에 다 있다" 절에 있다.

일곱 시장을 받는다. 코스피·코스닥 현물 둘에 선물·콜옵션·풋옵션·주식선물·ETF다. 응답 모양이
일곱 다 같아서 여기서는 대상이 늘 뿐 분기가 없다. **파생은 수량이 주가 아니라 계약이다** —
저장은 표기 그대로 하고 섞지 않는 책임은 조회하는 쪽에 있다.

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `include_stock_estimates` | `null` | 종목 추정을 이 run 에서 부를지. 비우면 시각으로 판단한다 |

    airflow dags trigger kis_investor_flow_intraday \\
      --conf '{"include_stock_estimates": true}'

## 실패와 재시도

- **한 대상이 실패해도 다른 대상은 저장한다.** 호출 하나가 트랜잭션 하나다.
- HTTP 400/403/404: 설정 오류라 즉시 실패한다.
- 시장 응답이 전부 0이면 실패시킨다. 코드가 틀렸다는 뜻이다. 단 09:05 이전 run 은
  skip 한다. 장 시작 직후에는 옳은 코드도 첫 집계 전이라 전부 0으로 온다.
- 종목 응답 0행은 정상이다. 갱신 전이면 슬롯이 없다.

## 필요한 환경

- `KIS_APP_KEY`, `KIS_APP_SECRET`. Airflow가 읽는 건 `compose/local/airflow/.env`다.
- `CONNECTION_ID`가 가리키는 Airflow 연결. 접속 정보는 `AIRFLOW_CONN_FINANCE`가 갖는다.

토큰은 다른 KIS DAG와 같은 Airflow Variable 캐시를 공유한다.
"""

import logging
import os
from contextlib import closing
from datetime import UTC, datetime, time, timedelta
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
from modules.collectors.market.kis_investor_flow import (
    InvestorFlowMarket,
    InvestorFlowStock,
    KisInvestorFlowCollector,
)
from modules.market_session import krx_open_day
from modules.utility import CONNECTION_ID, KIS_UNRECOVERABLE_STATUSES, KST_TIMEZONE, atomic

logger = logging.getLogger(__name__)

INCLUDE_ESTIMATES_PARAM = "include_stock_estimates"

# 공식 갱신 시각을 조금 지난 뒤에 부른다. 5분 스케줄이라 이 분들에 run 이 있다.
#
# **시각을 자연키로 쓰지 않는다.** 제공처가 갱신 시각을 바꿔도 슬롯 코드가 값을 가르므로
# 여기 목록은 "언제 부를지"만 정한다. 목록이 낡아도 값이 틀리지는 않고 늦게 들어올 뿐이다.
ESTIMATE_CALL_TIMES: tuple[time, ...] = (
    time(9, 35),
    time(10, 5),
    time(11, 25),
    time(13, 25),
    time(14, 35),
)

# KIS가 이 조회의 첫 집계를 내는 시각. 실측으로 09:05 run 은 값이 있었고 09:00 정각과
# 09:02 재시도는 전부 0이었다(2026-08-19).
FIRST_AGGREGATION_TIME = time(9, 5)


def _credentials() -> tuple[SecretStr, SecretStr]:
    app_key = os.environ.get("KIS_APP_KEY")
    app_secret = os.environ.get("KIS_APP_SECRET")
    if not app_key or not app_secret:
        raise AirflowFailException("KIS_APP_KEY and KIS_APP_SECRET are required")
    return SecretStr(app_key), SecretStr(app_secret)


def _connection() -> Any:
    return PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()


def before_first_aggregation(now_kst: datetime) -> bool:
    """장 시작 직후라 KIS가 첫 집계를 아직 안 냈는지.

    cron 의 마지막 슬롯(전일 15:55)은 data interval 이 다음 날 09:00에 끝나서 매 거래일
    첫 run 이 09:00 정각에 뜬다. 그 시각의 응답은 전부 0이라 all-zero 가드가 시장 코드
    오류로 오탐하므로 이 run 은 skip 한다. 빈 장 스냅샷이라 잃는 값도 없다.
    """
    return now_kst.time() < FIRST_AGGREGATION_TIME


def wants_stock_estimates(now_kst: datetime, params: dict[str, Any]) -> bool:
    """이 run 이 종목 추정을 부를지.

    수동 실행에서 `include_stock_estimates`를 주면 그 값을 따른다. 비어 있으면 갱신 시각
    목록으로 판단한다.
    """
    given = params.get(INCLUDE_ESTIMATES_PARAM)
    if given is not None:
        return bool(given)
    return now_kst.time().replace(second=0, microsecond=0) in ESTIMATE_CALL_TIMES


@dag(
    dag_id="kis_investor_flow_intraday",
    dag_display_name="🧭 외국인·기관·개인 수급 (KIS)",
    description="국내 정규장 동안 시장 수급을 5분마다, 종목 추정 수급을 갱신 시각에 받아 저장한다.",
    # KST 평일 09:00~15:59 = UTC 평일 00:00~06:59. 정규장 안에서만 돈다.
    schedule="*/5 9-15 * * 1-5",
    start_date=pendulum.datetime(2026, 8, 15, tz=KST_TIMEZONE),  # KST 2026-08-15 00:00 = UTC 2026-08-14 15:00
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 1, "retry_delay": timedelta(minutes=2)},
    params={
        INCLUDE_ESTIMATES_PARAM: Param(
            None,
            type=["null", "boolean"],
            title="종목 추정도 조회",
            description="비우면 갱신 시각(09:35, 10:05, 11:25, 13:25, 14:35 KST)에만 부른다.",
        ),
    },
    doc_md=__doc__,
    tags=["kis", "market", "intraday", "korea", "investor"],
)
def kis_investor_flow_intraday():
    @task(task_display_name="수급 수집·저장")
    def collect() -> int:
        context = get_current_context()
        params = dict(context.get("params") or {})

        now = datetime.now(UTC)
        now_kst = now.astimezone(KST_TIMEZONE)

        connection = _connection()
        try:
            closed = krx_open_day(connection, now_kst.date()) is False
        finally:
            connection.close()
        if closed:
            raise AirflowSkipException(f"KRX is closed on {now_kst.date()}")

        if before_first_aggregation(now_kst):
            raise AirflowSkipException("KIS has not published the first aggregation yet")

        app_key, app_secret = _credentials()
        collector = KisInvestorFlowCollector(access_token(Variable, app_key, app_secret), app_key, app_secret)

        # 이 조회에는 원천 시각이 없다. 응답을 받은 분으로 찍는다.
        observed_at = now.replace(second=0, microsecond=0)

        jobs: list[tuple[str, Any, Any]] = [
            (
                f"market:{market.value}",
                lambda market=market: collector.fetch_market_flow(market, observed_at),
                collector.store_market_flow,
            )
            for market in InvestorFlowMarket
        ]
        if wants_stock_estimates(now_kst, params):
            jobs += [
                (
                    f"estimate:{stock.value}",
                    lambda stock=stock: collector.fetch_stock_estimates(stock, now_kst.date()),
                    collector.store_stock_estimates,
                )
                for stock in InvestorFlowStock
            ]
        else:
            logger.info("Skipping stock estimates at %s; not an update slot", now_kst.strftime("%H:%M"))

        stored = 0
        failures: list[str] = []
        with closing(_connection()) as connection:
            for name, call, store in jobs:
                try:
                    fetch = call()
                except KisHTTPError as error:
                    if error.status in KIS_UNRECOVERABLE_STATUSES:
                        raise AirflowFailException(f"{name}: {error}") from error
                    logger.warning("%s failed with HTTP %s", name, error.status)
                    failures.append(f"{name}({error})")
                    continue
                except (KisResultError, KisPayloadError) as error:
                    logger.warning("%s failed: %s", name, error)
                    failures.append(f"{name}({error})")
                    continue
                except ConnectionError as error:
                    logger.warning("%s failed to connect: %s", name, error)
                    failures.append(f"{name}({error})")
                    continue

                with atomic(connection):
                    rows = store(connection, fetch)

                stored += rows
                logger.info("Stored %s rows for %s", rows, name)

        if failures:
            raise AirflowFailException(f"{len(failures)} of {len(jobs)} KIS calls failed: {'; '.join(failures)}")

        logger.info("Stored %s investor flow rows at %s", stored, observed_at.isoformat())
        return stored

    collect()


kis_investor_flow_intraday = kis_investor_flow_intraday()
