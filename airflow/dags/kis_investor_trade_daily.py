"""종목별 투자자 매매동향 확정 일별값 수집 DAG.

`kis_investor_flow_intraday`가 장중 **추정치**를 받는다면 이 DAG는 장 마감 뒤의 **확정값**을
받는다. 추정은 하루 다섯 회차뿐이고 개인이 없지만, 확정값은 12개 분류가 전부 있고 외국인이
등록·미등록으로 갈리며 대금 단위까지 확정돼 있다(백만원).

수집 규칙은 `modules/collectors/market/kis_investor_flow.py`에 있다.

## 한 번 부르면 30 거래일이 온다

`FID_INPUT_DATE_1`은 구간의 **끝**이고 응답은 그날부터 과거로 30 거래일을 담는다(실측).
그래서 하루치를 위해 부르는 호출이 이미 지난 달까지 채운다. 매일 도는 것만으로 30 거래일이
겹쳐 들어와 실패한 날이 저절로 메워진다.

## 백필은 날짜를 뒤로 건다

`end_date`를 주면 그날을 끝으로 하는 구간을 받는다. `pages`를 함께 주면 30 거래일씩 더 과거로
걸으며 그만큼 더 받는다. 달력이 아니라 응답이 준 가장 이른 거래일의 하루 전을 다음 끝 날짜로
쓴다. 우리가 거래일을 세면 휴장일에서 어긋난다.

    airflow dags trigger kis_investor_trade_daily \\
      --conf '{"end_date": "2026-07-01", "pages": 6}'

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `end_date` | `null` | 구간의 끝(YYYY-MM-DD). 비우면 실행일(KST) |
| `pages` | `1` | 30 거래일씩 몇 구간을 뒤로 걸을지 |

## 실패와 재시도

- **한 종목이 실패해도 다른 종목은 저장한다.** 호출 하나가 트랜잭션 하나다.
- HTTP 400/403/404: 설정 오류라 즉시 실패한다.
- 응답의 네 항등식이 깨지면 저장하지 않는다. 필드 뜻이 바뀐 것이다.
- 0행은 정상이다. 상장 전 구간을 요청하면 비어 있다.

## 필요한 환경

- `KIS_APP_KEY`, `KIS_APP_SECRET`. Airflow가 읽는 건 `compose/local/airflow/.env`다.
- `CONNECTION_ID`가 가리키는 Airflow 연결. 접속 정보는 `AIRFLOW_CONN_FINANCE`가 갖는다.

토큰은 다른 KIS DAG와 같은 Airflow Variable 캐시를 공유한다.
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
from modules.collectors.market.kis_investor_flow import (
    InvestorFlowStock,
    KisInvestorFlowCollector,
)
from modules.market_session import krx_open_day
from modules.utility import CONNECTION_ID, KIS_UNRECOVERABLE_STATUSES, KST_TIMEZONE, atomic

logger = logging.getLogger(__name__)

# 달력 하루만 받는다. ISO 주 표기(2026-W32)와 기본형(20260701)을 걸러 내는 그물이다.
CALENDAR_DAY_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")

END_DATE_PARAM = "end_date"
PAGES_PARAM = "pages"


def _credentials() -> tuple[SecretStr, SecretStr]:
    app_key = os.environ.get("KIS_APP_KEY")
    app_secret = os.environ.get("KIS_APP_SECRET")
    if not app_key or not app_secret:
        raise AirflowFailException("KIS_APP_KEY and KIS_APP_SECRET are required")
    return SecretStr(app_key), SecretStr(app_secret)


def _connection() -> Any:
    return PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()


def requested_end_date(now_kst: datetime, params: dict[str, Any]) -> date:
    """이 run 이 구간의 끝으로 쓸 날짜.

    **모양을 먼저 본다.** `date.fromisoformat`은 `20260701`과 `2026-W32`도 받는다. 주 표기는
    그 주의 월요일이 되어, 운영자가 넣은 값과 다른 구간을 조용히 받아 온다.
    """
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


def requested_pages(params: dict[str, Any]) -> int:
    """몇 구간을 뒤로 걸을지.

    `or 1`로 기본값을 주지 않는다. 0이 falsy라 조용히 1이 되고, 운영자는 아무것도 받지 않기를
    바랐는데 하루치를 받게 된다.
    """
    given = params.get(PAGES_PARAM)
    pages = 1 if given is None else int(given)
    if pages < 1:
        raise AirflowFailException(f"{PAGES_PARAM} must be at least 1, got {pages}")
    return pages


@dag(
    dag_id="kis_investor_trade_daily",
    dag_display_name="🧾 종목 투자자 매매동향 확정 (KIS)",
    description="장 마감 뒤 종목별 투자자 매매동향 확정값을 30 거래일씩 받아 저장한다.",
    # KST 평일 18:10 = UTC 평일 09:10. 정규장과 시간외를 모두 지난 뒤다.
    schedule="10 18 * * 1-5",
    start_date=pendulum.datetime(2026, 8, 15, tz=KST_TIMEZONE),  # KST 2026-08-15 00:00 = UTC 2026-08-14 15:00
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=10)},
    params={
        END_DATE_PARAM: Param(
            None,
            type=["null", "string"],
            title="구간의 끝",
            description="YYYY-MM-DD. 비우면 실행일(KST). 이 날짜부터 과거로 30 거래일이 온다.",
        ),
        PAGES_PARAM: Param(
            1,
            type="integer",
            minimum=1,
            title="구간 수",
            description="30 거래일씩 몇 구간을 뒤로 걸을지. 백필에만 쓴다.",
        ),
    },
    doc_md=__doc__,
    tags=["kis", "market", "daily", "korea", "investor"],
)
def kis_investor_trade_daily():
    @task(task_display_name="확정 수급 수집·저장")
    def collect() -> int:
        context = get_current_context()
        params = dict(context.get("params") or {})

        now_kst = datetime.now(UTC).astimezone(KST_TIMEZONE)
        end_date = requested_end_date(now_kst, params)
        pages = requested_pages(params)

        # 자동 실행만 휴장일을 건너뛴다. 백필은 끝 날짜가 휴장일이어도 그 앞 거래일들이
        # 응답에 담겨 오므로 막을 이유가 없다.
        if not params.get(END_DATE_PARAM):
            connection = _connection()
            try:
                closed = krx_open_day(connection, end_date) is False
            finally:
                connection.close()
            if closed:
                raise AirflowSkipException(f"KRX is closed on {end_date}")

        app_key, app_secret = _credentials()
        collector = KisInvestorFlowCollector(access_token(Variable, app_key, app_secret), app_key, app_secret)

        stored = 0
        failures: list[str] = []
        with closing(_connection()) as connection:
            for stock in InvestorFlowStock:
                cursor_date = end_date
                for page in range(pages):
                    name = f"{stock.value}:{cursor_date.isoformat()}"
                    try:
                        fetch = collector.fetch_stock_trade_daily(stock, cursor_date)
                    except KisHTTPError as error:
                        if error.status in KIS_UNRECOVERABLE_STATUSES:
                            raise AirflowFailException(f"{name}: {error}") from error
                        logger.warning("%s failed with HTTP %s", name, error.status)
                        failures.append(name)
                        break
                    except (KisResultError, KisPayloadError) as error:
                        logger.warning("%s failed: %s", name, error)
                        failures.append(name)
                        break
                    except ConnectionError as error:
                        logger.warning("%s failed to connect: %s", name, error)
                        failures.append(name)
                        break

                    if not fetch.rows:
                        logger.info("%s returned no rows; stopping this stock", name)
                        break

                    with atomic(connection):
                        rows = collector.store_stock_trade_daily(connection, fetch)

                    stored += rows
                    logger.info("Stored %s rows for %s", rows, name)

                    # 다음 구간의 끝은 이번 응답의 가장 이른 거래일 하루 전이다. 우리가 거래일을
                    # 세면 휴장일에서 어긋난다.
                    cursor_date = min(row.business_date for row in fetch.rows) - timedelta(days=1)
                    if page + 1 < pages:
                        logger.info("Walking back to %s for %s", cursor_date, stock.value)

        if failures:
            raise AirflowFailException(f"{len(failures)} KIS calls failed: {', '.join(failures)}")

        logger.info("Stored %s daily investor trade rows ending %s", stored, end_date)
        return stored

    collect()


kis_investor_trade_daily = kis_investor_trade_daily()
