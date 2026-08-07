"""FRED 미국 국채 금리 일별 수집 DAG.

시계열마다 태스크를 하나씩 매핑한다. 한 시계열이 실패해도 나머지는 저장되고, 재시도도
실패한 시계열만 다시 호출한다. 수집 대상은 `modules.collectors.fred.TREASURY_SERIES`가
정한다(현재 DGS3MO, DGS2, DGS10, DGS30). 시계열을 늘려도 이 파일은 바뀌지 않는다.

스케줄과 조회 기간은 한국 시간(KST) 기준이다. 저장하는 시각(`started_at`, `completed_at`)은
그대로 UTC다. 시간대는 트리거 시점과 날짜 경계를 정할 때만 쓴다.

## 조회 구간을 정하는 규칙

한 태스크는 `observation_start..observation_end` 하루 이상의 구간을 FRED에 한 번 요청한다.
구간은 아래 순서로 정해진다.

1. `params.observation_start` / `params.observation_end`가 있으면 그 값을 그대로 쓴다.
2. 없으면 이 run의 시각에서 계산한다. 기준 시각은 `data_interval_end`이고, 수동 run처럼
   data interval이 없으면 `dag_run.run_after`를 쓴다. 그 시각을 KST로 바꾼 날짜가
   `observation_end`이고, `observation_start`는 거기서 `params.lookback_days - 1`일 앞이다.

`lookback_days` 기본값은 7이다. 일별 스케줄이 매번 최근 7일을 다시 조회한다는 뜻이다.
휴장일과 발표 지연을 별도 캘린더 없이 흡수하려는 장치다. 어제 값이 오늘 늦게 올라와도
다음 run이 주워 담는다. 멱등 키가 `(series_id, observation_date)`라서 같은 날짜를 다시
받아도 행이 늘지 않고 최신 값으로 갱신된다.

되돌아보는 만큼 **요청한 구간보다 최대 6일 앞선 관측일이 함께 저장된다.** 6-01부터 백필해도
5-27 값이 들어온다. 구간을 정확히 맞춰야 하면 아래 사용법의 2번이나 3번을 쓴다.

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `observation_start` | `null` | 조회 시작 관측일(YYYY-MM-DD). 주면 `lookback_days`를 무시한다 |
| `observation_end` | `null` | 조회 종료 관측일(YYYY-MM-DD). 주면 run 시각을 무시한다 |
| `lookback_days` | `7` | 구간을 지정하지 않을 때 되돌아볼 일수. `1`이면 그 run의 하루만 |

날짜는 FRED의 관측일, 즉 **미국 영업일** 기준이다. run 시각에서 계산할 때만 KST 날짜를 쓴다.
KST 날짜는 미국 관측일보다 하루 앞서지만, FRED가 요청 구간 안에서 발표된 값만 돌려주므로
결과가 달라지지 않는다.

## 실행 방법

1. 일별 스케줄. 손댈 것 없다. KST 화~토 07:30에 돌면서 최근 7일을 다시 확인한다.

2. 구간을 정확히 맞춘 백필. run 하나가 자기 날짜 하루만 가져오므로 요청한 범위 밖으로
   넘치지 않는다. 이미 발표가 끝난 과거 구간에만 쓴다. 최근 며칠에 쓰면 발표 지연을
   흡수하지 못해 값이 빈다.

       airflow backfill create --dag-id fred_treasury_daily \
         --from-date 2026-06-01 --to-date 2026-07-31 \
         --dag-run-conf '{"lookback_days": 1}'

3. 과거 대량 적재. run 하나가 구간 전체를 한 번에 요청한다. 시계열당 호출 1회로 끝나므로
   FRED 분당 120회 제한에 걸리지 않는다. 두 달치를 2번 방식으로 넣으면 44개 run이
   시계열마다 호출을 반복해 176회가 된다.

       airflow dags trigger fred_treasury_daily \
         --conf '{"observation_start": "2026-06-01", "observation_end": "2026-07-31"}'

4. 하루만 확인. 수동 run은 data interval이 없어 `run_after`로 계산하므로, 날짜를 확실히
   하려면 직접 넘긴다.

       airflow dags test fred_treasury_daily \
         --conf '{"observation_start": "2026-08-04", "observation_end": "2026-08-04"}'

백필 run이 `queued`에서 안 넘어가면 그 backfill이 pause됐는지 본다. scheduler는 pause된
backfill의 dag_run을 running으로 올리지 않는다. 태스크를 clear해도 소용없다.

## 실패와 재시도

- HTTP 400/401/403/404: 설정 오류라 재시도해도 같으므로 `AirflowFailException`으로 즉시 실패한다.
- 그 밖의 HTTP 오류와 네트워크 오류: 그대로 올려서 재시도한다(2회, 1시간 간격).
  FRED가 `Retry-After`를 주면 경고 로그로 남긴다.
- 응답이 observations 계약을 어기면 재시도해도 같으므로 즉시 실패한다. 이때는 아무 것도 쓰지 않는다.
- 결측(`.`)은 실패가 아니다. 건너뛰고 나머지를 저장한다.

## 필요한 환경

- `FRED_API_KEY` 환경 변수. Airflow가 읽는 건 `compose/local/airflow/.env`다.
  다른 스택의 `compose/local/.env`에 넣으면 컨테이너에 들어가지 않는다. 값을 바꾸면
  `docker compose up -d`로 컨테이너를 다시 만들어야 반영된다.
- `CONNECTION_ID`가 가리키는 Airflow 연결. 접속 정보는 `AIRFLOW_CONN_NEWS`가 갖는다.

원본 응답은 `source_record`에, 유효 관측값은 `indicator_observation`에 저장한다. 테이블 정의의
원본은 백엔드의 `apps/models`이고, 이 DAG가 쓰는 SQL은 `airflow/sql/postgres/` 아래에 있다.
"""

