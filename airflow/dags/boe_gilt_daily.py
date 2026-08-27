"""영국 국채 금리(gilt) 일별 수집 DAG.

태스크는 하나다. 잉글랜드은행 IADB가 세 시계열을 한 응답에 담아 주기 때문에 시계열마다
요청할 일이 없다. `fred_treasury_daily`, `ecos_market_rate_daily`가 시계열마다 태스크를
매핑하는 것과 다르고 `mof_jgb_daily`와 같다. 수집 대상은
`modules.collectors.boe.GILT_DATASET`이 정한다(현재 `GILT5Y`, `GILT10Y`, `GILT20Y`).
같은 IADB에서 받는 영란은행 기준금리는 주기가 달라 `policy_rate_weekly`가 따로 받는다.
시계열을 늘려도 이 파일은 바뀌지 않는다.

IADB의 명목 par yield 노드가 일별로 고시하는 만기는 5·10·20년 셋뿐이다. 0.5~40년 전
구간을 담은 일별 수익률 곡선은 BoE가 따로 내지만 형식이 xlsx라 이 이미지의 의존성으로는
읽을 수 없다.

미국 국채를 받는 `fred_treasury_daily`, 국내 시장금리를 받는 `ecos_market_rate_daily`,
일본 국채를 받는 `mof_jgb_daily`, 유로 지역 곡선을 받는 `ecb_yield_curve_daily`와 같은
테이블에 쌓이며 `provider`로 갈린다.

인증이 없다. API 키도 등록도 필요 없고 환경 변수도 `AIRFLOW_CONN_FINANCE` 하나면 된다.

스케줄과 조회 기간은 한국 시간(KST) 기준이다. 관측일은 영국 영업일이라 KST 날짜와 어긋날
수 있는데, 되돌아보는 구간이 그 차이를 흡수한다. 저장하는 시각(`started_at`,
`completed_at`)은 그대로 UTC다.

## 값이 없는 구간과 잘못된 코드를 응답만으로 가를 수 없다

IADB는 요청 구간에 데이터가 한 행도 없으면 CSV가 아니라 **HTTP 200으로 HTML 오류
페이지**를 준다. 존재하지 않는 시계열 코드를 물었을 때도 같은 페이지가 온다.

그래서 수집기가 조회 구간보다 `modules.collectors.boe.FETCH_PADDING_DAYS`(14일)만큼 앞에서
받는다. 주말이나 영국 공휴일만 걸린 구간이라도 영업일이 반드시 들어가 응답이 CSV가 된다.
구간 밖의 행은 저장 전에 버리므로 저장 결과는 달라지지 않는다. 패딩까지 붙였는데도 오류
페이지가 오면 코드나 구간 자체가 틀린 것이라 즉시 실패한다.

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

날짜는 BoE 고시 기준일, 즉 **영국 영업일**이다. 주말과 영국 공휴일(bank holiday)에는
행이 아예 없다.

## 실행 방법

1. 일별 스케줄. 손댈 것 없다. KST 화~토 08:40에 돌면서 최근 7일을 다시 확인한다.

2. 과거 대량 적재. 한 응답이 곡선 전체를 담으므로 run 하나가 구간 전체를 처리한다.
   한 트랜잭션이 너무 커지지 않게 5년 단위로 자른다.

       airflow dags trigger boe_gilt_daily \\
         --conf '{"observation_start": "2020-01-01", "observation_end": "2024-12-31"}'

3. 구간을 정확히 맞춘 백필. run 하나가 자기 날짜 하루만 저장한다. 매 run이 응답을 다시
   받으므로 긴 구간에는 2번이 낫다.

       airflow backfill create --dag-id boe_gilt_daily \\
         --from-date 2026-06-01 --to-date 2026-07-31 \\
         --dag-run-conf '{"lookback_days": 1}'

4. 하루만 확인. 수동 run은 data interval이 없어 `run_after`로 계산하므로, 날짜를 확실히
   하려면 직접 넘긴다.

       airflow dags test boe_gilt_daily \\
         --conf '{"observation_start": "2026-08-04", "observation_end": "2026-08-04"}'

백필 run이 `queued`에서 안 넘어가면 그 backfill이 pause됐는지 본다. scheduler는 pause된
backfill의 dag_run을 running으로 올리지 않는다. 태스크를 clear해도 소용없다.

## 실패와 재시도

- HTTP 400/401/403/404: 경로가 바뀌었거나 차단된 것이라 즉시 실패한다. 그 밖의 HTTP·네트워크
  오류는 재시도한다(2회, 1시간 간격).
- CSV 대신 HTML 오류 페이지가 오면 즉시 실패한다. 패딩을 붙이고도 나온 것이라 시계열
  코드나 구간이 틀렸다는 뜻이다.
- 열 구성이 바뀌었거나 날짜 표기가 바뀌면 즉시 실패한다. 이때는 아무 것도 쓰지 않는다.
  값이 조용히 옆 칸으로 밀린 채 저장되는 것보다 멈추는 편이 낫다.

기본 User-Agent(`Python-urllib/3.x`)로 요청하면 IADB가 `Access Denied`를 준다. 수집기가
User-Agent를 명시한다. 인증이 아니므로 값 자체에 의미는 없다.

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

from modules.collectors.indicator.boe import (
    GILT_DATASET,
    BoeHTTPError,
    BoePayloadError,
    BoeRequest,
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
    dag_id="boe_gilt_daily",
    dag_display_name="🇬🇧 영국 국채 금리 (BoE)",
    description="잉글랜드은행에서 영국 Gilt 만기별 금리를 매일 받아 market.indicator_observation에 쌓는다.",
    schedule="40 8 * * 2-6",  # KST 화~토 08:40 = UTC 월~금 23:40
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
    tags=["boe", "macro", "daily"],
)
def boe_gilt_daily():
    @task
    def collect() -> int:
        context = get_current_context()
        try:
            observation_start, observation_end = resolve_observation_period(context)
        except PeriodError as error:
            raise AirflowFailException(str(error)) from error

        request = BoeRequest(
            dataset=GILT_DATASET,
            observation_start=observation_start,
            observation_end=observation_end,
        )

        try:
            response = fetch_curve(request)
        except BoeHTTPError as error:
            if error.status in UNRECOVERABLE_STATUSES:
                raise AirflowFailException(str(error)) from error
            if error.retry_after is not None:
                logger.warning("BoE asked to retry after %s seconds", error.retry_after)
            raise

        with closing(PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()) as connection:
            try:
                with atomic(connection):
                    count = store_observations(connection, response)
            except BoePayloadError as error:
                # HTML 오류 페이지도 여기 걸린다. 둘 다 파라미터나 제공처 형식 문제라 재시도해도 같다.
                raise AirflowFailException(str(error)) from error

        logger.info(
            "Stored %s gilt observations for %s..%s",
            count,
            request.observation_start,
            request.observation_end,
        )
        return count

    collect()


boe_gilt_daily = boe_gilt_daily()
