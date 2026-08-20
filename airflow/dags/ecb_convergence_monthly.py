"""유로 회원국 장기 국채 금리(월별) 수집 DAG.

프랑스·이탈리아·스페인의 10년 국채 금리를 ECB `IRS` dataflow에서 월별로 받는다. 수집
대상은 `modules.collectors.ecb_irs.ConvergenceSeries`가 정한다. 나라를 늘려도 이 파일은
바뀌지 않는다.

## 왜 월별인가

유로 지역 개별 회원국의 **일별** 국채 금리를 무료·무인증으로 주는 공식 소스는 독일뿐이다.
프랑스·이탈리아·스페인은 월별밖에 없다.

    ecb_yield_curve_daily     유로 지역(XM) AAA 곡선   일별   3개월~30년
    bbk_bund_daily            독일(DE)                 일별   1~30년
    ecb_convergence_monthly   프랑스·이탈리아·스페인   월별   10년만     ← 이 DAG

**독일은 여기서 받지 않는다.** 분데스방크가 같은 값을 일별로 주므로 `bbk_bund_daily`가
맡는다. 여기서 독일까지 받으면 `(country=DE, maturity_months=120)` 시계열이 둘이 되어
국가 비교 패널에 `독일` 선이 두 개 그려진다.

## 월별이라는 사실이 데이터에 남는다

이 테이블에는 일별 시계열이 대부분이다. 그래서 `series_id` 끝에 `M`을 붙이고(`FR10YM`)
`label`에도 `(월평균)`을 남긴다. 대시보드 범례와 표에 그대로 나온다.

`observation_date`는 **그 달의 1일**이다. 값은 한 달치 평균이라 특정 날짜의 고시가 아니다.
조회 구간 판정도 이 1일을 기준으로 하므로, 구간이 달 중간에서 시작하면 그 달은 빠진다.

## 스케줄이 주별이다

값은 월별인데 스케줄은 주별이다. 공표가 다음 달 중순께라 시점이 들쭉날쭉하고, 월별로
돌리면 한 번 놓쳤을 때 30일을 기다려야 한다. 되돌아보는 구간이 6개월이고 멱등 키가
`(provider, series_id, observation_date)`라서 매주 다시 읽어도 행이 늘지 않고 최신 값으로
갱신된다. 월평균은 다음 달에 개정되는 일이 있어 이 갱신이 실제로 쓰인다.

한 번 조회하면 3개국 × 6개월 = 18행이다. 매주 다시 읽어도 부담이 없다.

## 조회 구간을 정하는 규칙

규칙은 `modules.period.resolve_observation_period`에 있고 수집 DAG들이 함께 쓴다. 다른
DAG와 다른 것은 기본 되돌아보기 길이뿐이다.

1. `params.observation_start` / `params.observation_end`가 있으면 그 값을 그대로 쓴다.
2. 없으면 이 run의 KST 날짜가 `observation_end`이고, `observation_start`는 거기서
   `params.lookback_days - 1`일 앞이다.

`lookback_days` 기본값은 190이다. 대략 6개월로, 공표 지연과 사후 개정을 함께 흡수한다.
7일이면 이번 달 하나만 물어보게 되는데 그 달은 아직 공표 전이라 매번 0건이 된다.

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `observation_start` | `null` | 조회 시작 기준일(YYYY-MM-DD). 그 날이 속한 달부터 받는다 |
| `observation_end` | `null` | 조회 종료 기준일(YYYY-MM-DD). 그 날이 속한 달까지 받는다 |
| `lookback_days` | `190` | 구간을 지정하지 않을 때 되돌아볼 일수. 대략 6개월이다 |

날짜 구간은 요청 직전에 달로 바뀐다(`2026-03-15..2026-08-07` → `2026-03..2026-08`).
저장은 다시 날짜 기준이라 `2026-03-01`은 구간 밖이 되어 빠진다. 달을 정확히 맞추려면
`observation_start`를 그 달의 1일로 준다.

## 실행 방법

1. 주별 스케줄. 손댈 것 없다. KST 매주 수요일 08:30에 돌면서 최근 6개월을 다시 확인한다.

2. 과거 대량 적재. 한 응답이 3개국 전부를 담으므로 run 하나가 구간 전체를 처리한다.
   월별이라 10년을 넣어도 360행뿐이다.

       airflow dags trigger ecb_convergence_monthly \\
         --conf '{"observation_start": "2015-01-01", "observation_end": "2026-08-01"}'

3. 특정 달만 확인.

       airflow dags test ecb_convergence_monthly \\
         --conf '{"observation_start": "2026-06-01", "observation_end": "2026-06-01"}'

## 실패와 재시도

- HTTP 400/401/403/404: 시계열 키나 경로가 틀린 것이라 즉시 실패한다. 그 밖의 HTTP·네트워크
  오류는 재시도한다(2회, 1시간 간격).
- 아직 공표되지 않은 달은 HTTP 200에 빈 본문으로 온다. 실패가 아니라 관측값 0건이다.
  `source_record`는 남겨 조회한 구간과 아직 조회하지 않은 구간을 구분한다.
- `TIME_PERIOD`가 달 표기가 아니거나 요청하지 않은 나라가 섞이면 즉시 실패한다. 이때는
  아무 것도 쓰지 않는다.

## 필요한 환경

- `CONNECTION_ID`가 가리키는 Airflow 연결. 접속 정보는 `AIRFLOW_CONN_FINANCE`가 갖는다.
- 인증 정보는 없다.
"""

