"""종목 1분봉 장중 조정 DAG.

종목 분봉의 원천은 WebSocket 상주 수집기(`apps/realtime/`)이고 그 경로가 쓰는 봉은
잠정(`is_final=false`)이다. 장중 재연결로 봉을 놓치면 그 구멍이 마감 확정
DAG(`kis_stock_minute_bars_daily`, 20:05)까지 하루 종일 남았다. NXT 애프터마켓(~20:00)은
아예 그 조정 밖이었다.

이 DAG가 장중에 최근 구간만 REST 일자별 조회로 덮어 `is_final=true`로 확정한다.
수집 규칙은 `modules/collectors/kis.py`의 `KisQuoteCollector.fetch_stock_bars`에 있고
마감 확정 DAG와 같은 함수·같은 upsert를 쓴다.

## 왜 별도 DAG인가

지수·선물을 받는 `kis_quote_intraday`에 태스크로 붙일 수도 있지만 **앞단과 실패 성격이
다르다.** 저쪽은 5분마다 새 봉을 만드는 수집이고 이쪽은 이미 있는 봉을 확정하는 백업이다.
주기가 다르면 한 DAG에 둘 수 없고, 한 DAG에 두고 실행 분(minute)으로 갈라 돌리면 모드가
실행자의 의도가 아니라 시계에서 나온다(저장소 규칙 "슬롯·모드로 갈리는 DAG는 나눈다").

## 왜 30분인가

한 응답이 120봉이라 **한 호출이 최근 두 시간을 덮는다.** 주기가 두 시간 안이면 어느 구멍이든
다음 실행이 반드시 메운다. 5분마다 돌면 같은 창을 24번 겹쳐 도는 셈이고 호출만 여섯 배다.
WebSocket이 정상인 동안은 이 조정이 바꿀 것이 없다 — 백업이다.

**틱은 매시 05분과 35분이다.** KRX 마감(15:30) 봉은 15:35 틱이, 그 앞 구간은 그전 틱들이
집는다. 정각에 돌면 15:30 봉이 아직 완결되지 않아 그날 마지막 봉만 잠정으로 남는다.
NXT 마지막 봉(20:00)은 20:05에 도는 확정 DAG의 몫이라 여기서 20시대를 열지 않는다.

## 규칙 둘

- **진행 중인 분은 저장하지 않는다**(`fetch_stock_bars(until=...)`). REST upsert가
  `is_final=true`로 굳히기 때문에 아직 체결이 더 붙을 분을 넣으면 부분 봉이 확정으로 남는다.
- **한 번에 한 호출만 한다**(`max_calls=1`). 두 시간보다 오래된 구멍은 마감 확정 DAG가 메운다.

거래소는 봉이 생기고 있거나 방금 마감한 쪽만 부른다(`active_exchanges`). KRX는 09:00~15:35,
NXT는 08:00~20:05다. NXT는 `KIS_ENABLE_NXT_REST`로 뗄 수 있고, 그 판정은 마감 확정 DAG와
같은 `rest_exchanges()`가 한 벌로 갖는다.

## 전일종가

`stock_bar.previous_close`가 NOT NULL이고 분봉 응답의 `output1`은 요청한 날짜와 무관하게
지금 시세라(실측) 쓸 수 없다. 대신 `stock_investor_trade_daily`의 직전 거래일 종가를 읽는다
(`modules.collectors.kis.last_settled_close`). 값이 없으면 그 종목을 건너뛴다. 지어낸 분모보다
빈 구간이 낫다.

## 실패와 재시도

- **종목·거래소 하나가 실패해도 나머지는 저장한다.** 30분 뒤 같은 태스크가 다시 보므로 한
  번의 실패로 죽이면 경보만 늘고 고쳐지는 것은 없다.
- **전부 실패하면 죽인다.** 그때는 토큰이나 네트워크가 문제라 다음 실행도 같은 자리에서 멈춘다.
- HTTP 400/401/403: 설정 오류라 즉시 실패한다. 401은 토큰을 한 번 재발급하고 다시 시도한다.
- 0봉은 정상이다. 세션이 막 열렸거나 이미 다 확정된 것이다.

## 필요한 환경

- `KIS_APP_KEY`, `KIS_APP_SECRET`. Airflow가 읽는 건 `compose/local/airflow/.env`다.
- `CONNECTION_ID`가 가리키는 Airflow 연결. 접속 정보는 `AIRFLOW_CONN_FINANCE`가 갖는다.

토큰은 다른 KIS DAG와 같은 Airflow Variable 캐시를 공유한다.
"""