import logging
import os
from datetime import timedelta
from typing import Any

import pendulum
from airflow.exceptions import AirflowFailException
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Param, dag, get_current_context, task
from pydantic import SecretStr

from modules.collectors.fred import (
    TREASURY_SERIES,
    FredHTTPError,
    FredPayloadError,
    FredRequest,
    fetch_series,
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
from modules.utility import KST_TIMEZONE

logger = logging.getLogger(__name__)

# 이 DAG가 쓰는 Airflow 연결 ID. 값이 아니라 이름만 정한다. 실제 접속 정보는
# `AIRFLOW_CONN_NEWS`가 갖는다.
CONNECTION_ID = "news"

# 설정 오류라 재시도해도 같은 결과인 HTTP 상태.
UNRECOVERABLE_STATUSES = frozenset({400, 401, 403, 404})


@dag(
    dag_id="fred_treasury_daily",
    schedule="30 7 * * 2-6",  # KST 화~토 07:30 = UTC 월~금 22:30
    start_date=pendulum.datetime(2026, 8, 4, tz=KST_TIMEZONE),  # KST 2026-08-04 00:00 = UTC 2026-08-03 15:00
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
    tags=["fred", "macro", "daily"],
)
def fred_treasury_daily():
    @task
    def collect(series_id: str) -> int:
        context = get_current_context()
        try:
            observation_start, observation_end = resolve_observation_period(context)
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

        try:
            response = fetch_series(SecretStr(api_key), request)
        except FredHTTPError as error:
            if error.status in UNRECOVERABLE_STATUSES:
                raise AirflowFailException(str(error)) from error
            if error.retry_after is not None:
                logger.warning("FRED asked to retry after %s seconds", error.retry_after)
            raise

        # 반환 타입은 provider 버전에 따라 psycopg2/psycopg3 래퍼로 갈린다. 런타임 객체는
        # 어느 쪽이든 PEP 249 연결이라 commit·rollback을 갖는다.
        connection: Any = PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()
        try:
            count = store_observations(connection, response)
            connection.commit()
        except FredPayloadError as error:
            connection.rollback()
            raise AirflowFailException(str(error)) from error
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        logger.info(
            "Stored %s %s observations for %s..%s",
            count,
            request.series_id,
            request.observation_start,
            request.observation_end,
        )
        return count

    collect.expand(series_id=list(TREASURY_SERIES))


fred_treasury_daily = fred_treasury_daily()
