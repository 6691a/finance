"""KIS 해외지수 분봉으로 S&P500·나스닥 종합 현물의 마감 1분봉을 받는 DAG.

`slack_us_market_briefing`(KST 화~토 08:00)이 "미국장 마감" 표에 현물 지수를 실으려면
`quote_bar`에 그 봉이 있어야 한다. 지금까지 S&P500·나스닥은 **선물만**(Yahoo `ES=F`·`NQ=F`)
받았고 현물은 VIX·SOX·러셀2000뿐이었다. 국내에서 받을 수 있는 것은 국내를 우선한다는
원칙대로 KIS 해외지수 API로 현물을 받는다. CME 시세료(월 USD 221.10)는 선물 API 이야기라
여기 해당하지 않는다. 실측과 설계는 `docs/collection/kis-overseas-index-close.md`에 있다.

## 왜 하루 한 번인가

이 API는 날짜 커서 없이 **최근 102봉**만 준다(ET 14:40~16:41쯤). 브리핑이 필요한 것은
마감값 하나라 마감 뒤 한 번이면 된다. 16:01~16:41의 정산 구간 봉까지 저장하므로 마지막
봉이 공식 종가다. 장중 1분봉 전체가 필요해지면 `yahoo_quote_intraday`처럼 폴링 DAG를 따로
붙인다.

```python
SCHEDULE = "30 7 * * 2-6"  # KST 화~토 07:30 = UTC 월~금 22:30
```

미국 정규장 마감은 KST 05:00(서머타임)/06:00이다. `market_calendar_daily`(07:00)가
`market_session`을 갱신한 뒤이고 브리핑(08:00) 앞이다. 화~토인 이유는 브리핑과 같다 —
KST 월요일 아침에는 직전 미국 세션이 없다. 미국 휴장일은 `us_equity_open_day`로 건너뛴다.

## params가 없는 이유

받을 날짜를 고를 수 없는 API다. 대신 수집기가 모든 봉의 `stck_bsop_date`를 이 run이
기대한 세션 날짜(뉴욕 기준)와 대조해 다르면 실패시킨다. 묵은 봉을 오늘 것처럼 저장하는
것보다 멈추는 편이 낫다.

**기대 세션은 벽시계가 아니라 이 run의 `data_interval_end`에서 나온다.** 수동 run처럼
data interval이 없으면 `dag_run.run_after`를 쓴다.

## 실패와 재시도

| 상황 | 처리 |
| --- | --- |
| HTTP 400/403/404 | 설정·주소 오류. 즉시 실패 |
| HTTP 401 | 토큰을 한 번 재발급하고 다시 시도 |
| 그 밖의 HTTP·네트워크 오류 | 그대로 올려 Airflow가 재시도 |
| `rt_cd` 오류, 빈 차트, 묵은 날짜, 다른 코드 응답 | 즉시 실패 |
| 심볼 하나 실패 | **태스크 실패.** 둘뿐이라 항목별 수집 대신 전부 아니면 무(無)다 |

## 필요한 환경

- `KIS_APP_KEY`, `KIS_APP_SECRET`. 토큰 캐시는 `kis_quote_intraday`와 같은 Airflow
  `Variable`(`kis_access_token`)을 공유한다.
- `CONNECTION_ID`가 가리키는 Airflow 연결. 접속 정보는 `AIRFLOW_CONN_FINANCE`가 갖는다.
"""

import logging
import os
from contextlib import closing
from datetime import date, timedelta
from typing import Any

import pendulum
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Variable, dag, get_current_context, task
from airflow.sdk.exceptions import AirflowFailException, AirflowSkipException
from pydantic import SecretStr

from modules.collectors.kis import KisHTTPError, KisPayloadError, KisResultError, access_token
from modules.collectors.market.kis_overseas_index import (
    KisOverseasIndexCollector,
    OverseasIndex,
    OverseasIndexFetch,
    us_session_date,
)
from modules.market_session import us_equity_open_day
from modules.utility import CONNECTION_ID, KIS_UNRECOVERABLE_STATUSES, KST_TIMEZONE, atomic

logger = logging.getLogger(__name__)

SCHEDULE = "30 7 * * 2-6"  # KST 화~토 07:30 = UTC 월~금 22:30


def _credentials() -> tuple[SecretStr, SecretStr]:
    app_key = os.environ.get("KIS_APP_KEY")
    app_secret = os.environ.get("KIS_APP_SECRET")
    if not app_key or not app_secret:
        raise AirflowFailException("KIS_APP_KEY and KIS_APP_SECRET are required")
    return SecretStr(app_key), SecretStr(app_secret)


