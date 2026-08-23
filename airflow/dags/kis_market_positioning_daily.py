"""신용·증시자금·공매도·대차 일별 수집 DAG.

가격이 "얼마에 거래됐나"라면 이 값들은 **"누가 어떤 포지션으로 들고 있나"**다. 신용융자
잔고가 쌓인 종목은 하락할 때 반대매매가 나오고, 공매도와 대차 잔고는 되사야 할 물량이다.
지수와 분봉만 봐서는 안 보이는 축이다.

다섯 데이터 모두 체결 틱이 아니라 날짜별 집계라 WebSocket을 쓰지 않는다. 장중에 여러 번
불러도 판단력이 생기지 않고 오히려 미완성 당일 값을 확정치처럼 보이게 한다. 그래서 다음
영업일 아침에 한 번만 돈다.

수집 규칙은 `modules/collectors/market/kis_positioning.py`에 있다.

## 다섯 호출이 각자 다른 날짜 규칙을 쓴다

| 데이터 | 단위 | 날짜 입력 | 되돌아보기 |
| --- | --- | --- | --- |
| 신용잔고 일별 | 종목 | **결제일**(거래일 + padding) | 적용 |
| 공매도 | 종목 | 시작·종료일 | 적용 |
| 대차거래 | 종목 | 시작·종료일 | 적용 |
| 대차거래 | **시장**(코스피·코스닥) | 시작·종료일 | 적용 |
| 증시자금 | 시장 전체 | 종료일 하나 | **불필요** — 한 응답이 100영업일이다 |
| 신용잔고 상위 | 전체·코스닥 | 없음 | **불가** — 최신 스냅샷뿐이라 과거를 못 받는다 |

시장 신용융자 잔고는 증시자금 응답 안에 있다. **코스피와 코스닥으로 갈 수 없다**(파라미터를
무엇으로 바꿔도 같은 값이었다). 시장 대차는 갈린다.

## 실패를 어떻게 다루는가

**호출 하나가 트랜잭션 하나다.** 성공한 응답은 즉시 커밋하고 다음 호출로 넘어간다. 그래서
세 번째 API가 죽어도 앞의 둘은 남는다. 루프가 끝난 뒤 실패가 하나라도 있으면 태스크를
실패시켜 재시도한다. 자연키 upsert라 재시도가 중복 행을 만들지 않는다.

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `observation_start` | `null` | 조회 시작 거래일. 주면 `lookback_days`를 무시한다 |
| `observation_end` | `null` | 조회 종료 거래일. 비우면 이 run 시각의 KST 날짜 |
| `lookback_days` | `7` | 구간을 지정하지 않을 때 되돌아볼 일수 |

    airflow dags trigger kis_market_positioning_daily \\
      --conf '{"observation_start": "2026-06-01", "observation_end": "2026-08-12"}'

## 필요한 환경

- `KIS_APP_KEY`, `KIS_APP_SECRET`. Airflow가 읽는 건 `compose/local/airflow/.env`다.
- `CONNECTION_ID`가 가리키는 Airflow 연결. 접속 정보는 `AIRFLOW_CONN_FINANCE`가 갖는다.

토큰은 `kis_quote_intraday`와 같은 Airflow Variable 캐시를 공유한다. 발급 횟수 제한이 있어
DAG마다 따로 받지 않는다.
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

from modules.collectors.kis import (
    KisHTTPError,
    KisPayloadError,
    KisResultError,
    access_token,
)
from modules.collectors.market.kis_positioning import (
    RANKING_UNIVERSES,
    KisPositioningCollector,
    LendingMarket,
    PositioningStock,
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


def _skip_when_closed(today_kst) -> None:
    """확정 휴장일이면 태스크를 건너뛴다.

    행이 없거나 아직 판정하지 않았으면 그대로 수집한다. 화~토 스케줄이 주말은 이미 거르므로
    실효는 평일 공휴일이다.
    """
    connection = _connection()
    try:
        closed = krx_open_day(connection, today_kst) is False
    finally:
        connection.close()
    if closed:
        raise AirflowSkipException(f"KRX is closed on {today_kst}")


@dag(
    dag_id="kis_market_positioning_daily",
    dag_display_name="🧾 신용·공매도·대차·증시자금 (KIS)",
    description="영업일 아침에 신용잔고·신용순위·증시자금·공매도·대차 다섯 지표를 받아 저장한다.",
    schedule="10 8 * * 2-6",  # KST 화~토 08:10 = UTC 월~금 23:10
    start_date=pendulum.datetime(2026, 8, 14, tz=KST_TIMEZONE),  # KST 2026-08-14 00:00 = UTC 2026-08-13 15:00
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(hours=1)},
    params={
        OBSERVATION_START_PARAM: Param(
            None,
            type=["null", "string"],
            format="date",
            title="조회 시작 거래일",
            description="비우면 observation_end에서 lookback_days만큼 뺀 날. 주면 lookback_days를 무시한다.",
        ),
        OBSERVATION_END_PARAM: Param(
            None,
            type=["null", "string"],
            format="date",
            title="조회 종료 거래일",
            description="비우면 이 run 시각의 KST 날짜. 과거 구간을 한 번에 넣을 때 직접 넘긴다.",
        ),
        LOOKBACK_DAYS_PARAM: Param(
            LOOKBACK_DAYS,
            type="integer",
            minimum=1,
            title="되돌아볼 일수",
            description="구간을 지정하지 않을 때만 쓴다. 증시자금과 신용순위는 이 값을 쓰지 않는다.",
        ),
    },
    doc_md=__doc__,
    tags=["kis", "market", "daily", "korea", "positioning"],
)
def kis_market_positioning_daily():
    @task(task_display_name="KRX 포지션 지표")
    def collect_krx() -> int:
        context = get_current_context()
        try:
            observation_start, observation_end = resolve_observation_period(context)
        except PeriodError as error:
            raise AirflowFailException(str(error)) from error

        _skip_when_closed(datetime.now(UTC).astimezone(KST_TIMEZONE).date())

        app_key, app_secret = _credentials()
        collector = KisPositioningCollector(access_token(Variable, app_key, app_secret), app_key, app_secret)

        # 호출 하나가 트랜잭션 하나다. 앞의 성공을 뒤의 실패가 되돌리지 않는다.
        jobs: list[tuple[str, Any, Any]] = [
            (
                "market_funds",
                lambda: collector.fetch_market_funds(observation_end),
                collector.store_market_funds,
            ),
        ]
        # 시장 단위 값. 종목 둘만 보면 놓치는 축이라 함께 받는다.
        jobs += [
            (
                f"credit_ranking:{universe}",
                lambda universe=universe: collector.fetch_credit_ranking(universe),
                collector.store_credit_ranking,
            )
            for universe, _ in RANKING_UNIVERSES
        ]
        jobs += [
            (
                f"market_lending:{market.value}",
                lambda market=market: collector.fetch_market_lending(market, observation_start, observation_end),
                collector.store_market_lending,
            )
            for market in LendingMarket
        ]
        for stock in PositioningStock:
            jobs += [
                (
                    f"credit_balance:{stock.value}",
                    lambda stock=stock: collector.fetch_credit_balance(stock, observation_start, observation_end),
                    collector.store_credit_balance,
                ),
                (
                    f"short_sale:{stock.value}",
                    lambda stock=stock: collector.fetch_short_sale(stock, observation_start, observation_end),
                    collector.store_short_sale,
                ),
                (
                    f"lending:{stock.value}",
                    lambda stock=stock: collector.fetch_lending(stock, observation_start, observation_end),
                    collector.store_lending,
                ),
            ]

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
                    failures.append(name)
                    continue
                except (KisResultError, KisPayloadError) as error:
                    logger.warning("%s failed: %s", name, error)
                    failures.append(name)
                    continue
                except ConnectionError as error:
                    logger.warning("%s failed to connect: %s", name, error)
                    failures.append(name)
                    continue

                with atomic(connection):
                    rows = store(connection, fetch)

                stored += rows
                logger.info("Stored %s rows for %s", rows, name)

        if failures:
            raise AirflowFailException(f"{len(failures)} of {len(jobs)} KIS calls failed: {', '.join(failures)}")

        logger.info("Stored %s positioning rows for %s..%s", stored, observation_start, observation_end)
        return stored

    collect_krx()


kis_market_positioning_daily = kis_market_positioning_daily()