import logging
import os
from contextlib import closing
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import pendulum
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Variable, dag, task
from airflow.sdk.exceptions import AirflowFailException, AirflowSkipException
from pydantic import SecretStr

from modules.collectors.kis import (
    DomesticStock,
    KisHTTPError,
    KisPayloadError,
    KisResultError,
    KisTimeWindowError,
    StockExchange,
    access_token,
    rest_exchanges,
)
from modules.collectors.market.kis_quote import (
    KisQuoteCollector,
    StockBarFetch,
    last_settled_close,
)
from modules.market_session import krx_open_day
from modules.utility import CONNECTION_ID, KIS_UNRECOVERABLE_STATUSES, KST_TIMEZONE, atomic

logger = logging.getLogger(__name__)

# 마감 뒤 한 틱을 더 부른다. 마지막 봉(KRX 15:30, NXT 20:00)이 그 틱에서야 완결된다.
# 틱이 매시 05·35분이라 KRX 마감 봉은 15:35 틱이 집는다.
SESSION_GRACE_MINUTES = 5

# 조정 한 번에 허용하는 KIS 호출 수. 한 응답이 120봉이라 최근 두 시간을 덮는다.
RECONCILE_MAX_CALLS = 1


def _credentials() -> tuple[SecretStr, SecretStr]:
    app_key = os.environ.get("KIS_APP_KEY")
    app_secret = os.environ.get("KIS_APP_SECRET")
    if not app_key or not app_secret:
        raise AirflowFailException("KIS_APP_KEY and KIS_APP_SECRET are required")
    return SecretStr(app_key), SecretStr(app_secret)


def _connection() -> Any:
    # 반환 타입은 provider 버전에 따라 psycopg2/psycopg3 래퍼로 갈린다. 런타임 객체는
    # 어느 쪽이든 PEP 249 연결이라 commit·rollback을 갖는다.
    return PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()


def _skip_when_closed(connection: Any, today_kst: date) -> None:
    """확정 휴장일이면 skip.

    행이 없거나 아직 판정하지 않았으면 계속한다. **모르면 수집을 계속한다** — 캘린더 수집이
    실패했다는 이유로 진짜 거래일 데이터를 잃는 것이 빈 요청 몇 번보다 나쁘다.
    """
    if krx_open_day(connection, today_kst) is False:
        raise AirflowSkipException(f"KRX is closed on {today_kst}")


def _collector(app_key: SecretStr, app_secret: SecretStr, force: bool = False) -> KisQuoteCollector:
    return KisQuoteCollector(access_token(Variable, app_key, app_secret, force=force), app_key, app_secret)


def active_exchanges(now_kst: datetime) -> tuple[StockExchange, ...]:
    """지금 봉이 생기고 있고 손잡이가 켜져 있는 거래소.

    휴지 구간에 부르면 KIS 호출만 늘고 새 봉은 없다. 창은 거래소가 스스로 아는 값
    (`first_bar`·`last_bar`)에 마감 유예를 더한 것이다.

    `KIS_ENABLE_NXT_REST`가 꺼져 있으면 NXT는 창 안이어도 빠진다. 마감 확정 DAG와 같은
    손잡이를 같은 함수(`rest_exchanges`)로 읽어, 한쪽만 NXT를 계속 부르는 일이 없게 한다.
    모르는 값이면 `ValueError`가 올라오고 태스크가 그것을 즉시 실패로 바꾼다.
    """
    moment = now_kst.time()
    return tuple(exchange for exchange in rest_exchanges() if exchange.first_bar <= moment <= _grace_end(exchange))


def _grace_end(exchange: StockExchange) -> time:
    return (datetime.combine(date.min, exchange.last_bar) + timedelta(minutes=SESSION_GRACE_MINUTES)).time()


def _fetch(
    collector: KisQuoteCollector,
    app_key: SecretStr,
    app_secret: SecretStr,
    stock: DomesticStock,
    business_date: date,
    previous_close: Decimal,
    exchange: StockExchange,
    now: datetime,
) -> StockBarFetch:
    """종목 봉을 최근 한 호출만 받는다. 401이면 토큰을 한 번만 재발급하고 다시 시도한다.

    `until=now`가 진행 중인 분을 잘라 낸다. 그 규칙은 수집기가 알고 여기서는 기준 시각만 준다.
    """

    def call(active: KisQuoteCollector) -> StockBarFetch:
        return active.fetch_stock_bars(
            stock,
            business_date,
            previous_close,
            exchange,
            until=now,
            max_calls=RECONCILE_MAX_CALLS,
        )

    try:
        return call(collector)
    except KisHTTPError as error:
        if error.status != 401:
            raise
        logger.warning("KIS returned 401; reissuing the token once")
        return call(_collector(app_key, app_secret, force=True))


