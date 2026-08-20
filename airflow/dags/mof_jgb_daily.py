"""일본 재무성 국채 금리(JGB) 일별 수집 DAG.

태스크는 하나다. 재무성 CSV 한 파일이 곡선 전체를 담고 있어 시계열마다 요청할 일이 없다.
`fred_treasury_daily`, `ecos_market_rate_daily`가 시계열마다 태스크를 매핑하는 것과 다르다.
수집 대상은 `modules.collectors.mof.JgbSeries`가 정한다(현재 `JGB2Y`, `JGB5Y`, `JGB10Y`,
`JGB20Y`, `JGB30Y`, `JGB40Y`). 시계열을 늘려도 이 파일은 바뀌지 않는다.

재무성이 주는 열은 1~40년 열다섯 개지만 실제 입찰 발행되는 연한만 저장한다. 나머지는 발행
종목이 없는 곡선 위의 값이라 시장이 인용하지 않는다.

미국 국채를 받는 `fred_treasury_daily`, 국내 시장금리를 받는 `ecos_market_rate_daily`와
같은 테이블에 쌓이며 `provider`로 갈린다.

인증이 없다. API 키도 등록도 필요 없고 환경 변수도 `AIRFLOW_CONN_FINANCE` 하나면 된다.

스케줄과 조회 기간은 한국 시간(KST) 기준이다. 일본(JST)과 offset이 같아 날짜 경계가 어긋나지
않는다. 저장하는 시각(`started_at`, `completed_at`)은 그대로 UTC다.

## 파일이 두 개로 나뉜다

    jgbcm.csv            이번 달치만. 매달 1일에 비워진다
    data/jgbcm_all.csv   1974-09-24부터 지난달 말까지. 이번 달은 없다

**어느 한쪽도 최근 며칠과 과거를 함께 담지 못한다.** 조회 구간이 달 경계를 넘으면 둘 다
받아야 하고, 그 판단은 `modules.collectors.mof.fetch_curves`가 한다. 받은 파일마다
`source_record`가 한 행씩 생기므로 매달 초 며칠은 한 run이 레코드를 두 개 남긴다.
정상이다.

## 조회 구간을 정하는 규칙

`observation_start..observation_end` 구간은 아래 순서로 정해진다. 규칙은
`modules.period.resolve_observation_period`에 있고 세 수집 DAG가 함께 쓴다.

1. `params.observation_start` / `params.observation_end`가 있으면 그 값을 그대로 쓴다.
2. 없으면 이 run의 시각에서 계산한다. 기준 시각은 `data_interval_end`이고, 수동 run처럼
   data interval이 없으면 `dag_run.run_after`를 쓴다. 그 시각을 KST로 바꾼 날짜가
   `observation_end`이고, `observation_start`는 거기서 `params.lookback_days - 1`일 앞이다.

`lookback_days` 기본값은 7이다. 일별 스케줄이 매번 최근 7일을 다시 조회한다는 뜻이다.
휴일과 발표 지연을 별도 캘린더 없이 흡수하려는 장치다. 멱등 키가
`(provider, series_id, observation_date)`라서 같은 날짜를 다시 받아도 행이 늘지 않고 최신
값으로 갱신된다.

파일은 언제나 통째로 받고 구간 밖의 행은 저장 전에 버린다. 그래서 되돌아본 일수는 요청
크기가 아니라 저장 범위를 정한다.

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `observation_start` | `null` | 조회 시작 기준일(YYYY-MM-DD). 주면 `lookback_days`를 무시한다 |
| `observation_end` | `null` | 조회 종료 기준일(YYYY-MM-DD). 주면 run 시각을 무시한다 |
| `lookback_days` | `7` | 구간을 지정하지 않을 때 되돌아볼 일수. `1`이면 그 run의 하루만 |
| `source_file` | `auto` | 받을 파일. `auto`가 구간을 보고 정한다. `current`나 `all`로 고정할 수 있다 |

날짜는 재무성 고시 기준일, 즉 **일본 영업일**이다. 주말과 일본 공휴일에는 행이 아예 없다.

## 실행 방법

1. 일별 스케줄. 손댈 것 없다. KST 화~토 08:20에 돌면서 최근 7일을 다시 확인한다.

2. 과거 대량 적재. 파일을 통째로 받으므로 run 하나가 구간 전체를 처리한다. 전 구간을 한
   번에 넣으면 관측값이 8만 행에 가까워 한 트랜잭션이 커진다. 5년 단위로 자른다.

       airflow dags trigger mof_jgb_daily \\
         --conf '{"source_file": "all", "observation_start": "2020-01-01", "observation_end": "2024-12-31"}'

   1974-09-24 이전을 물으면 그런 데이터가 없다는 뜻이라 즉시 실패한다.

3. 구간을 정확히 맞춘 백필. run 하나가 자기 날짜 하루만 저장한다. 매 run이 파일을 다시
   받으므로 긴 구간에는 2번이 낫다.

       airflow backfill create --dag-id mof_jgb_daily \\
         --from-date 2026-06-01 --to-date 2026-07-31 \\
         --dag-run-conf '{"lookback_days": 1}'

4. 하루만 확인. 수동 run은 data interval이 없어 `run_after`로 계산하므로, 날짜를 확실히
   하려면 직접 넘긴다.

       airflow dags test mof_jgb_daily \\
         --conf '{"observation_start": "2026-08-06", "observation_end": "2026-08-06"}'

백필 run이 `queued`에서 안 넘어가면 그 backfill이 pause됐는지 본다. scheduler는 pause된
backfill의 dag_run을 running으로 올리지 않는다. 태스크를 clear해도 소용없다.

## 실패와 재시도

- HTTP 400/401/403/404: 경로가 바뀌었거나 차단된 것이라 즉시 실패한다. 그 밖의 HTTP·네트워크
  오류는 재시도한다(2회, 1시간 간격).
- CSV 열 구성이 바뀌었거나 기준일 표기가 바뀌면 즉시 실패한다. 이때는 아무 것도 쓰지 않는다.
  값이 조용히 옆 칸으로 밀린 채 저장되는 것보다 멈추는 편이 낫다.
- 받은 파일이 요청 구간의 시작을 못 덮으면 즉시 실패한다. 자동 선택에서는 1974-09-24보다
  이른 구간을 물었을 때만 난다. `source_file`을 직접 지정했다면 그 지정이 틀린 것이다.

## 필요한 환경

- `CONNECTION_ID`가 가리키는 Airflow 연결. 접속 정보는 `AIRFLOW_CONN_FINANCE`가 갖는다.
- 인증 정보는 없다.

원본 CSV는 jsonb 컬럼에 넣지 않으므로 `source_record.payload`는 비어 있다. 어느 파일이 어느
구간을 담고 있었는지는 `source_record.metadata`가 남긴다. 유효 관측값은
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

from modules.collectors.mof import (
    MofFile,
    MofHTTPError,
    MofPayloadError,
    MofRequest,
    fetch_curves,
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

# 받을 파일을 고정하는 run 파라미터. 기본값은 구간을 보고 수집기가 정하게 두는 것이다.
SOURCE_FILE_PARAM = "source_file"
AUTO_FILE = "auto"


def resolve_source_file(value: object) -> MofFile | None:
    """`source_file` 파라미터를 파일 선택으로 바꾼다. `auto`면 수집기가 정하도록 None을 준다."""
    text = str(value or AUTO_FILE)
    if text == AUTO_FILE:
        return None
    try:
        return MofFile(text)
    except ValueError as error:
        choices = ", ".join((AUTO_FILE, *(file.value for file in MofFile)))
        raise AirflowFailException(f"{SOURCE_FILE_PARAM} must be one of {choices}") from error


@dag(
    dag_id="mof_jgb_daily",
    dag_display_name="🇯🇵 일본 국채 금리 (재무성 JGB)",
    description="일본 재무성 CSV에서 JGB 만기별 금리를 매일 받아 market.indicator_observation에 쌓는다.",
    schedule="20 8 * * 2-6",  # KST 화~토 08:20 = UTC 월~금 23:20
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
        SOURCE_FILE_PARAM: Param(
            AUTO_FILE,
            type="string",
            enum=[AUTO_FILE, MofFile.CURRENT.value, MofFile.ALL.value],
            title="받을 파일",
            description="auto는 구간을 보고 정한다. current는 이번 달치, all은 1974년부터 지난달 말까지다.",
        ),
    },
    doc_md=__doc__,
    tags=["mof", "macro", "daily"],
)
def mof_jgb_daily():
    @task(task_display_name="JGB 수집·저장")
    def collect() -> int:
        context = get_current_context()
        try:
            observation_start, observation_end = resolve_observation_period(context)
        except PeriodError as error:
            raise AirflowFailException(str(error)) from error

        params = context.get("params") or {}
        request = MofRequest(
            observation_start=observation_start,
            observation_end=observation_end,
            file=resolve_source_file(params.get(SOURCE_FILE_PARAM)),
        )

        try:
            responses = fetch_curves(request)
        except MofHTTPError as error:
            if error.status in UNRECOVERABLE_STATUSES:
                raise AirflowFailException(str(error)) from error
            if error.retry_after is not None:
                logger.warning("MOF asked to retry after %s seconds", error.retry_after)
            raise
        except MofPayloadError as error:
            # 커버리지 부족도 여기 걸린다. 둘 다 파라미터나 제공처 형식 문제라 재시도해도 같다.
            raise AirflowFailException(str(error)) from error

        with closing(PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()) as connection:
            try:
                with atomic(connection):
                    # 파일이 여럿이면 한 트랜잭션에 함께 넣는다. 한쪽만 커밋되면 구간에 구멍이 남는다.
                    count = sum(store_observations(connection, response) for response in responses)
            except MofPayloadError as error:
                raise AirflowFailException(str(error)) from error

        logger.info(
            "Stored %s JGB observations for %s..%s from %s",
            count,
            request.observation_start,
            request.observation_end,
            ", ".join(response.file.filename for response in responses),
        )
        return count

    collect()


mof_jgb_daily = mof_jgb_daily()
