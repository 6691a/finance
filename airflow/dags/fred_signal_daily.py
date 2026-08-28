"""FRED 미국 실질금리·신용스프레드·주간 실업수당 수집 DAG.

`fred_treasury_daily`·`fred_macro_daily`와 같은 수집기를 쓰고 대상만 다르다. 실질금리
(`REAL10Y`), 기대인플레(`BREAKEVEN10Y`), 하이일드 신용스프레드(`HY_OAS`), 주간 신규
실업수당(`INITIAL_CLAIMS_W`) 넷이며 `modules.collectors.indicator.fred.SIGNAL_SERIES`가 정한다.

## 왜 국채 DAG에 합치지 않나

**이름이 거짓이 된다.** 저 DAG는 국채 곡선을 받는 곳이고, 여기 넷 중 국채는 없다.
실질금리와 기대인플레는 물가연동국채 시장이 만드는 값이라 만기가 명목 10년물과 같고,
그래서 `kind`도 `government_bond`가 아니라 `tips_rate`다.

**되돌아볼 구간도 다르다.** 국채는 매일 확정돼 7일이면 충분하지만 주간 실업수당은 다음
주에 정정된다. `LOOKBACK_DAYS_SIGNAL`이 30일이라 최근 네 번의 청구 건수를 매번 다시 받는다.
일별 셋은 그 창 안에서 휴장일만 비므로 늘어나는 비용이 없다.

## 왜 거시 DAG에도 안 넣나

저쪽은 월간이고 190일을 되돌아본다. 일별 계열을 그 창으로 물으면 계열당 130행 남짓을
매일 다시 받는다. 값은 같고 요청만 커진다.

근원 CPI·PCE는 월간이라 그쪽(`fred_macro_daily`)이 받는다. 여기 넷과 발표 주기가 다르다.

## 실패와 재시도

계열마다 태스크를 매핑한다(`.expand`). 실패가 곧 그 태스크의 실패라 따로 판정할 것이 없고
재시도도 실패한 계열만 다시 돈다. `fred_treasury_daily`와 같은 구조다.

## params

`fred_treasury_daily`와 같다. `observation_start`/`observation_end`로 구간을 직접 주거나
`lookback_days`로 되돌아볼 일수를 바꾼다.

    airflow dags trigger fred_signal_daily \\
      --conf '{"observation_start": "2005-01-01", "observation_end": "2026-08-27"}'

## 필요한 환경

- `FRED_API_KEY`. URL 질의 문자열에 들어가므로 예외 메시지와 로그에 URL을 넣지 않는다.
- `CONNECTION_ID`가 가리키는 Airflow 연결. 접속 정보는 `AIRFLOW_CONN_FINANCE`가 갖는다.
"""

import logging
import os
from contextlib import closing
from datetime import timedelta

import pendulum
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Param, dag, get_current_context, task
from airflow.sdk.exceptions import AirflowFailException
from pydantic import SecretStr

from modules.collectors.indicator.fred import (
    SIGNAL_SERIES,
    FredCollector,
    FredHTTPError,
    FredPayloadError,
    FredRequest,
)
from modules.period import (
    LOOKBACK_DAYS_PARAM,
    OBSERVATION_END_PARAM,
    OBSERVATION_START_PARAM,
    PeriodError,
    resolve_observation_period,
)
from modules.utility import CONNECTION_ID, KST_TIMEZONE, UNRECOVERABLE_STATUSES, atomic

logger = logging.getLogger(__name__)

# 주간 신규 실업수당은 다음 주에 정정된다. 30일이면 최근 네 번의 발표를 다시 받는다.
LOOKBACK_DAYS_SIGNAL = 30


@dag(
    dag_id="fred_signal_daily",
    dag_display_name="🇺🇸 미국 실질금리·신용스프레드·실업수당 (FRED)",
    description="매일 FRED에서 미국 실질금리·기대인플레·하이일드 스프레드·주간 신규 실업수당을 받아 저장한다.",
    # KST 화~토 07:50 = UTC 월~금 22:50. 국채(07:30)·거시(07:40) 수집 뒤라 겹치지 않는다.
    # 미국 지표는 미국 영업일에 발표되므로 주말 트리거는 값이 없다.
    schedule="50 7 * * 2-6",
    start_date=pendulum.datetime(2026, 8, 28, tz=KST_TIMEZONE),  # KST 2026-08-28 00:00 = UTC 2026-08-27 15:00
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(hours=1)},
    params={
        OBSERVATION_START_PARAM: Param(
            None,
            type=["null", "string"],
            format="date",
            title="조회 시작일",
            description="비우면 observation_end에서 lookback_days만큼 앞으로 잡는다.",
        ),
        OBSERVATION_END_PARAM: Param(
            None,
            type=["null", "string"],
            format="date",
            title="조회 종료일",
            description="비우면 이 run의 data_interval_end를 KST 날짜로 바꿔 쓴다.",
        ),
        LOOKBACK_DAYS_PARAM: Param(
            LOOKBACK_DAYS_SIGNAL,
            type="integer",
            minimum=1,
            title="되돌아볼 일수",
            description="주간 실업수당의 정정을 흡수한다. 짧게 잡으면 정정된 값을 못 받는다.",
        ),
    },
    doc_md=__doc__,
    tags=["fred", "macro", "daily"],
)
def fred_signal_daily():
    @task(task_display_name="지표 수집·저장")
    def collect(series_id: str) -> int:
        """시계열 하나를 받아 저장한다.

        시계열마다 태스크를 매핑해 한 계열이 실패해도 나머지가 저장되게 한다. 재시도도 실패한
        계열만 다시 호출한다. `fred_treasury_daily`와 같은 구조다.
        """
        context = get_current_context()
        try:
            observation_start, observation_end = resolve_observation_period(context, LOOKBACK_DAYS_SIGNAL)
        except PeriodError as error:
            raise AirflowFailException(str(error)) from error

        request = FredRequest(
            series_id=series_id,
            observation_start=observation_start,
            observation_end=observation_end,
        )

        api_key = os.environ.get("FRED_API_KEY")
        if not api_key:
            raise AirflowFailException("FRED_API_KEY is required")
        collector = FredCollector(SecretStr(api_key))

        try:
            response = collector.fetch_series(request)
        except FredHTTPError as error:
            if error.status in UNRECOVERABLE_STATUSES:
                raise AirflowFailException(str(error)) from error
            if error.retry_after is not None:
                logger.warning("FRED asked to retry after %s seconds", error.retry_after)
            raise

        with closing(PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()) as connection:
            try:
                with atomic(connection):
                    count = collector.store_observations(connection, response)
            except FredPayloadError as error:
                raise AirflowFailException(str(error)) from error

        logger.info(
            "Stored %s %s observations for %s..%s",
            count,
            request.series_id,
            request.observation_start,
            request.observation_end,
        )
        return count

    collect.expand(series_id=list(SIGNAL_SERIES))


fred_signal_daily = fred_signal_daily()
