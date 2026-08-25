"""시장별 외국인·기관·개인 수급 장중 수집 DAG.

가격이 "얼마에 거래됐나"이고 포지션이 "누가 들고 있나"라면, 수급은 **"지금 누가 사고 누가
파나"**다. 지수가 오르는데 외국인이 팔고 개인이 받는 장과, 외국인이 사는 장은 다음 날이
다르다.

수집 규칙은 `modules/collectors/market/kis_investor_flow.py`에 있다.

## 5분마다 받는 이유

이 조회는 **그날 장 시작부터 지금까지의 누적**을 준다. 원천 시각이 없어서 응답을 받은 분을
`observed_at`으로 찍는다. 5분마다 찍어 두면 나중에 `lag()`로 빼서 "10:00~10:05 사이에
외국인이 얼마 샀나"를 복원할 수 있다. **소급 수집이 안 되는 값이라** 그때 안 찍으면 영영
없다. 지금은 브리핑과 추론 툴이 세션의 마지막 스냅샷만 읽지만, 주기를 늘리면 장중 흐름을
되살릴 길이 닫힌다.

종목 추정 수급은 여기 없다. KIS가 하루 다섯 번만 집계해서 조회 성격도 실패 판정도 달라
`kis_investor_estimate_intraday`로 갈랐다(2026-08-25). 그쪽 docstring 에 이유가 있다.

## 시장 코드는 문서에 적힌 것만 쓴다

잘못된 시장 코드는 오류가 아니라 값 0으로 온다(실측). 그래서 수집기 Enum에는 공식 postman
컬렉션에 적힌 코드만 있고, **모든 값이 0인 응답은 실패로 다룬다.** 코드표와 근거는
`modules/collectors/market/kis_investor_flow.py`의 "시장 코드는 문서에 다 있다" 절에 있다.

일곱 시장을 받는다. 코스피·코스닥 현물 둘에 선물·콜옵션·풋옵션·주식선물·ETF다. 응답 모양이
일곱 다 같아서 여기서는 대상이 늘 뿐 분기가 없다. **파생은 수량이 주가 아니라 계약이다** —
저장은 표기 그대로 하고 섞지 않는 책임은 조회하는 쪽에 있다.

## 실패와 재시도

- **한 시장이 실패해도 다른 시장은 저장한다.** 호출 하나가 트랜잭션 하나다.
- HTTP 400/403/404: 설정 오류라 즉시 실패한다.
- 응답이 전부 0이면 실패시킨다. 코드가 틀렸다는 뜻이다. 단 09:05 이전 run 은 skip 한다.
  장 시작 직후에는 옳은 코드도 첫 집계 전이라 전부 0으로 온다.

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
from airflow.sdk import Variable, dag, task
from pydantic import SecretStr

from modules.collectors.kis import (
    KisHTTPError,
    KisPayloadError,
    KisResultError,
    KisTimeWindowError,
    access_token,
)
from modules.collectors.market.kis_investor_flow import (
    InvestorFlowMarket,
    KisInvestorFlowCollector,
)
from modules.market_session import krx_open_day
from modules.utility import CONNECTION_ID, KIS_UNRECOVERABLE_STATUSES, KST_TIMEZONE, atomic

logger = logging.getLogger(__name__)

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


@dag(
    dag_id="kis_investor_flow_intraday",
    dag_display_name="🧭 시장 수급 (KIS)",
    description="국내 정규장 동안 일곱 시장의 외국인·기관·개인 누적 수급을 5분마다 받아 저장한다.",
    # KST 평일 09:00~15:59 = UTC 평일 00:00~06:59. 정규장 안에서만 돈다.
    schedule="*/5 9-15 * * 1-5",
    start_date=pendulum.datetime(2026, 8, 15, tz=KST_TIMEZONE),  # KST 2026-08-15 00:00 = UTC 2026-08-14 15:00
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 1, "retry_delay": timedelta(minutes=2)},
    doc_md=__doc__,
    tags=["kis", "market", "intraday", "korea", "investor"],
)
def kis_investor_flow_intraday():
    @task(task_display_name="시장 수급 수집·저장")
    def collect() -> int:
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

        stored = 0
        failures: list[str] = []
        with closing(_connection()) as connection:
            for market in InvestorFlowMarket:
                try:
                    fetch = collector.fetch_market_flow(market, observed_at)
                except KisHTTPError as error:
                    if error.status in KIS_UNRECOVERABLE_STATUSES:
                        raise AirflowFailException(f"{market.value}: {error}") from error
                    logger.warning("%s failed with HTTP %s", market.value, error.status)
                    failures.append(f"{market.value}({error})")
                    continue
                except KisTimeWindowError as error:
                    # 제공처가 지금은 이 조회를 받지 않는다(응답 본문이 창을 말해 준다). 재시도는 같은
                    # 답을 받으며 예산만 태우므로 즉시 죽인다. 사람이 시각을 맞춰 다시 트리거한다.
                    raise AirflowFailException(f"{market.value}: {error}. 제한 시각 뒤에 다시 트리거한다.") from error
                except (KisResultError, KisPayloadError) as error:
                    logger.warning("%s failed: %s", market.value, error)
                    failures.append(f"{market.value}({error})")
                    continue
                except ConnectionError as error:
                    logger.warning("%s failed to connect: %s", market.value, error)
                    failures.append(f"{market.value}({error})")
                    continue

                with atomic(connection):
                    rows = collector.store_market_flow(connection, fetch)

                stored += rows
                logger.info("Stored %s rows for %s", rows, market.value)

        if failures:
            raise AirflowFailException(
                f"{len(failures)} of {len(InvestorFlowMarket)} KIS calls failed: {'; '.join(failures)}"
            )

        logger.info("Stored %s investor flow rows at %s", stored, observed_at.isoformat())
        return stored

    collect()


kis_investor_flow_intraday = kis_investor_flow_intraday()
