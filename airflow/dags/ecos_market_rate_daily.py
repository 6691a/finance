"""ECOS 국내 시장금리 일별 수집 DAG.

시계열마다 태스크를 하나씩 매핑한다. 하나가 실패해도 나머지는 저장되고, 재시도도 실패한
시계열만 다시 호출한다. 수집 대상은 `modules.collectors.indicator.ecos.MarketRateSeries`가 정한다
(현재 `KTB2Y`, `KTB3Y`, `KTB10Y`, `KTB30Y`, `CD91D`). 시계열을 늘려도 이 파일은 바뀌지 않는다.

한국은행 통계표 `1.3.2.1. 시장금리(일별)`(817Y002)을 주기 `D`로 조회한다. 미국 국채를
받는 `fred_treasury_daily`와 같은 테이블에 쌓이며 `provider`로 갈린다.

저장하는 `series_id`는 `KTB10Y`처럼 읽을 수 있는 ID다. ECOS 항목코드(`010210000`)는
`MarketRateSeries`가 들고 있다가 요청 URL에만 쓰고 `source_record.metadata`에 남긴다.
숫자 코드를 그대로 저장하면 DB와 대시보드에서 무슨 값인지 읽을 수 없기 때문이다.

스케줄과 조회 기간은 한국 시간(KST) 기준이다. 저장하는 시각(`started_at`, `completed_at`)은
그대로 UTC다. 시간대는 트리거 시점과 날짜 경계를 정할 때만 쓴다.

## 조회 구간을 정하는 규칙

한 태스크는 `observation_start..observation_end` 하루 이상의 구간을 ECOS에 한 번 요청한다.
구간은 아래 순서로 정해진다.

1. `params.observation_start` / `params.observation_end`가 있으면 그 값을 그대로 쓴다.
2. 없으면 이 run의 시각에서 계산한다. 기준 시각은 `data_interval_end`이고, 수동 run처럼
   data interval이 없으면 `dag_run.run_after`를 쓴다. 그 시각을 KST로 바꾼 날짜가
   `observation_end`이고, `observation_start`는 거기서 `params.lookback_days - 1`일 앞이다.

`lookback_days` 기본값은 7이다. 일별 스케줄이 매번 최근 7일을 다시 조회한다는 뜻이다.
휴장일과 발표 지연을 별도 캘린더 없이 흡수하려는 장치다. 멱등 키가
`(provider, series_id, observation_date)`라서 같은 날짜를 다시 받아도 행이 늘지 않고 최신
값으로 갱신된다.

FRED와 달리 ECOS는 요청 구간 밖의 날짜를 돌려주지 않는다. 되돌아본 만큼이 그대로 조회
구간이므로 백필이 요청 범위 밖으로 넘치지 않는다.

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `observation_start` | `null` | 조회 시작 관측일(YYYY-MM-DD). 주면 `lookback_days`를 무시한다 |
| `observation_end` | `null` | 조회 종료 관측일(YYYY-MM-DD). 주면 run 시각을 무시한다 |
| `lookback_days` | `7` | 구간을 지정하지 않을 때 되돌아볼 일수. `1`이면 그 run의 하루만 |

날짜는 한국은행의 고시 기준일, 즉 **국내 영업일**이다. ECOS가 요청 구간 안에 값이 있는
날만 돌려주므로 휴장일은 응답에 아예 없다.

## 실행 방법

1. 일별 스케줄. 손댈 것 없다. KST 화~토 08:00에 돌면서 최근 7일을 다시 확인한다.

2. 구간을 정확히 맞춘 백필. run 하나가 자기 날짜 하루만 가져온다.

       airflow backfill create --dag-id ecos_market_rate_daily \\
         --from-date 2026-06-01 --to-date 2026-07-31 \\
         --dag-run-conf '{"lookback_days": 1}'

3. 과거 대량 적재. run 하나가 구간 전체를 한 번에 요청한다. 항목당 호출 1회로 끝난다.
   ECOS는 한 번에 10만 건까지 주므로 일별 값이면 수백 년 치가 한 호출에 들어온다.

       airflow dags trigger ecos_market_rate_daily \\
         --conf '{"observation_start": "2000-01-01", "observation_end": "2026-07-31"}'

4. 하루만 확인. 수동 run은 data interval이 없어 `run_after`로 계산하므로, 날짜를 확실히
   하려면 직접 넘긴다.

       airflow dags test ecos_market_rate_daily \\
         --conf '{"observation_start": "2026-08-06", "observation_end": "2026-08-06"}'

백필 run이 `queued`에서 안 넘어가면 그 backfill이 pause됐는지 본다. scheduler는 pause된
backfill의 dag_run을 running으로 올리지 않는다. 태스크를 clear해도 소용없다.

## 실패와 재시도

ECOS는 실패도 HTTP 200으로 답하고 본문의 `RESULT.CODE`에 이유를 담는다. 그래서 분류를
상태 코드가 아니라 그 값으로 한다.

- `INFO-200`(데이터 없음): 실패가 아니다. 관측값 0건으로 저장하고 끝낸다. 휴장일만 걸린
  구간이거나 아직 발표되지 않은 구간이다.
- `INFO-100`(인증키 무효)과 `ERROR-1xx~4xx`(요청 인자 문제): 고치기 전에는 재시도해도
  같으므로 `AirflowFailException`으로 즉시 실패한다.
- 그 밖의 `RESULT` 코드: ECOS 쪽 서버·DB 오류로 보고 그대로 올려 재시도한다(2회, 1시간 간격).
- HTTP 400/401/403/404: 설정 오류라 즉시 실패한다. 그 밖의 HTTP·네트워크 오류는 재시도한다.
- 응답이 `StatisticSearch` 계약을 어기거나 요청한 건수보다 데이터가 많아 잘리면 즉시
  실패한다. 이때는 아무 것도 쓰지 않는다.

## 필요한 환경

- `ECOS_API_KEY` 환경 변수. Airflow가 읽는 건 `compose/local/airflow/.env`다.
  다른 스택의 `compose/local/.env`에 넣으면 컨테이너에 들어가지 않는다. 값을 바꾸면
  `docker compose up -d`로 컨테이너를 다시 만들어야 반영된다.
- `CONNECTION_ID`가 가리키는 Airflow 연결. 접속 정보는 `AIRFLOW_CONN_FINANCE`가 갖는다.

원본 응답은 `source_record`에, 유효 관측값은 `indicator_observation`에 저장한다. 테이블 정의의
원본은 백엔드의 `apps/models`이고, 이 DAG가 쓰는 SQL은 `airflow/sql/postgres/` 아래에 있다.
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

from modules.collectors.indicator.ecos import (
    MARKET_RATE_SERIES,
    EcosCollector,
    EcosHTTPError,
    EcosPayloadError,
    EcosRequest,
    EcosResultError,
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

# 인증키가 유효하지 않다는 응답. 키를 고치기 전에는 재시도해도 같다.
INVALID_KEY_CODE = "INFO-100"

# 요청 인자를 고쳐야 하는 오류 대역. ECOS는 서버·DB 쪽 오류를 ERROR-5xx, ERROR-6xx로 내고
# 그보다 낮은 대역을 요청 문제에 쓴다. 실제 응답으로 확인한 코드는 INFO-100과 INFO-200뿐이라
# 모르는 코드는 재시도 쪽에 둔다. 재시도는 값이 싸고, 잘못 즉시 실패시키면 그 run이 사라진다.
UNRECOVERABLE_RESULT_PREFIXES = ("ERROR-1", "ERROR-2", "ERROR-3", "ERROR-4")


def is_unrecoverable_result(code: str) -> bool:
    """이 `RESULT.CODE`가 재시도로 풀리지 않는 오류인지."""
    return code == INVALID_KEY_CODE or code.startswith(UNRECOVERABLE_RESULT_PREFIXES)


@dag(
    dag_id="ecos_market_rate_daily",
    dag_display_name="🇰🇷 국내 시장금리 (ECOS)",
    description="한국은행 ECOS에서 국고채·CD 등 국내 시장금리를 매일 받아 market.indicator_observation에 쌓는다.",
    schedule="0 8 * * 2-6",  # KST 화~토 08:00 = UTC 월~금 23:00
    start_date=pendulum.datetime(2026, 8, 7, tz=KST_TIMEZONE),  # KST 2026-08-07 00:00 = UTC 2026-08-06 15:00
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(hours=1)},
    params={
        OBSERVATION_START_PARAM: Param(
            None,
            type=["null", "string"],
            format="date",
            title="조회 시작 관측일",
            description="비우면 observation_end에서 lookback_days만큼 뺀 날. 주면 lookback_days를 무시한다.",
        ),
        OBSERVATION_END_PARAM: Param(
            None,
            type=["null", "string"],
            format="date",
            title="조회 종료 관측일",
            description="비우면 이 run 시각의 KST 날짜. 과거 구간을 한 번에 넣을 때 직접 넘긴다.",
        ),
        LOOKBACK_DAYS_PARAM: Param(
            LOOKBACK_DAYS,
            type="integer",
            minimum=1,
            title="되돌아볼 일수",
            description="구간을 지정하지 않을 때만 쓴다. 1이면 그 run의 하루만 조회한다.",
        ),
    },
    doc_md=__doc__,
    tags=["ecos", "macro", "daily"],
)
def ecos_market_rate_daily():
    @task(task_display_name="시계열 수집·저장")
    def collect(series: str) -> int:
        context = get_current_context()
        try:
            observation_start, observation_end = resolve_observation_period(context)
        except PeriodError as error:
            raise AirflowFailException(str(error)) from error
        request = EcosRequest(
            series=series,
            observation_start=observation_start,
            observation_end=observation_end,
        )

        api_key = os.environ.get("ECOS_API_KEY")
        if not api_key:
            raise AirflowFailException("ECOS_API_KEY is required")
        collector = EcosCollector(SecretStr(api_key))

        try:
            response = collector.fetch_series(request)
        except EcosHTTPError as error:
            if error.status in UNRECOVERABLE_STATUSES:
                raise AirflowFailException(str(error)) from error
            if error.retry_after is not None:
                logger.warning("ECOS asked to retry after %s seconds", error.retry_after)
            raise

        with closing(PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()) as connection:
            try:
                with atomic(connection):
                    count = collector.store_observations(connection, response)
            except EcosResultError as error:
                if is_unrecoverable_result(error.code):
                    raise AirflowFailException(str(error)) from error
                raise
            except EcosPayloadError as error:
                raise AirflowFailException(str(error)) from error

        logger.info(
            "Stored %s %s observations for %s..%s",
            count,
            request.series_id,
            request.observation_start,
            request.observation_end,
        )
        return count

    collect.expand(series=list(MARKET_RATE_SERIES))


ecos_market_rate_daily = ecos_market_rate_daily()
