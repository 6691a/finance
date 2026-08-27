"""국내·미국 시장 영업일 캘린더 수집 DAG.

장중 수집기가 "오늘 이 시장이 열었나"를 물을 곳을 채운다. 결과는 `market_session` 테이블에
쌓이고 `kis_quote_intraday`와 `yahoo_quote_intraday`가 읽는다.

## 누가 무엇의 주인인가

| 시장 | 개장 판정 | 결제일 |
| --- | --- | --- |
| `KRX` | KIS 국내휴장일조회 | KIS 국내휴장일조회 |
| `US_EQUITY` | **NYSE 공식 캘린더** | KIS 해외결제일자조회 |

미국 판정을 KIS에 맡기지 않는 이유는 실측 때문이다. 해외결제일자조회는 **휴장한 나라의 행을
아예 주지 않고**(2026-07-03 미국 대체휴장에 US 행 0개), **미래 날짜에는 0행**으로 답한다.
그래서 그것만으로는 미국 휴장일 행이 영원히 생기지 않고 오늘 이후를 미리 알 수도 없다.
NYSE 페이지는 3년치를 미리 고시한다.

## 태스크

- `domestic_holiday` — 독립 실행. 오늘부터 앞으로 1년치 KRX 거래일을 저장한다.
- `nyse_calendar` → `overseas_settlement` — NYSE가 미국 행을 먼저 만들어야 결제일이 붙는다.

국내와 미국 경로는 서로 롤백하지 않는다. 한쪽이 실패해도 다른 쪽의 저장은 남는다.

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `base_date` | `null` | 국내 조회 기준일(YYYY-MM-DD). 비우면 이 run의 KST 날짜 |
| `trade_date` | `null` | 해외 조회 기준일(YYYY-MM-DD). 비우면 이 run의 KST 날짜 |

해외는 하루치만 오고 미래를 주지 않으므로 **과거를 채우려면 `trade_date`로 하루씩 돌린다.**
과거 날짜는 정상 조회된다(실측 2026-05-14).

    airflow dags trigger market_calendar_daily \\
      --conf '{"trade_date": "2026-05-14"}'

## KIS 호출 제한

국내휴장일조회는 KIS가 **하루 한 번**을 권고한다. 이 DAG 밖에서 부르지 않는다. 수동
재실행은 운영자가 그 제한을 확인한 뒤 한다. 연속조회 사이에는 수집기가 지연을 둔다.

## 실패와 재시도

- HTTP 400/403/404: 설정 오류라 즉시 실패한다. 401은 토큰 만료일 수 있으므로 한 번
  재발급하고 다시 시도한다.
- 그 밖의 HTTP·네트워크 오류: 그대로 올려 재시도한다.
- 본문 `rt_cd` 오류, 연속조회 커서 정지, 알 수 없는 `Y`/`N`: 즉시 실패한다.
- **페이지 상한 도달은 실패가 아니다.** 국내 조회는 미래를 끝없이 주므로 상한이 정지 조건이다.
- NYSE 표 계약이 어긋나면 즉시 실패한다. 이때 기존 판정은 그대로 남는다.
- **해외 응답 0행은 실패가 아니다.** 주말·미래·값 없음이 모두 0행이라 가를 수 없다.

## 필요한 환경

- `KIS_APP_KEY`, `KIS_APP_SECRET`. Airflow가 읽는 건 `compose/local/airflow/.env`다.
  `config.yaml`의 `kis_app_key`/`kis_app_secret`과 같아야 한다. 어긋나면 토큰 발급이
  HTTP 403 `EGW00103`으로 떨어진다.
- `CONNECTION_ID`가 가리키는 Airflow 연결. 접속 정보는 `AIRFLOW_CONN_FINANCE`가 갖는다.

토큰은 `kis_quote_intraday`와 **같은 Airflow Variable 캐시를 공유한다.** 발급 횟수 제한이
있어 DAG마다 따로 받지 않는다.
"""

import logging
import os
from collections.abc import Callable
from contextlib import closing
from datetime import date, timedelta
from typing import Any

import pendulum
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Param, Variable, dag, get_current_context, task
from airflow.sdk.exceptions import AirflowFailException
from pydantic import SecretStr

from modules.collectors.calendar.kis_market_calendar import (
    KisCursorError,
    KisMarketCalendarCollector,
    SettlementTargetMissing,
)
from modules.collectors.calendar.nyse_calendar import (
    NyseParseError,
    fetch_calendar,
    parse_calendar,
    store_calendar,
)
from modules.collectors.kis import (
    KisHTTPError,
    KisPayloadError,
    KisResultError,
    access_token,
)
from modules.utility import CONNECTION_ID, KIS_UNRECOVERABLE_STATUSES, KST_TIMEZONE, atomic

logger = logging.getLogger(__name__)

BASE_DATE_PARAM = "base_date"
TRADE_DATE_PARAM = "trade_date"


def _credentials() -> tuple[SecretStr, SecretStr]:
    app_key = os.environ.get("KIS_APP_KEY")
    app_secret = os.environ.get("KIS_APP_SECRET")
    if not app_key or not app_secret:
        raise AirflowFailException("KIS_APP_KEY and KIS_APP_SECRET are required")
    return SecretStr(app_key), SecretStr(app_secret)


def _cached_token(app_key: SecretStr, app_secret: SecretStr, force: bool = False) -> SecretStr:
    """`kis_quote_intraday`와 같은 캐시를 쓴다. 저장소를 고르는 일만 여기 있다."""
    return access_token(Variable, app_key, app_secret, force=force)


