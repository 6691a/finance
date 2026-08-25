"""독일 국채 수익률 곡선 일별 수집 DAG.

태스크는 하나다. SDMX 키의 만기 차원에 `+`를 넣어 만기를 한꺼번에 물을 수 있어 시계열마다
요청할 일이 없다. `mof_jgb_daily`, `boe_gilt_daily`, `ecb_yield_curve_daily`와 같고
`fred_treasury_daily`, `ecos_market_rate_daily`와 다르다. 수집 대상은
`modules.collectors.bbk.BundSeries`가 정한다(현재 1·2·3·5·7·10·15·20·30년).
시계열을 늘려도 이 파일은 바뀌지 않는다.

## 유로 지역 국채가 셋으로 나뉜 이유

유로 지역 개별 회원국의 **일별** 국채 금리를 무료·무인증으로 주는 공식 소스는 독일뿐이다.

    ecb_yield_curve_daily     유로 지역(XM) AAA 곡선   일별   3개월~30년
    bbk_bund_daily            독일(DE)                 일별   1~30년     ← 이 DAG
    ecb_convergence_monthly   프랑스·이탈리아·스페인   월별   10년만

셋 다 같은 `indicator_observation` 테이블에 쌓이고 `provider`로 갈린다. 독일 곡선의 만기
아홉 개는 유로 지역 AAA 곡선의 1년 이상 만기와 정확히 같아서 그대로 겹쳐 볼 수 있다.

인증이 없다. API 키도 등록도 필요 없고 환경 변수도 `AIRFLOW_CONN_FINANCE` 하나면 된다.

스케줄과 조회 기간은 한국 시간(KST) 기준이다. 관측일은 독일 영업일이라 KST 날짜와 어긋날
수 있는데, 되돌아보는 구간이 그 차이를 흡수한다. 저장하는 시각(`started_at`,
`completed_at`)은 그대로 UTC다.

곡선은 독일 시간 정오 무렵(KST 저녁)에 갱신된다. KST 다음 날 아침에 도는 이 DAG는 그
값을 받는다.

## 응답이 가로 형식이다

만기가 열로 늘어서고 값 열마다 `_FLAGS` 열이 따라붙는다. 수집기는 열을 **위치가 아니라
이름으로** 묶는다. 요청한 시계열 키가 헤더에 전부 있고 모르는 키가 없다는 것까지 확인한
뒤 그 인덱스를 쓴다.

요청에 `lang=en`을 붙인다. 붙이지 않으면 독일어 표기라 구분자가 `;`, 소수점이 `,`가 되어
파싱이 통째로 어긋난다.

## 조회 구간을 정하는 규칙

`observation_start..observation_end` 구간은 아래 순서로 정해진다. 규칙은
`modules.period.resolve_observation_period`에 있고 수집 DAG들이 함께 쓴다.

1. `params.observation_start` / `params.observation_end`가 있으면 그 값을 그대로 쓴다.
2. 없으면 이 run의 시각에서 계산한다. 기준 시각은 `data_interval_end`이고, 수동 run처럼
   data interval이 없으면 `dag_run.run_after`를 쓴다. 그 시각을 KST로 바꾼 날짜가
   `observation_end`이고, `observation_start`는 거기서 `params.lookback_days - 1`일 앞이다.

`lookback_days` 기본값은 7이다. 멱등 키가 `(provider, series_id, observation_date)`라서
같은 날짜를 다시 받아도 행이 늘지 않고 최신 값으로 갱신된다.

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `observation_start` | `null` | 조회 시작 기준일(YYYY-MM-DD). 주면 `lookback_days`를 무시한다 |
| `observation_end` | `null` | 조회 종료 기준일(YYYY-MM-DD). 주면 run 시각을 무시한다 |
| `lookback_days` | `7` | 구간을 지정하지 않을 때 되돌아볼 일수. `1`이면 그 run의 하루만 |

날짜는 분데스방크 고시 기준일, 즉 **독일 영업일**이다. 주말과 독일 공휴일에는 행이 없다.

## 실행 방법

1. 일별 스케줄. 손댈 것 없다. KST 화~토 08:10에 돌면서 최근 7일을 다시 확인한다.

2. 과거 대량 적재. 한 응답이 곡선 전체를 담으므로 run 하나가 구간 전체를 처리한다.
   만기가 아홉 개라 관측값이 빨리 불어난다. 한 트랜잭션이 너무 커지지 않게 2년 단위로 자른다.

       airflow dags trigger bbk_bund_daily \\
         --conf '{"observation_start": "2023-01-01", "observation_end": "2024-12-31"}'

3. 구간을 정확히 맞춘 백필. run 하나가 자기 날짜 하루만 저장한다. 긴 구간에는 2번이 낫다.

       airflow backfill create --dag-id bbk_bund_daily \\
         --from-date 2026-06-01 --to-date 2026-07-31 \\
         --dag-run-conf '{"lookback_days": 1}'

4. 하루만 확인.

       airflow dags test bbk_bund_daily \\
         --conf '{"observation_start": "2026-08-06", "observation_end": "2026-08-06"}'

백필 run이 `queued`에서 안 넘어가면 그 backfill이 pause됐는지 본다. scheduler는 pause된
backfill의 dag_run을 running으로 올리지 않는다.

## 실패와 재시도

- HTTP 400/401/403/404: 시계열 키나 경로가 틀린 것이라 즉시 실패한다. 그 밖의 HTTP·네트워크
  오류는 재시도한다(2회, 1시간 간격).
- 헤더에 요청하지 않은 시계열이 섞였거나 요청한 시계열이 빠졌으면 즉시 실패한다. 이때는
  아무 것도 쓰지 않는다. 값이 조용히 옆 칸으로 밀린 채 저장되는 것보다 멈추는 편이 낫다.
- 구간에 데이터 줄이 하나도 없으면 실패가 아니다. 관측값 0건으로 저장하고 `source_record`는
  남긴다. 조회했지만 값이 없는 구간과 아직 조회하지 않은 구간이 구분돼야 한다.

## 필요한 환경

- `CONNECTION_ID`가 가리키는 Airflow 연결. 접속 정보는 `AIRFLOW_CONN_FINANCE`가 갖는다.
- 인증 정보는 없다.

원본 CSV는 jsonb 컬럼에 넣지 않으므로 `source_record.payload`는 비어 있다. 유효 관측값은
`indicator_observation`에 저장한다. 테이블 정의의 원본은 백엔드의 `apps/models`이고, 이 DAG가
쓰는 SQL은 `airflow/sql/postgres/` 아래에 있다.
"""