def _cached_token(app_key: SecretStr, app_secret: SecretStr, force: bool = False) -> SecretStr:
    """`kis_quote_intraday`와 같은 캐시를 쓴다. 저장소를 고르는 일만 여기 있다."""
    return access_token(Variable, app_key, app_secret, force=force)


def _session_date() -> date:
    """이 run이 기대하는 미국 세션 날짜. 기준 시각은 벽시계가 아니라 이 run의 시각이다.

    `data_interval_end`가 없는 수동 run은 `dag_run.run_after`를 쓴다. `datetime.now`를 쓰면
    DAG을 켤 때 Airflow가 만드는 직전 인터벌 run이 그 인터벌 대신 지금 시각으로 세션을 잡아,
    아직 끝나지 않은 세션을 기대하며 죽는다(2026-08-24 관측: 토요일 인터벌 run이 KST 월요일
    오후에 돌면서 월요일 세션을 기대했다).
    """
    context = get_current_context()
    reference = context.get("data_interval_end") or context["dag_run"].run_after
    return us_session_date(reference)


def _connection() -> Any:
    return PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()


def _skip_when_closed(session_date: date) -> None:
    """미국 확정 휴장일이면 건너뛴다. 캘린더가 없으면(`None`) 진행한다 — 묵은 날짜 검사가 잡는다."""
    with closing(_connection()) as connection:
        closed = us_equity_open_day(connection, session_date) is False
    if closed:
        raise AirflowSkipException(f"US equity market was closed on {session_date}")


def _fetch_with_retry(
    collector: KisOverseasIndexCollector,
    index: OverseasIndex,
    session_date: date,
    app_key: SecretStr,
    app_secret: SecretStr,
) -> OverseasIndexFetch:
    """401이면 토큰을 한 번만 재발급하고 다시 시도한다. 되돌릴 수 없는 HTTP 오류는 즉시 실패다.

    토큰은 수집기 객체가 사는 동안 안 변하므로 재발급은 객체를 다시 만드는 것이다.
    자격 증명이 여기 남는 이유는 재발급이 DAG의 일이기 때문이다.
    """
    try:
        return collector.fetch(index, session_date)
    except KisHTTPError as error:
        if error.status in KIS_UNRECOVERABLE_STATUSES:
            raise AirflowFailException(f"{index.value}: {error}") from error
        if error.status != 401:
            raise
        logger.warning("KIS returned 401; reissuing the token once")
        reissued = KisOverseasIndexCollector(_cached_token(app_key, app_secret, force=True), app_key, app_secret)
        return reissued.fetch(index, session_date)


@dag(
    dag_id="kis_overseas_index_close",
    dag_display_name="🇺🇸 미국 지수 마감 1분봉 (KIS)",
    description="미국 정규장 마감 뒤 KIS 해외지수 분봉으로 S&P500·나스닥 종합의 마지막 102봉을 index_bar에 저장한다.",
    schedule=SCHEDULE,
    start_date=pendulum.datetime(2026, 8, 22, tz=KST_TIMEZONE),  # KST 2026-08-22 00:00 = UTC 2026-08-21 15:00
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=5)},
    doc_md=__doc__,
    tags=["kis", "market", "daily", "us"],
)
def kis_overseas_index_close():
    @task(task_display_name="미국 지수 마감 1분봉")
    def collect() -> int:
        session_date = _session_date()
        _skip_when_closed(session_date)

        app_key, app_secret = _credentials()
        collector = KisOverseasIndexCollector(_cached_token(app_key, app_secret), app_key, app_secret)

        # 둘 다 먼저 받는다. 하나라도 실패하면 저장 없이 태스크가 죽는다.
        fetches: list[OverseasIndexFetch] = []
        for index in OverseasIndex:
            try:
                fetches.append(_fetch_with_retry(collector, index, session_date, app_key, app_secret))
            except (KisPayloadError, KisResultError) as error:
                raise AirflowFailException(f"{index.value}: {error}") from error

        with closing(_connection()) as connection, atomic(connection):
            stored = [collector.store(connection, fetch) for fetch in fetches]

        for fetch, count in zip(fetches, stored, strict=True):
            logger.info(
                "Stored %s bars for %s (%s) on %s; latest bar %s",
                count,
                fetch.index.value,
                fetch.name,
                session_date,
                fetch.latest_bar_at.isoformat(),
            )
        return sum(stored)

    collect()


kis_overseas_index_close = kis_overseas_index_close()
