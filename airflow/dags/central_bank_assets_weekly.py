"""중앙은행 대차대조표 주간 수집 DAG.

정책금리를 수집하는 중앙은행의 **대차대조표 잔액**을 `indicator_observation`에 쌓는다.
`indicator_observation`에 있던 것은 국채·CD·정책금리로 전부 **가격**이었고 **수량**이
없었다. 정책금리가 0에 붙어 있던 구간에서 통화정책의 강도를 가른 것은 금리가 아니라 자산
매입 규모였고, 2022년 이후의 QT도 잔액 없이는 숫자를 댈 수 없다.

대상은 중앙은행 여섯이고 계열은 일곱이다. 영란은행만 둘인 이유는 아래에 있다.

| 중앙은행 | 저장 `series_id` | 제공처 | 주기 | 단위 |
| --- | --- | --- | --- | --- |
| 연방준비제도 | `FEDASSETS_W` | FRED `WALCL` | 주(수요일 잔액) | 백만 달러 |
| 유로시스템 | `EAASSETS_W` | FRED `ECBASSETSW` | 주(금요일 잔액) | 백만 유로 |
| 일본은행 | `JPASSETS_M` | FRED `JPNASSETS` | 월(말잔) | 억엔 |
| 한국은행 | `KRASSETS_M` | ECOS `103Y002`/`BCAA1` | 월(말잔) | 십억원 |
| 분데스방크 | `DEASSETS_W` | BBK `BBBK11`/`D.TTA032` | 주(금요일 잔액) | 백만 유로 |
| 영란은행 | `GBASSETS_Q` | BoE IADB `RPQB75A` | **분기** | 백만 파운드 |
| 영란은행 | `GBRESERVES_W` | BoE IADB `RPWB56A` | 주(수요일 잔액) | 백만 파운드 |

**단위를 한 통화로 환산하지 않는다.** 환산하면 환율 변동이 자산 증감으로 위장한다. 2022년
엔 약세 구간에서 달러 환산 BOJ 자산은 줄고 엔화 잔액은 늘었다 — 두 값이 정반대 이야기를
한다. 나라 사이 비교는 잔액이 아니라 증가율(YoY)로 하고, 그 계산은 조회 쪽이 한다.

**증가율을 저장하지 않는다.** 주기가 주간 넷·월간 둘·분기 하나로 갈려서, 증가율을 저장하면
"무엇 대비"가 값에서 사라진다. 기준을 바꾸면 저장한 증가율에서는 다시 못 만들지만 잔액에서는
언제든 만든다. 국채 금리를 저장하고 금리차를 저장하지 않는 것과 같은 판단이다.

## 영란은행만 계열이 둘인 이유

**BoE는 총자산을 주간으로 고시하지 않는다.** 주간 Weekly Report(`RPW*`)는 발행권·준비금·
repo·채권보유·APF 대출·외환보유를 항목으로만 주고 총계 줄이 없다. BoE 자신이 "주간 보고는
대차대조표의 90% 이상"이라고 말하는 것이 총계가 아니라는 뜻이다.

주간 항목을 더해 총자산을 만들지 않는다. 그 값은 BoE가 고시한 값이 아니고, 90%만 담고 있어
다른 나라 총자산과 같은 자로 비교하면 조용히 적게 나온다. 대신 총자산(분기)과 준비금잔액
(주간)을 따로 저장하고 `indicator_series.kind`로 가른다 — `balance_sheet`와
`balance_sheet_item`이다. 한 종류로 두면 "중앙은행 총자산 전부"를 묻는 쿼리가 영국의
준비금을 총자산으로 읽는다.

## 왜 제공처마다 태스크인가

제공처가 넷이라 태스크도 넷이다. 하나가 실패해도 나머지 제공처는 저장되고, 재시도도 실패한
제공처만 다시 호출한다. 한 태스크 안에서 `if provider == ...`로 갈리지 않는다.

**`policy_rate_weekly`에 계열만 얹지 않았다.** 제공처와 주기가 겹치지만, 자산 수집 실패가
이미 성공한 정책금리 수집까지 죽이고 재시도가 정책금리를 다시 받는다. 되돌아볼 일수도
45일과 800일로 다르다(아래).

## 조회 구간을 정하는 규칙

구간은 다른 지표 DAG과 같은 순서로 정해진다.

1. `params.observation_start` / `params.observation_end`가 있으면 그 값을 그대로 쓴다.
2. 없으면 이 run의 시각(`data_interval_end`, 없으면 `dag_run.run_after`)을 KST 날짜로 바꾼
   값이 `observation_end`이고, `observation_start`는 거기서 `params.lookback_days - 1`일
   앞이다.

`lookback_days` 기본값은 **800**이다. 다른 지표 DAG(7일·45일)보다 훨씬 넓은데, 이 DAG이
받는 값 둘이 발표가 크게 밀려 있기 때문이다.

- **영란은행 총자산은 분기 고시에 17개월 지연이다**(2026-08-27 시점 최신값이 2025-03-31).
- **한국은행 총자산은 두 달 지연이다**(2026-08-27 시점 최신값이 2026-06).

45일 창으로는 둘 다 **행이 한 줄도 안 잡힌다.** 그때 제공처가 답하는 방식이 갈리는데 둘 다
나쁘다 — IADB는 CSV가 아니라 HTML 오류 페이지를 HTTP 200으로 줘서 태스크가 매주 죽고,
ECOS는 데이터 없음(`INFO-200`)으로 답해서 **조용한 0건**이 된다. 2026-08-28에 실제 호출로
확인한 것이 이것이고, 그래서 제공처마다 창을 다르게 두는 대신 하나를 넓게 잡는다.

800일이면 분기 경계가 여덟 번 들어가고 두 지연을 모두 덮는다. 매주 같은 구간을 다시 받지만
멱등 키가 `(provider, series_id, observation_date)`라 행이 늘지 않고, 일곱 계열을 합쳐도 한
실행이 쓰는 행은 800개 남짓이다.

**그러고도 0건이면 태스크를 죽인다.** 창이 이렇게 넓으면 0건은 "발표 전"이 아니라 제공처나
식별자가 바뀌었다는 뜻이다.

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `observation_start` | `null` | 조회 시작 관측일(YYYY-MM-DD). 주면 `lookback_days`를 무시한다 |
| `observation_end` | `null` | 조회 종료 관측일(YYYY-MM-DD). 주면 run 시각을 무시한다 |
| `lookback_days` | `800` | 구간을 지정하지 않을 때 되돌아볼 일수. 발표가 가장 밀린 계열에 맞춘 값이다 |

## 실행 방법

1. 주간 스케줄. 손댈 것 없다. KST 월요일 09:20에 돌면서 최근 800일을 다시 확인한다. 그
   시점이면 지난주 발표가 전부 끝나 있다 — 연준은 목요일 16:30 ET, 유로시스템·분데스방크는
   화요일, BoE 주간 보고는 목요일이다.

2. 과거 적재. 새 수집 코드 없이 구간만 넓게 준다.

       airflow dags trigger central_bank_assets_weekly \\
         --conf '{"observation_start": "1999-01-01", "observation_end": "2026-08-27"}'

   **이력 전체가 값어치를 갖는다.** 국채와 달리 자산 증가율은 그 자체로 통화정책 국면을
   가른다. 주간 넷이 27년, 월간 둘이 더 길지만 전부 합쳐 1만 행 미만이다.

## 실패와 재시도

**하나라도 실패하면 그 태스크를 죽인다.** 주 1회라 다음 실행이 같은 창을 다시 보긴 하지만
그게 한 주 뒤다. 그 사이 값이 비어 있는 것을 아무도 모르는 편보다 지금 멈추는 편이 낫다.
계열이 둘 이상인 제공처(FRED)는 항목별로 실패를 모아 이름과 사유를 함께 올린다.

- **0건도 실패다.** 800일 창에서 값이 하나도 없으면 발표 전이 아니라 제공처나 식별자가
  바뀐 것이다. FRED·ECOS는 계열마다, 분데스방크·BoE는 조회마다 센다. BoE는 한 조회가 계열
  둘을 함께 받으므로 **둘 다 비었을 때만** 잡힌다 — 준비금이 주간이라 사실상 총자산 쪽
  이력이 통째로 사라져야 걸린다.
- HTTP 400/401/403/404와 인증·식별자 문제는 `AirflowFailException`으로 즉시 실패한다.
- ECOS는 실패도 HTTP 200으로 답하므로 본문의 `RESULT.CODE`로 가른다. `INFO-200`(데이터 없음)은
  예외가 아니라 0건이고, 위의 0건 판정이 그것을 잡는다.
- BoE IADB는 값이 없는 구간과 잘못된 코드에 똑같이 HTML 오류 페이지를 HTTP 200으로 준다.
  800일 창이 그 둘을 가르는 장치다 — 그러고도 HTML이면 코드나 구간이 틀린 것이라 즉시
  실패한다.
- 분데스방크는 배수 표기(`unit multiplier`)가 `Millions`가 아니면 즉시 실패한다. 자릿수가
  조용히 1000배 어긋나는 것보다 멈추는 편이 낫다.
- 그 밖의 HTTP·네트워크 오류는 그대로 올려 재시도한다(2회, 1시간 간격).

## 필요한 환경

- `ECOS_API_KEY`, `FRED_API_KEY` 환경 변수. BoE와 분데스방크는 인증이 없다.
- `CONNECTION_ID`가 가리키는 Airflow 연결. 접속 정보는 `AIRFLOW_CONN_FINANCE`가 갖는다.

원본 응답은 `source_record`에, 유효 관측값은 `indicator_observation`에 저장한다. 테이블 정의의
원본은 백엔드의 `apps/models`이고, 이 DAG가 쓰는 SQL은 `airflow/sql/postgres/` 아래에 있다.
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

from modules.collectors.indicator.bbk_statement import (
    BbkStatementHTTPError,
    BbkStatementPayloadError,
    StatementRequest,
    fetch_statement,
)
from modules.collectors.indicator.bbk_statement import store_observations as store_statement_observations
from modules.collectors.indicator.boe import (
    BALANCE_SHEET_DATASET,
    BoeHTTPError,
    BoePayloadError,
    BoeRequest,
    fetch_curve,
)
from modules.collectors.indicator.boe import store_observations as store_boe_observations
from modules.collectors.indicator.ecos import BALANCE_SHEET_SERIES as ECOS_ASSET_SERIES
from modules.collectors.indicator.ecos import (
    EcosCollector,
    EcosHTTPError,
    EcosPayloadError,
    EcosRequest,
    EcosResultError,
)
from modules.collectors.indicator.fred import BALANCE_SHEET_SERIES as FRED_ASSET_SERIES
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

# 되돌아볼 일수. 다른 지표 DAG(7일·45일)보다 훨씬 넓은데, 발표가 크게 밀린 계열 둘에 맞춘
# 값이다 — 영란은행 총자산이 분기 고시에 17개월 지연, 한국은행 총자산이 두 달 지연이다
# (2026-08-28 실측). 좁게 잡으면 BoE는 HTML 오류 페이지로 매주 죽고 ECOS는 조용한 0건이 된다.
# 800일이면 분기 경계가 여덟 번 들어가고 둘을 모두 덮는다. 겹쳐 받는 것은 멱등 키가 흡수한다.
LOOKBACK_DAYS_ASSETS = 800

# ECOS는 실패도 HTTP 200에 `RESULT.CODE`로 알린다. `policy_rate_weekly`가 같은 판정을
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
        return resolve_observation_period(context, LOOKBACK_DAYS_ASSETS)
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
        raise AirflowFailException(f"{provider} balance sheet collection failed: {'; '.join(failures)}")


def require_observations(name: str, stored: int, observation_start: date, observation_end: date) -> None:
    """0건을 성공으로 넘기지 않는다.

    `LOOKBACK_DAYS_ASSETS`가 800일이라 이 창에는 가장 밀린 계열도 반드시 들어간다. 그런데도
    비었다면 발표 전이 아니라 제공처나 식별자가 바뀐 것이다. ECOS는 그 상태를 데이터 없음
    (`INFO-200`)으로 답해 예외를 내지 않으므로, 여기서 세지 않으면 조용한 성공이 된다.
    """
    if stored == 0:
        raise AirflowFailException(f"{name} returned no observations for {observation_start}..{observation_end}")


@dag(
    dag_id="central_bank_assets_weekly",
    dag_display_name="🏛️ 중앙은행 대차대조표 (FRED·ECOS·BBK·BoE)",
    description="미국·유로 지역·일본·한국·독일·영국 중앙은행의 총자산 잔액을 주 1회 받아 indicator_observation에 쌓는다.",
    schedule="20 9 * * 1",  # KST 월 09:20 = UTC 일 00:20
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
            LOOKBACK_DAYS_ASSETS,
            type="integer",
            minimum=1,
            title="되돌아볼 일수",
            description="구간을 지정하지 않을 때만 쓴다. 발표가 가장 밀린 계열(영국 분기 총자산)에 맞춘 값이다.",
        ),
    },
    doc_md=__doc__,
    tags=["balance-sheet", "macro", "weekly"],
)
def central_bank_assets_weekly():
    @task(task_display_name="미국·유로 지역·일본 (FRED)")
    def collect_fred() -> int:
        observation_start, observation_end = resolve_period()
        collector = FredCollector(SecretStr(require_env("FRED_API_KEY")))

        stored = 0
        failures: list[str] = []
        for series in FRED_ASSET_SERIES:
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
                        count = collector.store_observations(connection, response)
                except FredPayloadError as error:
                    raise AirflowFailException(str(error)) from error

            require_observations(series, count, observation_start, observation_end)
            stored += count

        require_no_failures("FRED", failures)
        logger.info("Stored %s FRED balance sheet observations for %s..%s", stored, observation_start, observation_end)
        return stored

    @task(task_display_name="한국 (ECOS)")
    def collect_ecos() -> int:
        observation_start, observation_end = resolve_period()
        collector = EcosCollector(SecretStr(require_env("ECOS_API_KEY")))

        stored = 0
        failures: list[str] = []
        for series in ECOS_ASSET_SERIES:
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
                        count = collector.store_observations(connection, response)
                except EcosResultError as error:
                    if is_unrecoverable_result(error.code):
                        raise AirflowFailException(str(error)) from error
                    failures.append(f"{series}({error})")
                    continue
                except EcosPayloadError as error:
                    raise AirflowFailException(str(error)) from error

            # ECOS는 데이터 없음을 `INFO-200`으로 답해 예외를 내지 않는다. 0건이 조용히 지나간다.
            require_observations(series, count, observation_start, observation_end)
            stored += count

        require_no_failures("ECOS", failures)
        logger.info("Stored %s ECOS balance sheet observations for %s..%s", stored, observation_start, observation_end)
        return stored

    @task(task_display_name="독일 (분데스방크)")
    def collect_bbk() -> int:
        observation_start, observation_end = resolve_period()
        request = StatementRequest(observation_start=observation_start, observation_end=observation_end)

        try:
            response = fetch_statement(request)
        except BbkStatementHTTPError as error:
            if error.status in UNRECOVERABLE_STATUSES:
                raise AirflowFailException(str(error)) from error
            if error.retry_after is not None:
                logger.warning("Bundesbank asked to retry after %s seconds", error.retry_after)
            raise

        with closing(PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()) as connection:
            try:
                with atomic(connection):
                    count = store_statement_observations(connection, response)
            except BbkStatementPayloadError as error:
                # 배수 표기가 바뀐 것도 여기 걸린다. 제공처 형식 문제라 재시도해도 같다.
                raise AirflowFailException(str(error)) from error

        require_observations("DEASSETS_W", count, observation_start, observation_end)
        logger.info("Stored %s Bundesbank balance sheet observations for %s..%s", count, observation_start, observation_end)
        return count

    @task(task_display_name="영국 (BoE)")
    def collect_boe() -> int:
        observation_start, observation_end = resolve_period()
        request = BoeRequest(
            dataset=BALANCE_SHEET_DATASET,
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
                    count = store_boe_observations(connection, response)
            except BoePayloadError as error:
                # HTML 오류 페이지도 여기 걸린다. 둘 다 파라미터나 제공처 형식 문제라 재시도해도 같다.
                raise AirflowFailException(str(error)) from error

        # 한 조회가 계열 둘을 함께 받으므로 이 판정은 둘 다 비었을 때만 걸린다.
        require_observations("BoE balance sheet", count, observation_start, observation_end)
        logger.info("Stored %s BoE balance sheet observations for %s..%s", count, observation_start, observation_end)
        return count

    collect_fred()
    collect_ecos()
    collect_bbk()
    collect_boe()


central_bank_assets_weekly = central_bank_assets_weekly()