@dag(
    dag_id="kis_equity_bar_reconcile",
    dag_display_name="🩹 종목 1분봉 장중 조정 (KIS)",
    description="장중 30분마다 삼성전자·SK하이닉스의 최근 1분봉을 REST 확정본으로 덮어 잠정 봉을 메운다.",
    # KST 평일 08:05~19:35의 매시 05·35분 = UTC 평일 23:05~10:35. KRX 마감(15:30) 봉을
    # 15:35 틱이 집도록 정각이 아니라 05분에 건다. NXT 마지막 봉(20:00)은 20:05에 도는
    # kis_stock_minute_bars_daily 의 몫이라 20시대를 열지 않는다.
    schedule="5,35 8-19 * * 1-5",
    start_date=pendulum.datetime(2026, 8, 25, tz=KST_TIMEZONE),  # KST 2026-08-25 00:00 = UTC 2026-08-24 15:00
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 1, "retry_delay": timedelta(minutes=2)},
    doc_md=__doc__,
    tags=["kis", "market", "intraday", "korea", "quote"],
)
def kis_equity_bar_reconcile():
    @task(task_display_name="종목 봉 조정")
    def reconcile() -> int:
        now = datetime.now(UTC)
        now_kst = now.astimezone(KST_TIMEZONE)
        today_kst = now_kst.date()

        try:
            exchanges = active_exchanges(now_kst)
        except ValueError as error:
            raise AirflowFailException(str(error)) from error
        if not exchanges:
            raise AirflowSkipException(f"No exchange session is live at {now_kst:%H:%M} KST")

        app_key, app_secret = _credentials()
        collector = _collector(app_key, app_secret)

        stored = 0
        succeeded: list[str] = []
        failures: list[str] = []
        with closing(_connection()) as connection:
            _skip_when_closed(connection, today_kst)

            for stock in DomesticStock:
                base = last_settled_close(connection, stock.value, today_kst)
                if base is None:
                    # 확정 일별 수급이 아직 직전 거래일을 채우지 않았다. 분모를 지어내지 않는다.
                    logger.warning("%s has no settled previous close yet; skipping", stock.value)
                    continue

                for exchange in exchanges:
                    name = f"{stock.value}:{exchange.value}"
                    try:
                        fetch = _fetch(collector, app_key, app_secret, stock, today_kst, base, exchange, now)
                    except KisHTTPError as error:
                        if error.status in KIS_UNRECOVERABLE_STATUSES:
                            raise AirflowFailException(f"{name}: {error}") from error
                        logger.warning("%s failed with HTTP %s", name, error.status)
                        failures.append(f"{name}({error})")
                        continue
                    except KisTimeWindowError as error:
                        # 제공처가 지금은 이 조회를 받지 않는다(응답 본문이 창을 말해 준다). 재시도는 같은
                        # 답을 받으며 예산만 태우므로 즉시 죽인다. 사람이 시각을 맞춰 다시 트리거한다.
                        raise AirflowFailException(f"{name}: {error}. 제한 시각 뒤에 다시 트리거한다.") from error
                    except (KisResultError, KisPayloadError) as error:
                        logger.warning("%s failed: %s", name, error)
                        failures.append(f"{name}({error})")
                        continue
                    except ConnectionError as error:
                        logger.warning("%s failed to connect: %s", name, error)
                        failures.append(f"{name}({error})")
                        continue

                    succeeded.append(name)
                    if not fetch.bars:
                        # 세션이 막 열렸거나 이미 다 확정됐다. 0봉은 실패가 아니다.
                        continue
                    # 거래소마다 트랜잭션 하나다. 한 저장 실패가 다른 시계열을 되돌리지 않는다.
                    with atomic(connection):
                        stored += collector.store_stock_bars(connection, fetch)

        if failures and not succeeded:
            raise ConnectionError(f"Every reconcile call failed: {'; '.join(failures)}")
        if failures:
            logger.warning(
                "%s of %s reconcile calls failed: %s",
                len(failures),
                len(failures) + len(succeeded),
                "; ".join(failures),
            )

        logger.info("Confirmed %s bars across %s series: %s", stored, len(succeeded), ", ".join(succeeded))
        return stored

    reconcile()


kis_equity_bar_reconcile = kis_equity_bar_reconcile()