def _requested_date(parameter: str) -> date:
    """`params`의 날짜. 비어 있으면 이 run 시각의 KST 날짜다."""
    context = get_current_context()
    params = dict(context.get("params") or {})
    given = params.get(parameter)
    if given:
        try:
            return date.fromisoformat(str(given))
        except ValueError as error:
            raise AirflowFailException(f"{parameter} must be YYYY-MM-DD: {given!r}") from error

    reference = context.get("data_interval_end") or context["dag_run"].run_after
    return reference.astimezone(KST_TIMEZONE).date()


def _fetch_with_retry(call, collector: KisMarketCalendarCollector, app_key: SecretStr, app_secret: SecretStr):
    """401이면 토큰을 한 번만 재발급하고 다시 시도한다.

    토큰은 수집기 객체가 사는 동안 안 변하므로 재발급은 객체를 다시 만드는 것이다.
    """
    try:
        return call(collector)
    except KisHTTPError as error:
        if error.status in KIS_UNRECOVERABLE_STATUSES:
            raise AirflowFailException(str(error)) from error
        if error.status != 401:
            raise
        logger.warning("KIS returned 401; reissuing the token once")
        return call(_collector(app_key, app_secret, force=True))


def _collector(app_key: SecretStr, app_secret: SecretStr, force: bool = False) -> KisMarketCalendarCollector:
    return KisMarketCalendarCollector(_cached_token(app_key, app_secret, force=force), app_key, app_secret)


def _store[Stored](store: Callable[..., Stored], *arguments: Any) -> Stored:
    """저장 한 번을 한 트랜잭션으로 감싼다.

    위임 대상 셋의 반환이 갈린다 — `store_domestic`·`store_calendar`는 저장 건수(`int`)이고
    `store_overseas`는 `UsSettlement | None`이다. 그래서 구체 타입이 아니라 `TypeVar`다.
    """
    with closing(PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()) as connection:
        try:
            with atomic(connection):
                return store(connection, *arguments)
        except (KisPayloadError, KisResultError, NyseParseError, SettlementTargetMissing) as error:
            raise AirflowFailException(str(error)) from error


@dag(
    dag_id="market_calendar_daily",
    dag_display_name="🗓 국내·미국 영업일 캘린더",
    description="KIS 국내휴장일과 NYSE 캘린더로 시장별 개장 여부를 채운다.",
    schedule="0 7 * * *",  # KST 매일 07:00 = UTC 전날 22:00
    start_date=pendulum.datetime(2026, 8, 13, tz=KST_TIMEZONE),  # KST 2026-08-13 00:00 = UTC 2026-08-12 15:00
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=30)},
    params={
        BASE_DATE_PARAM: Param(
            None,
            type=["null", "string"],
            format="date",
            title="국내 조회 기준일",
            description="비우면 이 run 시각의 KST 날짜. 이 날부터 앞으로의 거래일이 온다.",
        ),
        TRADE_DATE_PARAM: Param(
            None,
            type=["null", "string"],
            format="date",
            title="해외 조회 기준일",
            description="비우면 이 run 시각의 KST 날짜. 미래는 조회되지 않으므로 과거를 채울 때만 넘긴다.",
        ),
    },
    doc_md=__doc__,
    tags=["kis", "nyse", "market", "calendar", "daily"],
)
def market_calendar_daily():
    @task(task_display_name="국내 휴장일")
    def domestic_holiday() -> int:
        base_date = _requested_date(BASE_DATE_PARAM)
        app_key, app_secret = _credentials()
        collector = _collector(app_key, app_secret)

        try:
            fetch = _fetch_with_retry(
                lambda active: active.fetch_domestic_calendar(base_date), collector, app_key, app_secret
            )
        except (KisCursorError, KisResultError) as error:
            raise AirflowFailException(str(error)) from error

        count = _store(collector.store_domestic, fetch)
        logger.info(
            "Stored %s KRX session days from %s over %s page(s)",
            count,
            base_date,
            fetch.page_count,
        )
        return count

    @task(task_display_name="NYSE 캘린더")
    def nyse_calendar() -> int:
        fetch = fetch_calendar()
        try:
            calendar = parse_calendar(fetch.html)
        except NyseParseError as error:
            raise AirflowFailException(str(error)) from error

        count = _store(store_calendar, fetch, calendar)
        logger.info("Stored %s US_EQUITY session days for %s", count, list(calendar.years))
        return count

    @task(task_display_name="해외 결제일")
    def overseas_settlement() -> int:
        trade_date = _requested_date(TRADE_DATE_PARAM)
        app_key, app_secret = _credentials()
        collector = _collector(app_key, app_secret)

        try:
            fetch = _fetch_with_retry(
                lambda active: active.fetch_overseas_settlement(trade_date), collector, app_key, app_secret
            )
        except (KisCursorError, KisResultError) as error:
            raise AirflowFailException(str(error)) from error

        settlement = _store(collector.store_overseas, fetch)
        if settlement is None:
            # 주말·미래·미국 휴장이 모두 여기 온다. 판정은 NYSE가 이미 갖고 있다.
            logger.info("No US settlement row for %s; left the NYSE verdict alone", trade_date)
            return 0
        logger.info(
            "US settlement for %s: local %s, domestic %s (%s markets agreed)",
            trade_date,
            settlement.local_settlement_date,
            settlement.domestic_settlement_date,
            settlement.market_count,
        )
        return 1

    domestic_holiday()
    nyse_calendar() >> overseas_settlement()


market_calendar_daily = market_calendar_daily()
