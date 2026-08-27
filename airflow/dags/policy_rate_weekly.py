"""중앙은행 정책금리 주간 수집 DAG.

국채를 수집하는 나라의 **중앙은행이 정하는 값**을 `indicator_observation`에 쌓는다. 이
테이블에 있던 것은 국채와 CD처럼 전부 시장이 만드는 값이라, 그 값들이 무엇을 기준으로
움직이는지를 말해 주는 축이 없었다. `KTB10Y - KRBASE` 같은 금리차와 정책금리 변경일 전후의
선반영을 여기 쌓인 값으로 낸다.

대상은 중앙은행 다섯이다. 국채는 아홉 나라에서 받지만 통화정책은 독일·프랑스·이탈리아·
스페인이 ECB 하나로 묶여서, 유로 지역은 `country='XM'` 한 계열이다.

| 중앙은행 | 저장 `series_id` | 제공처 | 주기 |
| --- | --- | --- | --- |
| 한국은행 | `KRBASE` | ECOS `722Y001` | 일별(달력일 전부) |
| 일본은행 | `JPBASE_M` | ECOS `902Y006` | **월별** |
| 연방준비제도 | `DFEDTARU` | FRED | 일별 |
| 유럽중앙은행 | `EADFR` | FRED(`ECBDFR`) | 일별 |
| 영란은행 | `GBBASE` | BoE IADB `IUDBEDR` | 일별(영업일) |

각 중앙은행에서 **대표값 하나만** 받는다. ECB는 예금금리(DFR), 연준은 목표범위 상단이다.
답해야 하는 질문이 "정책금리가 언제 얼마나 바뀌었나"이고 나머지 고시값은 그 질문에 값을
더하지 않는다. 늘리는 것은 수집기 Enum과 마스터 시드 한 줄씩이다.

## 왜 제공처마다 태스크인가

제공처가 셋이라 태스크도 셋이다. 하나가 실패해도 나머지 제공처는 저장되고, 재시도도 실패한
제공처만 다시 호출한다. 한 태스크 안에서 `if provider == ...`로 갈리지 않는다.

**기존 수집 DAG에 계열만 얹지 않았다.** `boe_gilt_daily`와 `ecb_yield_curve_daily`는 태스크
하나가 요청 하나인 형태라, 정책금리를 얹으면 그 실패가 이미 성공한 국채 곡선 태스크까지
죽이고 재시도가 곡선을 다시 받는다. 되돌아볼 일수도 국채 쪽(7일·190일)과 뜻이 다르고,
스케줄은 그 제공처의 국채 고시 시각에 맞춰져 있다.

## 조회 구간을 정하는 규칙

구간은 다른 지표 DAG과 같은 순서로 정해진다.

1. `params.observation_start` / `params.observation_end`가 있으면 그 값을 그대로 쓴다.
2. 없으면 이 run의 시각(`data_interval_end`, 없으면 `dag_run.run_after`)을 KST 날짜로 바꾼
   값이 `observation_end`이고, `observation_start`는 거기서 `params.lookback_days - 1`일 앞이다.

`lookback_days` 기본값은 **45**다. 정책금리는 통화정책 회의 때만 바뀌므로 주 1회로 충분하고,
창을 넓게 잡아 실행이 한두 번 밀려도 구멍이 안 생긴다. 멱등 키가
`(provider, series_id, observation_date)`라 겹쳐 받아도 행이 늘지 않는다.

월별인 일본은 이 구간을 `YYYYMM`으로 바꿔 요청하고 관측일은 **그 달의 1일**이다. 45일이면
달 경계가 반드시 하나 들어간다.

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `observation_start` | `null` | 조회 시작 관측일(YYYY-MM-DD). 주면 `lookback_days`를 무시한다 |
| `observation_end` | `null` | 조회 종료 관측일(YYYY-MM-DD). 주면 run 시각을 무시한다 |
| `lookback_days` | `45` | 구간을 지정하지 않을 때 되돌아볼 일수 |

## 실행 방법

1. 주간 스케줄. 손댈 것 없다. KST 월요일 09:00에 돌면서 최근 45일을 다시 확인한다.

2. 과거 적재. 새 수집 코드 없이 구간만 넓게 준다.

       airflow dags trigger policy_rate_weekly \\
         --conf '{"observation_start": "2020-01-01", "observation_end": "2026-08-27"}'

   **국채 이력이 있는 구간까지만 값어치가 있다.** 비교할 국채가 없는 구간의 정책금리는
   그 자체로는 답하는 질문이 없다.

## 실패와 재시도

**하나라도 실패하면 그 태스크를 죽인다.** 주 1회라 다음 실행이 같은 창을 다시 보긴 하지만
그게 한 주 뒤다. 그 사이 값이 비어 있는 것을 아무도 모르는 편보다 지금 멈추는 편이 낫다.
계열이 둘인 제공처(ECOS·FRED)는 항목별로 실패를 모아 이름과 사유를 함께 올린다.

- HTTP 400/401/403/404와 인증·식별자 문제는 `AirflowFailException`으로 즉시 실패한다.
- ECOS는 실패도 HTTP 200으로 답하므로 본문의 `RESULT.CODE`로 가른다. `INFO-200`(데이터 없음)은
  실패가 아니라 0건이다.
- BoE IADB는 값이 없는 구간과 잘못된 코드에 똑같이 HTML 오류 페이지를 HTTP 200으로 준다.
  수집기가 조회 구간 앞에 패딩을 붙이므로, 그러고도 HTML이면 코드나 구간이 틀린 것이라
  즉시 실패한다.
- 그 밖의 HTTP·네트워크 오류는 그대로 올려 재시도한다(2회, 1시간 간격).

## 필요한 환경

- `ECOS_API_KEY`, `FRED_API_KEY` 환경 변수. BoE는 인증이 없다.
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

from modules.collectors.indicator.boe import (
    POLICY_DATASET,
    BoeHTTPError,
    BoePayloadError,
    BoeRequest,
    fetch_curve,
    store_observations,
)
from modules.collectors.indicator.ecos import POLICY_RATE_SERIES as ECOS_POLICY_SERIES
from modules.collectors.indicator.ecos import (
    EcosCollector,
    EcosHTTPError,
    EcosPayloadError,
    EcosRequest,
    EcosResultError,
)
from modules.collectors.indicator.fred import POLICY_RATE_SERIES as FRED_POLICY_SERIES
from modules.collectors.indicator.fred import (
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

# 되돌아볼 일수. 주 1회 실행이 한두 번 밀려도 구멍이 안 생기게 넓게 잡는다. 국채 쪽 기본값
# (7일)과 뜻이 다르므로 여기서 따로 갖는다.
LOOKBACK_DAYS_POLICY = 45

# ECOS는 실패도 HTTP 200에 `RESULT.CODE`로 알린다. `ecos_market_rate_daily`가 같은 판정을
# 갖고 있다. DAG끼리 import하지 않으므로 두 벌이고, 한쪽을 고치면 다른 쪽도 함께 본다.
INVALID_KEY_CODE = "INFO-100"
UNRECOVERABLE_RESULT_PREFIXES = ("ERROR-1", "ERROR-2", "ERROR-3", "ERROR-4")


def is_unrecoverable_result(code: str) -> bool:
    """이 `RESULT.CODE`가 재시도로 풀리지 않는 오류인지."""
    return code == INVALID_KEY_CODE or code.startswith(UNRECOVERABLE_RESULT_PREFIXES)


def resolve_period():
    """이 run이 저장할 관측 구간. 파라미터 문제는 재시도해도 같으므로 즉시 실패시킨다."""
    context = get_current_context()
    try:
        return resolve_observation_period(context, LOOKBACK_DAYS_POLICY)
    except PeriodError as error:
        raise AirflowFailException(str(error)) from error


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise AirflowFailException(f"{name} is required")
    return value


def require_no_failures(provider: str, failures: list[str]) -> None:
    """계열 하나라도 실패했으면 태스크를 죽인다.

    주 1회라 다음 실행이 곧 같은 창을 다시 보지 않는다(한 주 뒤다). 사유에 쉼표가 들어가므로
    구분자는 `;`다.
    """
    if failures:
        raise AirflowFailException(f"{provider} policy rate collection failed: {'; '.join(failures)}")


@dag(
    dag_id="policy_rate_weekly",
    dag_display_name="🏦 중앙은행 정책금리 (ECOS·FRED·BoE)",
    description="한국·일본·미국·유로 지역·영국의 중앙은행 정책금리를 주 1회 받아 indicator_observation에 쌓는다.",
    schedule="0 9 * * 1",  # KST 월 09:00 = UTC 일 00:00
    start_date=pendulum.datetime(2026, 8, 31, tz=KST_TIMEZONE),  # KST 2026-08-31 00:00 = UTC 2026-08-30 15:00
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
            LOOKBACK_DAYS_POLICY,
            type="integer",
            minimum=1,
            title="되돌아볼 일수",
            description="구간을 지정하지 않을 때만 쓴다. 정책금리는 회의 때만 바뀌므로 넓게 잡는다.",
        ),
    },
    doc_md=__doc__,
    tags=["policy-rate", "macro", "weekly"],
)
def policy_rate_weekly():
    @task(task_display_name="한국·일본 (ECOS)")
    def collect_ecos() -> int:
        observation_start, observation_end = resolve_period()
        collector = EcosCollector(SecretStr(require_env("ECOS_API_KEY")))

        stored = 0
        failures: list[str] = []
        for series in ECOS_POLICY_SERIES:
            request = EcosRequest(
                series=series,
                observation_start=observation_start,
                observation_end=observation_end,
            )
            try:
                response = collector.fetch_series(request)
            except EcosHTTPError as error:
                if error.status in UNRECOVERABLE_STATUSES:
                    raise AirflowFailException(str(error)) from error
                if error.retry_after is not None:
                    logger.warning("ECOS asked to retry after %s seconds", error.retry_after)
                failures.append(f"{series}({error})")
                continue

            with closing(PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()) as connection:
                try:
                    with atomic(connection):
                        stored += collector.store_observations(connection, response)
                except EcosResultError as error:
                    if is_unrecoverable_result(error.code):
                        raise AirflowFailException(str(error)) from error
                    failures.append(f"{series}({error})")
                except EcosPayloadError as error:
                    raise AirflowFailException(str(error)) from error

        require_no_failures("ECOS", failures)
        logger.info("Stored %s ECOS policy rate observations for %s..%s", stored, observation_start, observation_end)
        return stored

    @task(task_display_name="미국·유로 지역 (FRED)")
    def collect_fred() -> int:
        observation_start, observation_end = resolve_period()
        collector = FredCollector(SecretStr(require_env("FRED_API_KEY")))

        stored = 0
        failures: list[str] = []
        for series in FRED_POLICY_SERIES:
            request = FredRequest(
                series_id=series,
                observation_start=observation_start,
                observation_end=observation_end,
            )
            try:
                response = collector.fetch_series(request)
            except FredHTTPError as error:
                if error.status in UNRECOVERABLE_STATUSES:
                    raise AirflowFailException(str(error)) from error
                if error.retry_after is not None:
                    logger.warning("FRED asked to retry after %s seconds", error.retry_after)
                failures.append(f"{series}({error})")
                continue

            with closing(PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()) as connection:
                try:
                    with atomic(connection):
                        stored += collector.store_observations(connection, response)
                except FredPayloadError as error:
                    raise AirflowFailException(str(error)) from error

        require_no_failures("FRED", failures)
        logger.info("Stored %s FRED policy rate observations for %s..%s", stored, observation_start, observation_end)
        return stored

    @task(task_display_name="영국 (BoE)")
    def collect_boe() -> int:
        observation_start, observation_end = resolve_period()
        request = BoeRequest(
            dataset=POLICY_DATASET,
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

        logger.info("Stored %s BoE policy rate observations for %s..%s", count, observation_start, observation_end)
        return count

    collect_ecos()
    collect_fred()
    collect_boe()


policy_rate_weekly = policy_rate_weekly()
