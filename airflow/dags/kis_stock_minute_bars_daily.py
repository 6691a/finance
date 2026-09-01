"""종목 1분봉 장 마감 후 수집 DAG.

`quote_bar`에는 지수와 선물만 있었다. 수급·포지션·공시는 전부 종목 단위로 쌓이는데 그
종목의 가격 움직임과 겹칠 시계열이 없었다. 외국인이 파는 5분에 주가가 어떻게 됐는지 화면에서
볼 수 없다는 뜻이다. 이 DAG가 그 칸을 채운다.

수집 규칙은 `modules/collectors/kis.py`의 `KisQuoteCollector.fetch_stock_bars`에 있다.

## 장중이 아니라 마감 후에 받는다

KIS에는 종목 분봉 조회가 둘이다. 장중 조회(`FHKST03010200`)와 일자별 조회(`FHKST03010230`)인데
**장중 조회는 15:30 봉에 마감 동시호가 물량을 두 번 싣는다**(실측: 2,730,280 = 2 × 1,365,140).
하루 합이 누적 거래량을 넘는다. 장중 한복판 봉은 둘이 완전히 같지만, 한 시계열에 두 조회를
섞으면 그날의 마지막 봉만 값이 갈린다.

일자별 조회는 한 번에 120봉이라 정규장 381봉을 네 번에 덮는다. 장중 조회는 30봉이라 열네
번이다. 호출 수도 이쪽이 낫다.

그래서 실시간성을 포기하고 마감 후 확정본을 받는다. 장중 알림이 필요해지면 그때 WebSocket을
붙인다. 장중 REST 조회를 섞는 선택지는 위 이유로 없다.

## 전일종가는 우리 테이블에서 읽는다

`quote_bar.previous_close`가 NOT NULL인데, 분봉 응답의 `output1`은 요청한 날짜와 무관하게
**지금 시세**를 담는다(실측: 2026-07-03을 요청해도 `acml_vol`이 오늘 값이다). 그대로 쓰면
백필한 모든 봉에 오늘의 전일종가가 박힌다.

대신 `stock_investor_trade_daily`에 있는 직전 거래일 종가를 읽는다. **`kis_investor_trade_daily`가
먼저 돌아야 한다.** 값이 없는 거래일은 건너뛴다. 지어낸 분모보다 빈 구간이 낫다.

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `business_date` | `null` | 받을 거래일(YYYY-MM-DD). 비우면 이 run의 날짜(KST) |
| `days` | `1` | 그 날짜부터 과거로 며칠을 받을지. 백필에만 쓴다. 한 run에 최대 31일 |

    airflow dags trigger kis_stock_minute_bars_daily \\
      --conf '{"business_date": "2026-08-14", "days": 5}'

`days`를 늘리면 달력 날짜를 하루씩 뒤로 걷는다. 휴장일은 0봉으로 와서 건너뛴다. 거래일만
세는 계산을 하지 않는 이유는 그 계산이 곧 휴장일 달력의 사본이 되기 때문이다.

**한 run에 31일까지다.** `max_active_runs=1`이라 긴 백필 run이 그날 마감 확정 run을 직접
점유한다. 더 넓은 구간은 run을 나눠 명시적으로 돌린다.

## 실패와 재시도

- **한 종목·한 날짜가 실패해도 나머지는 저장한다.** 날짜 하나가 트랜잭션 하나다.
- HTTP 400/403/404: 설정 오류라 즉시 실패한다.
- **0봉은 휴장일이면 정상이고 개장일이면 실패다.** `market_session.krx_open_day`로 가른다.
  캘린더가 모르면(`None`) 저장소 규칙대로 정상으로 둔다. 전에는 안 묻고 "아마 휴장"으로
  넘겨 개장일의 빈 하루가 초록이었다(2026-08-31 조사 G-35).
- **전일종가가 없어 KIS를 한 번도 못 불렀으면 실패다.** 종목 하나가 빠지는 것은 건너뛰지만
  둘 다 빠지면 호출 0회·`failures` 0건으로 `stored=0` 성공이었다. 하루 한 번 도는 확정
  수집이라 다시 집는 실행이 없다 — 앞단 `kis_investor_trade_daily`가 죽은 날이 그 자리다.

## NXT를 떼는 손잡이

`KIS_ENABLE_NXT_REST=false`면 KRX만 받는다. NXT가 흔들릴 때 코드를 고치지 않고 그쪽만 떼기
위한 것이다(분봉 문서 §3.3·§11.1). **기본은 켜짐이고** 모르는 값은 즉시 실패한다 — 오타가
조용히 켜짐으로 읽히면 손잡이를 당겼다고 믿는 사람과 실제 동작이 갈린다. 판단은
`modules.collectors.kis.rest_exchanges`가 한 벌로 갖는다.

WebSocket 쪽 `KIS_ENABLE_NXT_WEBSOCKET`도 기본값과 허용 값이 같다. 두 손잡이가 다르게
동작하면 한쪽을 끈 사람이 다른 쪽도 껐다고 믿는다. 판정은 트리가 달라 두 벌이고
(`apps/realtime/main.py`), `tests/realtime/`이 그 둘을 대조한다.

## 필요한 환경

- `KIS_APP_KEY`, `KIS_APP_SECRET`. Airflow가 읽는 건 `compose/local/airflow/.env`다.
- `KIS_ENABLE_NXT_REST`(선택). 비우면 KRX·NXT 둘 다 받는다.
- `CONNECTION_ID`가 가리키는 Airflow 연결. 접속 정보는 `AIRFLOW_CONN_FINANCE`가 갖는다.

토큰은 다른 KIS DAG와 같은 Airflow Variable 캐시를 공유한다.
"""

