"""국내 심리·경기 지수 월별 수집 DAG.

지금까지 한국 매크로는 금리(ECOS)와 수출입(관세청)뿐이었다. 지수가 빠진 날 "금리가 올라서"는
말할 수 있고 **"체감 경기가 꺾여서"는 말할 근거가 없었다.** 이 DAG가 그 축을 채운다.
설계는 `docs/collection/korea-industry-macro-expansion.md` 3단계다.

세 계열을 받는다.

| 계열 | 무엇 | 통계표 |
| --- | --- | --- |
| `CSI_M` | 소비자심리지수 | `511Y002` 소비자동향조사 |
| `BSI_M` | 전산업 업황실적BSI | `512Y015` 기업경기조사(실적) |
| `LEADING_M` | 선행종합지수 | `901Y067` 경기종합지수 |

수집 대상은 `modules.collectors.indicator.ecos.SENTIMENT_SERIES`가 정한다. 계열을 늘려도
이 파일은 바뀌지 않는다.

## 왜 별도 DAG인가

**`ecos_market_rate_daily`에 넣지 않는다.** 셋 다 월간 계열이라 일별 DAG에 태우면 매일 같은
값을 다시 쓴다. `policy_rate_weekly`가 정책금리를 따로 떼어 둔 것과 같은 판단이고, 그쪽은
주간이라 월간인 이것과도 갈린다.

## 조회 창이 넓다

`lookback_days` 기본값이 **120**이다. 월간 통계는 기준월이 끝난 뒤에 나온다 — 실측
2026-09-04 기준 선행종합지수의 최신값이 2026-07이고 BSI는 2026-08이다. 창이 좁으면 발표가
한 달만 밀려도 조용한 0건이 된다. 멱등 키가 `(provider, series_id, observation_date)`라
같은 달을 다시 받아도 행이 늘지 않는다.

월간 관측일은 **그 달의 1일**이다(`parse_time`). `fred.py`·`ecb_irs.py`의 월간 계열과 같은
규약이라 조회하는 쪽이 주기별로 다른 날짜 규칙을 알 필요가 없다.

## 축이 둘인 통계표

소비자동향조사는 (조사항목 `FME`, 대상 `99988`)이고 기업경기조사는 (산업 `99988`,
조사항목 `AA`)다. **순서가 서로 다르고, 하나만 넘기면 ECOS가 오류가 아니라 데이터 없음
(`INFO-200`)으로 답한다.** 좌표는 `EcosSeries`가 들고 있고
`tests/collectors/test_ecos.py`가 그것을 잠근다.

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `observation_start` | `null` | 조회 시작 관측일(YYYY-MM-DD). 주면 `lookback_days`를 무시한다 |
| `observation_end` | `null` | 조회 종료 관측일(YYYY-MM-DD). 비우면 이 run 시각의 KST 날짜 |
| `lookback_days` | `120` | 구간을 지정하지 않을 때 되돌아볼 일수 |

과거 대량 적재는 한 번의 호출로 끝난다. ECOS가 한 요청에 10만 건까지 준다.

    airflow dags trigger ecos_sentiment_monthly \\
      --conf '{"observation_start": "2008-09-01", "observation_end": "2026-08-31"}'

## 실패와 재시도

**항목별 실패 수집**이다. 계열 하나가 실패해도 나머지는 저장하고 마지막에 판정한다.

- **하나라도 실패하면 태스크를 죽인다.** 월 1회 발표를 주 1회 확인하는 자리라 다음 실행이
  곧 같은 창을 다시 보지 않는다.
- **전부 0건이면 실패다.** 창이 120일이라 세 계열 어느 것도 이 안에 관측이 없을 수 없다.
  비었다면 발표 전이 아니라 항목코드나 통계표가 바뀐 것이고, ECOS는 그 상태를 데이터 없음
  (`INFO-200`)으로 답해 예외를 내지 않는다. 여기서 세지 않으면 매주 "성공, 0건"이 된다.
- `INFO-100`(인증키 무효)과 `ERROR-1xx~4xx`(요청 인자 문제)는 즉시 실패한다.
- HTTP 400/401/403/404도 즉시 실패한다. 그 밖의 HTTP·네트워크 오류는 재시도한다.

## 필요한 환경

- `ECOS_API_KEY`. Airflow가 읽는 건 `compose/local/airflow/.env`다.
- `CONNECTION_ID`가 가리키는 Airflow 연결. 접속 정보는 `AIRFLOW_CONN_FINANCE`가 갖는다.
"""