import logging
from contextlib import closing
from datetime import timedelta

import pendulum
from airflow.exceptions import AirflowFailException
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Param, dag, get_current_context, task

from modules.collectors.indicator.bbk import (
    BbkHTTPError,
    BbkPayloadError,
    BbkRequest,
    fetch_curve,
    store_observations,
)
from modules.period import (
    LOOKBACK_DAYS,
    LOOKBACK_DAYS_PARAM,
    OBSERVATION_END_PARAM,
    OBSERVATION_START_PARAM,
    PeriodError,
    resolve_observation_period,
)
from modules.utility import CONNECTION_ID, KST_TIMEZONE, UNRECOVERABLE_STATUSES, atomic

logger = logging.getLogger(__name__)


@dag(
    dag_id="bbk_bund_daily",
    dag_display_name="🇩🇪 독일 국채 금리 (Bundesbank)",
    description="독일연방은행에서 독일 국채 만기별 금리를 매일 받아 market.indicator_observation에 쌓는다.",
    schedule="10 8 * * 2-6",  # KST 화~토 08:10 = UTC 월~금 23:10
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
            description="비우면 observation_end에서 lookback_days만큼 뺀 날. 주면 lookback_days를 무시한다.",
        ),
        OBSERVATION_END_PARAM: Param(
            None,
            type=["null", "string"],
            format="date",
            title="조회 종료 기준일",
            description="비우면 이 run 시각의 KST 날짜. 과거 구간을 한 번에 넣을 때 직접 넘긴다.",
        ),
        LOOKBACK_DAYS_PARAM: Param(
            LOOKBACK_DAYS,
            type="integer",
            minimum=1,
            title="되돌아볼 일수",
            description="구간을 지정하지 않을 때만 쓴다. 1이면 그 run의 하루만 저장한다.",
        ),
    },
    doc_md=__doc__,
    tags=["bbk", "macro", "daily"],
)
def bbk_bund_daily():
    @task
    def collect() -> int:
        context = get_current_context()
        try:
            observation_start, observation_end = resolve_observation_period(context)
        except PeriodError as error:
            raise AirflowFailException(str(error)) from error

        request = BbkRequest(observation_start=observation_start, observation_end=observation_end)

        try:
            response = fetch_curve(request)
        except BbkHTTPError as error:
            if error.status in UNRECOVERABLE_STATUSES:
                raise AirflowFailException(str(error)) from error
            if error.retry_after is not None:
                logger.warning("Bundesbank asked to retry after %s seconds", error.retry_after)
            raise

        with closing(PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()) as connection:
            try:
                with atomic(connection):
                    count = store_observations(connection, response)
            except BbkPayloadError as error:
                raise AirflowFailException(str(error)) from error

        logger.info(
            "Stored %s German government bond observations for %s..%s",
            count,
            request.observation_start,
            request.observation_end,
        )
        return count

    collect()


bbk_bund_daily = bbk_bund_daily()
