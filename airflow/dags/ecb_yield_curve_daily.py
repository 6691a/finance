"""유로 지역 국채 수익률 곡선 일별 수집 DAG.

태스크는 하나다. SDMX 키의 마지막 차원에 `+`를 넣어 만기를 한꺼번에 물을 수 있어 시계열마다
요청할 일이 없다. `fred_treasury_daily`, `ecos_market_rate_daily`가 시계열마다 태스크를
매핑하는 것과 다르고 `mof_jgb_daily`, `boe_gilt_daily`와 같다. 수집 대상은
`modules.collectors.ecb.EuroYieldSeries`가 정한다(현재 3개월·6개월·1·2·3·5·7·10·15·20·30년).
시계열을 늘려도 이 파일은 바뀌지 않는다.

받는 값은 유로 지역 전체의 **AAA 등급 국채 스팟 곡선** 하나다. 독일이나 프랑스 같은 개별
회원국 곡선이 아니다. ECB가 Svensson 모형으로 추정해 고시하며 `indicator_series.country`에는
통화권을 뜻하는 `XM`이 들어간다.

미국 국채를 받는 `fred_treasury_daily`, 국내 시장금리를 받는 `ecos_market_rate_daily`,
일본 국채를 받는 `mof_jgb_daily`, 영국 국채를 받는 `boe_gilt_daily`와 같은 테이블에 쌓이며
`provider`로 갈린다.

인증이 없다. API 키도 등록도 필요 없고 환경 변수도 `AIRFLOW_CONN_FINANCE` 하나면 된다.

스케줄과 조회 기간은 한국 시간(KST) 기준이다. 관측일은 유로 지역 영업일(TARGET 결제일)이라
KST 날짜와 어긋날 수 있는데, 되돌아보는 구간이 그 차이를 흡수한다. 저장하는 시각
(`started_at`, `completed_at`)은 그대로 UTC다.

곡선은 유럽 시간 정오 무렵에 갱신되고 최근 1~2 영업일은 아직 없을 수 있다. 그래서 어제
날짜가 비어 있는 것은 정상이다. 되돌아보는 구간이 다음 run에서 그 날짜를 채운다.

## 값이 없는 구간과 잘못된 키가 갈린다

- 요청 구간에 데이터가 없으면 **HTTP 200에 빈 본문**이 온다. 헤더 줄조차 없다. 이건 휴장
  이지 오류가 아니라서 관측값 0건으로 저장하고 `source_record`는 남긴다. 조회했지만 값이
  없는 구간과 아직 조회하지 않은 구간이 구분돼야 하기 때문이다.
- 존재하지 않는 시계열 키를 물으면 **HTTP 404**가 온다. 설정 오류라 즉시 실패한다.

`boe_gilt_daily`는 이 둘을 응답만으로 가를 수 없어 구간에 패딩을 붙이지만 여기서는 필요 없다.

## 조회 구간을 정하는 규칙

`observation_start..observation_end` 구간은 아래 순서로 정해진다. 규칙은
`modules.period.resolve_observation_period`에 있고 수집 DAG들이 함께 쓴다.

1. `params.observation_start` / `params.observation_end`가 있으면 그 값을 그대로 쓴다.
2. 없으면 이 run의 시각에서 계산한다. 기준 시각은 `data_interval_end`이고, 수동 run처럼
   data interval이 없으면 `dag_run.run_after`를 쓴다. 그 시각을 KST로 바꾼 날짜가
   `observation_end`이고, `observation_start`는 거기서 `params.lookback_days - 1`일 앞이다.

`lookback_days` 기본값은 7이다. 일별 스케줄이 매번 최근 7일을 다시 조회한다는 뜻이다.
휴일과 발표 지연을 별도 캘린더 없이 흡수하려는 장치다. 멱등 키가
`(provider, series_id, observation_date)`라서 같은 날짜를 다시 받아도 행이 늘지 않고 최신
값으로 갱신된다.

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `observation_start` | `null` | 조회 시작 기준일(YYYY-MM-DD). 주면 `lookback_days`를 무시한다 |
| `observation_end` | `null` | 조회 종료 기준일(YYYY-MM-DD). 주면 run 시각을 무시한다 |
| `lookback_days` | `7` | 구간을 지정하지 않을 때 되돌아볼 일수. `1`이면 그 run의 하루만 |

날짜는 ECB 고시 기준일, 즉 **유로 지역 영업일**이다. 주말과 TARGET 휴일에는 행이 아예 없다.

## 실행 방법

1. 일별 스케줄. 손댈 것 없다. KST 화~토 08:50에 돌면서 최근 7일을 다시 확인한다.

2. 과거 대량 적재. 한 응답이 곡선 전체를 담으므로 run 하나가 구간 전체를 처리한다.
   만기가 열한 개라 관측값이 빨리 불어난다. 한 트랜잭션이 너무 커지지 않게 2년 단위로 자른다.

       airflow dags trigger ecb_yield_curve_daily \\
         --conf '{"observation_start": "2023-01-01", "observation_end": "2024-12-31"}'

   곡선은 2004-09-06부터 고시된다. 그보다 이른 구간을 물으면 값이 0건으로 나온다.

3. 구간을 정확히 맞춘 백필. run 하나가 자기 날짜 하루만 저장한다. 매 run이 응답을 다시
   받으므로 긴 구간에는 2번이 낫다.

       airflow backfill create --dag-id ecb_yield_curve_daily \\
         --from-date 2026-06-01 --to-date 2026-07-31 \\
         --dag-run-conf '{"lookback_days": 1}'

4. 하루만 확인. 수동 run은 data interval이 없어 `run_after`로 계산하므로, 날짜를 확실히
   하려면 직접 넘긴다.

       airflow dags test ecb_yield_curve_daily \\
         --conf '{"observation_start": "2026-08-05", "observation_end": "2026-08-05"}'

백필 run이 `queued`에서 안 넘어가면 그 backfill이 pause됐는지 본다. scheduler는 pause된
backfill의 dag_run을 running으로 올리지 않는다. 태스크를 clear해도 소용없다.

## 실패와 재시도

- HTTP 400/401/403/404: 시계열 키나 경로가 틀린 것이라 즉시 실패한다. 그 밖의 HTTP·네트워크
  오류는 재시도한다(2회, 1시간 간격).
- 열 구성이 바뀌었거나 우리가 물어본 것과 다른 시계열 키가 섞여 오면 즉시 실패한다.
  이때는 아무 것도 쓰지 않는다. 다른 등급의 곡선이 조용히 섞여 저장되는 것보다 멈추는
  편이 낫다.

## 필요한 환경

- `CONNECTION_ID`가 가리키는 Airflow 연결. 접속 정보는 `AIRFLOW_CONN_FINANCE`가 갖는다.
- 인증 정보는 없다.

원본 CSV는 jsonb 컬럼에 넣지 않으므로 `source_record.payload`는 비어 있다. 어느 구간을
물어 어느 구간이 돌아왔는지는 `source_record.metadata`가 남긴다. 유효 관측값은
`indicator_observation`에 저장한다. 테이블 정의의 원본은 백엔드의 `apps/models`이고, 이 DAG가
쓰는 SQL은 `airflow/sql/postgres/` 아래에 있다.
"""