import logging
import os
from contextlib import closing
from datetime import date, timedelta

import pendulum
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Param, dag, get_current_context, task
from airflow.sdk.exceptions import AirflowFailException
from pydantic import SecretStr

from modules.collectors.indicator.ecos import (
    SENTIMENT_SERIES,
    EcosCollector,
    EcosHTTPError,
    EcosPayloadError,
    EcosRequest,
    EcosResultError,
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

# 월간 통계는 기준월이 끝난 뒤에 나온다. 실측 2026-09-04 기준 선행종합지수 최신값이 2026-07,
# BSI가 2026-08이다. 창이 좁으면 발표가 한 달 밀린 것이 조용한 0건이 된다.
LOOKBACK_DAYS_SENTIMENT = 120

# 인증키가 유효하지 않다는 응답. 키를 고치기 전에는 재시도해도 같다.
INVALID_KEY_CODE = "INFO-100"

# 요청 인자를 고쳐야 하는 오류 대역. `ecos_market_rate_daily`와 같은 판단이다.
UNRECOVERABLE_RESULT_PREFIXES = ("ERROR-1", "ERROR-2", "ERROR-3", "ERROR-4")


def is_unrecoverable_result(code: str) -> bool:
    """이 `RESULT.CODE`가 재시도로 풀리지 않는 오류인지."""
    return code == INVALID_KEY_CODE or code.startswith(UNRECOVERABLE_RESULT_PREFIXES)


def resolve_period() -> tuple[date, date]:
    """이 run이 저장할 관측 구간. 파라미터 문제는 재시도해도 같으므로 즉시 실패시킨다."""
    context = get_current_context()
    try:
        return resolve_observation_period(context, LOOKBACK_DAYS_SENTIMENT)
    except PeriodError as error:
        raise AirflowFailException(str(error)) from error


@dag(
    dag_id="ecos_sentiment_monthly",
    dag_display_name="🇰🇷 국내 심리·경기 지수 (ECOS)",
    description="한국은행 ECOS에서 소비자심리지수·업황실적BSI·선행종합지수를 주 1회 받아 indicator_observation에 쌓는다.",
    schedule="0 10 * * 1",  # KST 월 10:00 = UTC 일 01:00
    start_date=pendulum.datetime(2026, 9, 7, tz=KST_TIMEZONE),  # KST 2026-09-07 00:00 = UTC 2026-09-06 15:00
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
            LOOKBACK_DAYS_SENTIMENT,
            type="integer",
            minimum=1,
            title="되돌아볼 일수",
            description=(
                "구간을 지정하지 않을 때만 쓴다. 월간 통계라 발표가 한두 달 밀린다 — "
                "좁게 잡으면 아직 안 나온 달만 물어보게 되어 조용한 0건이 된다."
            ),
        ),
    },
    doc_md=__doc__,
    tags=["ecos", "macro", "korea", "sentiment", "monthly"],
)
def ecos_sentiment_monthly():
    @task(task_display_name="심리·경기 지수")
    def collect_sentiment() -> int:
        observation_start, observation_end = resolve_period()

        api_key = os.environ.get("ECOS_API_KEY")
        if not api_key:
            raise AirflowFailException("ECOS_API_KEY is required")
        collector = EcosCollector(SecretStr(api_key))

        stored = 0
        failures: list[str] = []
        for series in SENTIMENT_SERIES:
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

        # 사유에 쉼표가 들어가므로 구분자는 `;`다.
        if failures:
            raise AirflowFailException(f"ECOS sentiment collection failed: {'; '.join(failures)}")

        # **0건은 성공이 아니다.** 창이 120일이라 세 계열 어느 것도 이 안에 관측이 없을 수 없다.
        # ECOS는 잘못된 항목코드에도 데이터 없음으로 답하므로 여기서 세지 않으면 매주
        # "성공, 0건"이 된다. `policy_rate_weekly`와 같은 판정이다.
        if stored == 0:
            raise AirflowFailException(
                f"ECOS returned no sentiment observations for {observation_start}..{observation_end}"
            )

        logger.info("Stored %s ECOS sentiment observations for %s..%s", stored, observation_start, observation_end)
        return stored

    collect_sentiment()


ecos_sentiment_monthly = ecos_sentiment_monthly()