import logging
import os
import re
from datetime import date, timedelta
from typing import Any

import pendulum
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Param, Variable, dag, get_current_context, task
from airflow.sdk.exceptions import AirflowFailException
from pydantic import SecretStr

from modules.collectors.kis import (
    DomesticStock,
    KisHTTPError,
    KisPayloadError,
    KisResultError,
    KisTimeWindowError,
    access_token,
    rest_exchanges,
)
from modules.collectors.market.kis_quote import (
    KisQuoteCollector,
    last_settled_close,
)
from modules.market_session import krx_open_day
from modules.utility import CONNECTION_ID, KIS_UNRECOVERABLE_STATUSES, KST_TIMEZONE

logger = logging.getLogger(__name__)

# 달력 하루만 받는다. ISO 주 표기(2026-W33)와 기본형(20260814)을 걸러 내는 그물이다.
CALENDAR_DAY_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")

BUSINESS_DATE_PARAM = "business_date"
DAYS_PARAM = "days"

# 한 run이 걸을 수 있는 최대 달력 일수. 분봉 문서 §9가 백필 한 run에 정한 상한과 같은 값이다.
# 이 DAG는 `max_active_runs=1`이라 긴 백필 run이 그날 마감 확정 run을 직접 점유한다. 상한이
# 없으면 `days` 오타 하나(3650)가 정규 확정을 며칠 멈춘다. 더 넓은 구간은 run을 나눈다.
MAX_DAYS = 31


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


def _run_date() -> date:
    """이 run이 기대하는 거래일(KST). 기준 시각은 벽시계가 아니라 이 run의 시각이다.

    `kis_overseas_index_daily._session_date`와 같은 규칙이다. `datetime.now`를 쓰면 지난
    run을 clear했을 때 그날이 아니라 오늘 봉을 다시 받고 성공으로 끝난다. `days` 기본값이
    1이라 그 하루를 덮어 줄 창이 없어서, 빠진 날은 빠진 채로 남고 아무도 모른다.
    """
    context = get_current_context()
    reference = context.get("data_interval_end") or context["dag_run"].run_after
    return reference.astimezone(KST_TIMEZONE).date()


def requested_business_date(run_date: date, params: dict[str, Any]) -> date:
    """받을 거래일.

    **모양을 먼저 본다.** `date.fromisoformat`은 `20260814`와 `2026-W33`도 받는다. 주 표기는
    그 주의 월요일이 되어, 운영자가 넣은 값과 다른 날짜를 조용히 받아 온다.
    """
    given = params.get(BUSINESS_DATE_PARAM)
    if not given:
        return run_date
    text = str(given).strip()
    if not CALENDAR_DAY_PATTERN.fullmatch(text):
        raise AirflowFailException(f"{BUSINESS_DATE_PARAM} must be YYYY-MM-DD, got {given!r}")
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise AirflowFailException(f"{BUSINESS_DATE_PARAM} must be YYYY-MM-DD, got {given!r}") from None


def require_attempts(attempted: int, business_date: date, days: int) -> None:
    """KIS를 한 번도 못 불렀으면 실패다. 근거는 모듈 docstring "실패와 재시도"."""
    if attempted == 0:
        raise AirflowFailException(
            f"no previous close for any stock in the {days} day(s) ending {business_date.isoformat()}; "
            "kis_investor_trade_daily has not filled that window"
        )


def no_bars_failure(open_day: bool | None, name: str) -> str | None:
    """0봉의 뜻을 캘린더로 가른다. 개장일이면 실패 사유, 휴장일·모름이면 `None`이다."""
    if open_day is True:
        return f"{name}(no bars on an open day)"
    return None


def requested_days(params: dict[str, Any]) -> int:
    """며칠을 뒤로 걸을지.

    `or 1`로 기본값을 주지 않는다. 0이 falsy라 조용히 1이 되고, 운영자는 아무것도 받지 않기를
    바랐는데 하루치를 받게 된다.

    상한도 여기서 막는다. `Param`의 `maximum`은 UI와 API 트리거만 검사하고, 태스크가 직접
    받은 값(`conf` 없이 넘어온 경우, 다른 코드가 부르는 경우)은 지나간다.
    """
    given = params.get(DAYS_PARAM)
    days = 1 if given is None else int(given)
    if days < 1:
        raise AirflowFailException(f"{DAYS_PARAM} must be at least 1, got {days}")
    if days > MAX_DAYS:
        raise AirflowFailException(f"{DAYS_PARAM} must be at most {MAX_DAYS}, got {days}")
    return days


