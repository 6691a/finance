"""FRED 미국 월간 거시지표 수집 DAG.

`fred_treasury_daily`와 같은 수집기를 쓰고 대상만 다르다. 소비자물가지수(헤드라인·근원),
생산자물가지수, 근원 PCE 물가지수, 소매판매, 실업률, 비농업고용, 주간 신규 실업수당 여덟이며
`modules.collectors.indicator.fred.MACRO_SERIES`(`kind`가 `price_index`·`activity`인 계열)가 정한다.

**주간 계열 하나가 섞여 있다.** 신규 실업수당(`INITIAL_CLAIMS_W`)은 주간이지만 재는 것이
실물활동이라 여기 실린다. 어느 DAG이 받을지는 발표 주기가 아니라 `kind`가 정한다 — 주기로
가르면 계열이 늘 때마다 그 필터가 거짓이 된다. 190일 창으로 물으면 27행쯤 오고, 다음 주에
정정되는 값이 그 안에서 함께 갱신된다.

실질금리·기대인플레·신용스프레드는 `fred_signal_daily`가 받는다. 여기 창(190일)으로 일별
계열을 물으면 같은 값을 매일 130행씩 다시 받는다.

## 왜 국채 DAG에 합치지 않나

**되돌아볼 구간이 다르다.** 국채는 매일 발표되므로 7일이면 충분하지만, 이들은 월간이고
발표가 한 달 넘게 늦다. 7월 CPI는 8월 중순에 나온다. 7일 창으로 물으면 아직 발표되지 않은
이번 달만 묻게 되어 매번 0건이 온다. 그래서 `LOOKBACK_DAYS_MACRO`가 190일(약 6개월)이다.
정정도 흔해서 지난 발표를 다시 읽는 값어치가 있다. `ecb_convergence_monthly`가 같은 이유로
같은 값을 쓴다.

이름도 이유다. 국채 DAG에 CPI가 들어가면 그 이름이 거짓이 된다.

## 월간인데 왜 매일 도나

지표는 월간이지만 **발표일이 불규칙하다.** CPI는 대체로 다음 달 중순, 소매판매는 중순 전,
PPI는 그 사이다. 달마다 요일도 다르다. 발표 달력을 따로 두고 맞추는 것보다 매일 한 번씩
묻는 편이 싸다. 계열당 요청 하나이고 하루 세 번이다.

멱등 키가 `(provider, series_id, observation_date)`라서 같은 값을 며칠씩 다시 받아도 행이
늘지 않는다.

## 관측일은 그 달 1일이다

FRED는 월간 값을 그 달 1일로 준다(실측 2026-08-16: 7월 CPI가 `2026-07-01`). 수집기가 그걸
검증하고, 다른 날짜가 오면 실패시킨다. 달 중간 날짜가 섞이면 같은 달이 두 행이 되고 그 뒤로는
어느 쪽이 진짜인지 알 수 없다.

## params

`fred_treasury_daily`와 같다. `observation_start`/`observation_end`로 구간을 직접 주거나
`lookback_days`로 되돌아볼 일수를 바꾼다.

    airflow dags trigger fred_macro_daily \\
      --conf '{"observation_start": "2005-01-01", "observation_end": "2026-08-16"}'

## 필요한 환경

- `FRED_API_KEY`. URL 질의 문자열에 들어가므로 예외 메시지와 로그에 URL을 넣지 않는다.
- `CONNECTION_ID`가 가리키는 Airflow 연결. 접속 정보는 `AIRFLOW_CONN_FINANCE`가 갖는다.
"""

import logging
import os
from contextlib import closing
from datetime import timedelta

import pendulum
from airflow.sdk import dag, get_current_context, task
from airflow.sdk.exceptions import AirflowFailException
from pydantic import SecretStr

from modules import dag_common
from modules.collectors.indicator.fred import (
    MACRO_SERIES,
    FredCollector,
    FredHTTPError,
    FredPayloadError,
    FredRequest,
)
from modules.utility import KST_TIMEZONE, UNRECOVERABLE_STATUSES, atomic

logger = logging.getLogger(__name__)

# 월간 지표는 발표가 한 달 넘게 늦고 정정도 잦다. 190일이면 최근 여섯 달치 정정까지 다시 받는다.
LOOKBACK_DAYS_MACRO = 190


@dag(
    dag_id="fred_macro_daily",
    dag_display_name="🇺🇸 미국 물가·소매판매 (FRED)",
    description="매일 FRED에서 미국 CPI·근원 CPI·PPI·근원 PCE·소매판매·고용을 받아 저장한다. 발표가 늦어 되돌아보는 구간이 길다.",
    # KST 화~토 07:40 = UTC 월~금 22:40. 국채 수집(07:30)보다 10분 뒤라 겹치지 않는다.
    # 미국 지표는 미국 영업일에 발표되므로 주말 트리거는 값이 없다.
    schedule="40 7 * * 2-6",
    start_date=pendulum.datetime(2026, 8, 16, tz=KST_TIMEZONE),  # KST 2026-08-16 00:00 = UTC 2026-08-15 15:00
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(hours=1)},
    params={
        **dag_common.observation_period_params(
            lookback_default=LOOKBACK_DAYS_MACRO,
            lookback_hint="월간 발표와 정정을 흡수한다. 짧게 잡으면 아직 발표되지 않은 달만 묻게 된다.",
            start_title="조회 시작일",
            end_title="조회 종료일",
            start_hint="비우면 observation_end에서 lookback_days만큼 앞으로 잡는다.",
            end_hint="비우면 이 run의 data_interval_end를 KST 날짜로 바꿔 쓴다.",
        ),
    },
    doc_md=__doc__,
    tags=["fred", "macro", "daily"],
)
def fred_macro_daily():
    @task(task_display_name="지표 수집·저장")
    def collect(series_id: str) -> int:
        """시계열 하나를 받아 저장한다.

        시계열마다 태스크를 매핑해 한 계열이 실패해도 나머지가 저장되게 한다. 재시도도 실패한
        계열만 다시 호출한다. `fred_treasury_daily`와 같은 구조다.
        """
        context = get_current_context()
        observation_start, observation_end = dag_common.resolve_period_or_fail(context, LOOKBACK_DAYS_MACRO)

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

        with closing(dag_common.connection()) as connection:
            try:
                with atomic(connection):
                    count = collector.store_observations(connection, response)
            except FredPayloadError as error:
                # 관측일이 그 달 1일이 아닌 경우도 여기로 온다. 재시도해도 같은 응답이다.
                raise AirflowFailException(str(error)) from error

        logger.info(
            "Stored %s %s observations for %s..%s",
            count,
            request.series_id,
            request.observation_start,
            request.observation_end,
        )
        return count

    collect.expand(series_id=list(MACRO_SERIES))


fred_macro_daily = fred_macro_daily()