import logging
from contextlib import closing
from datetime import timedelta

import pendulum
from airflow.exceptions import AirflowFailException
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Param, dag, get_current_context, task

from modules.collectors.ecb_irs import (
    EcbIrsHTTPError,
    EcbIrsPayloadError,
    EcbIrsRequest,
    fetch_rates,
    store_observations,
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

# 대략 6개월. 다른 수집 DAG의 7일과 다르다. 월별 값이 다음 달 중순께 공표되고 사후 개정도
# 있어서, 7일이면 아직 공표 전인 이번 달 하나만 물어보게 되어 매번 0건이 된다.
LOOKBACK_DAYS_MONTHLY = 190


@dag(
    dag_id="ecb_convergence_monthly",
    dag_display_name="🇪🇺 유로 회원국 10년물 월평균 (ECB)",
    description="ECB에서 프랑스·이탈리아·스페인 10년 국채 금리 월평균을 받아 market.indicator_observation에 쌓는다.",
    schedule="30 8 * * 3",  # KST 매주 수요일 08:30 = UTC 매주 화요일 23:30
    start_date=pendulum.datetime(2026, 8, 7, tz=KST_TIMEZONE),  # KST 2026-08-07 00:00 = UTC 2026-08-06 15:00
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(hours=1)},
    params={
        OBSERVATION_START_PARAM: Param(
            None,
            type=["null", "string"],
            format="date",
            title="조회 시작 기준일",
            description="그 날이 속한 달부터 받는다. 달을 정확히 맞추려면 그 달의 1일을 준다.",
        ),
        OBSERVATION_END_PARAM: Param(
            None,
            type=["null", "string"],
            format="date",
            title="조회 종료 기준일",
            description="그 날이 속한 달까지 받는다. 비우면 이 run 시각의 KST 날짜다.",
        ),
        LOOKBACK_DAYS_PARAM: Param(
            LOOKBACK_DAYS_MONTHLY,
            type="integer",
            minimum=1,
            title="되돌아볼 일수",
            description="구간을 지정하지 않을 때만 쓴다. 기본 190일은 대략 6개월이다.",
        ),
    },
    doc_md=__doc__,
    tags=["ecb", "macro", "monthly"],
)
def ecb_convergence_monthly():
    @task
    def collect() -> int:
        context = get_current_context()
        try:
            observation_start, observation_end = resolve_observation_period(
                context,
                default_lookback_days=LOOKBACK_DAYS_MONTHLY,
            )
        except PeriodError as error:
            raise AirflowFailException(str(error)) from error

        request = EcbIrsRequest(observation_start=observation_start, observation_end=observation_end)

        try:
            response = fetch_rates(request)
        except EcbIrsHTTPError as error:
            if error.status in UNRECOVERABLE_STATUSES:
                raise AirflowFailException(str(error)) from error
            if error.retry_after is not None:
                logger.warning("ECB asked to retry after %s seconds", error.retry_after)
            raise

        with closing(PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()) as connection:
            try:
                with atomic(connection):
                    count = store_observations(connection, response)
            except EcbIrsPayloadError as error:
                raise AirflowFailException(str(error)) from error

        logger.info(
            "Stored %s euro member state observations for %s..%s",
            count,
            request.start_month,
            request.end_month,
        )
        return count

    collect()


ecb_convergence_monthly = ecb_convergence_monthly()