@dag(
    dag_id="kis_stock_minute_bars_daily",
    dag_display_name="📊 삼성전자·SK하이닉스 1분봉 확정 (KIS)",
    description="장 마감 뒤 삼성전자·SK하이닉스의 1분봉을 KRX·NXT 각각 받아 stock_bar에 저장한다.",
    # KST 평일 20:05 = UTC 평일 11:05. 확정 일별 수급(18:10)이 전일종가를 채운 뒤이고,
    # NXT 애프터마켓(~20:00)까지 끝난 직후다. 20:15 최종 브리핑(slack_kr_market_briefing)이
    # WebSocket 잠정이 아니라 REST 확정(is_final) 봉을 읽도록 그보다 먼저 돈다.
    schedule="5 20 * * 1-5",
    start_date=pendulum.datetime(2026, 8, 15, tz=KST_TIMEZONE),  # KST 2026-08-15 00:00 = UTC 2026-08-14 15:00
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=10)},
    params={
        BUSINESS_DATE_PARAM: Param(
            None,
            type=["null", "string"],
            title="거래일",
            description="YYYY-MM-DD. 비우면 이 run의 날짜(KST).",
        ),
        DAYS_PARAM: Param(
            1,
            type="integer",
            minimum=1,
            maximum=MAX_DAYS,
            title="일수",
            description=(
                f"그 날짜부터 과거로 며칠을 받을지. 휴장일은 0봉으로 와서 건너뛴다. "
                f"한 run에 최대 {MAX_DAYS}일이고, 더 넓은 구간은 run을 나눈다 — "
                f"긴 run 하나가 그날 마감 확정을 점유한다."
            ),
        ),
    },
    doc_md=__doc__,
    tags=["kis", "market", "daily", "korea", "quote"],
)
def kis_stock_minute_bars_daily():
    @task(task_display_name="분봉 수집·저장")
    def collect() -> int:
        context = get_current_context()
        params = dict(context.get("params") or {})

        business_date = requested_business_date(_run_date(), params)
        days = requested_days(params)

        app_key, app_secret = _credentials()
        try:
            exchanges = rest_exchanges()
        except ValueError as error:
            raise AirflowFailException(str(error)) from error
        collector = KisQuoteCollector(access_token(Variable, app_key, app_secret), app_key, app_secret)

        stored = 0
        attempted = 0
        failures: list[str] = []
        for offset in range(days):
            target = business_date - timedelta(days=offset)
            for stock in DomesticStock:
                connection = _connection()
                try:
                    base = last_settled_close(connection, stock.value, target)
                    open_day = krx_open_day(connection, target)
                finally:
                    connection.close()
                if base is None:
                    # 확정 일별 수급이 아직 그 구간을 채우지 않았다. 분모를 지어내지 않는다.
                    logger.warning("%s:%s has no previous close yet; skipping", stock.value, target.isoformat())
                    continue
                attempted += 1

                # 같은 종목을 KRX 한 번, NXT 한 번 받는다. 통합(UN)은 두 거래소 체결이
                # 섞여 쓰지 않는다. NXT 전일 기준가도 KRX 확정 종가다. NXT는
                # `KIS_ENABLE_NXT_REST`로 뗄 수 있다(rest_exchanges).
                for exchange in exchanges:
                    name = f"{stock.value}:{exchange.value}:{target.isoformat()}"

                    try:
                        fetch = collector.fetch_stock_bars(stock, target, base, exchange)
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

                    if not fetch.bars:
                        failure = no_bars_failure(open_day, name)
                        if failure is None:
                            logger.info("%s returned no bars; a closed day", name)
                        else:
                            logger.warning("%s returned no bars on an open day", name)
                            failures.append(failure)
                        continue

                    connection = _connection()
                    try:
                        rows = collector.store_stock_bars(connection, fetch)
                        connection.commit()
                    except Exception:
                        connection.rollback()
                        raise
                    finally:
                        connection.close()

                    stored += rows
                    logger.info("Stored %s bars for %s in %s calls", rows, name, fetch.call_count)

        if failures:
            raise AirflowFailException(f"{len(failures)} KIS calls failed: {'; '.join(failures)}")
        require_attempts(attempted, business_date, days)

        logger.info(
            "Stored %s stock bars ending %s across %s",
            stored,
            business_date,
            ", ".join(exchange.value for exchange in exchanges),
        )
        return stored

    collect()


kis_stock_minute_bars_daily = kis_stock_minute_bars_daily()