import logging
from contextlib import closing
from datetime import timedelta

import pendulum
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Param, dag, get_current_context, task
from airflow.sdk.exceptions import AirflowFailException

from modules.collectors.indicator.ecb import (
    EcbHTTPError,
    EcbPayloadError,
    EcbRequest,
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
    dag_id="ecb_yield_curve_daily",
    dag_display_name="🇪🇺 유로 지역 국채 금리 (ECB)",
    description="ECB에서 유로 지역 AAA 국채 만기별 금리를 매일 받아 market.indicator_observation에 쌓는다.",
    schedule="50 8 * * 2-6",  # KST 화~토 08:50 = UTC 월~금 23:50
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
    tags=["ecb", "macro", "daily"],
)
def ecb_yield_curve_daily():
    @task
    def collect() -> int:
        context = get_current_context()
        try:
            observation_start, observation_end = resolve_observation_period(context)
        except PeriodError as error:
            raise AirflowFailException(str(error)) from error

        request = EcbRequest(observation_start=observation_start, observation_end=observation_end)

        try:
            response = fetch_curve(request)
        except EcbHTTPError as error:
            if error.status in UNRECOVERABLE_STATUSES:
                raise AirflowFailException(str(error)) from error
            if error.retry_after is not None:
                logger.warning("ECB asked to retry after %s seconds", error.retry_after)
            raise

        with closing(PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()) as connection:
            try:
                with atomic(connection):
                    count = store_observations(connection, response)
            except EcbPayloadError as error:
                raise AirflowFailException(str(error)) from error

        logger.info(
            "Stored %s euro area yield curve observations for %s..%s",
            count,
            request.observation_start,
            request.observation_end,
        )
        return count

    collect()


ecb_yield_curve_daily = ecb_yield_curve_daily()
