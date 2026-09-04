"""산업 대표 20사 분기 실적 수집 DAG.

**목적은 개별 기업 분석이 아니라 한국 거시 지표다.** 대상 스무 곳은 투자 후보 목록이 아니라
한국 경제의 표본이고, 그들의 매출·영업이익·순이익이 함께 꺾이면 그것을 "한국 기업 이익
사이클이 꺾였다"로 읽는다. 설계는 `docs/collection/korea-industry-macro-expansion.md`다.

결과는 `earnings_fact`에 `release_type='periodic'`으로 쌓인다.

## 호출 하나가 스무 곳이다

`fnlttMultiAcnt.json`은 회사 고유번호를 콤마로 이어 받는다. 한 번에 621행이 오고 회사가
스무 곳 다 들어 있다(2026-09-04 실측). 그래서 한 기간이 호출 하나이고, 네 기간을 훑어도
하루 4콜이다. `dart_disclosure_intraday`가 회사마다 목록을 부르는 것과 다르다.

## 대상은 DB가 정한다

`instrument.filing_entity_id`가 채워진 행이 대상이다. **`is_watched`(시세 대상)와 다른
축이다** — 공시·실적은 받지만 시세는 안 받는 종목이 있고, 둘을 한 플래그로 묶으면 대상을
늘릴 때 분봉·수급·실시간 구독까지 함께 끌려온다.

## 왜 매번 네 기간인가

정정 공시로 지나간 분기의 숫자가 바뀐다. 그때 새 접수번호로 오므로 지난 기간도 계속 봐야
집을 수 있다. 자연키가 `(provider, rcept_no, statement_scope, amount_basis, metric)`이라
같은 값을 다시 받아도 행이 늘지 않는다.

## 연결과 별도를 둘 다 저장한다

응답이 `CFS`(연결)와 `OFS`(별도)를 함께 준다. 한쪽을 고르지 않는다 — 자연키에
`statement_scope`가 들어 있어 서로 덮지 않고, 어느 쪽을 볼지는 읽는 쪽이 정한다.
`fnlttSinglAcntAll` 경로가 연결을 우선하고 없을 때만 별도를 쓰는 것과 갈리는 지점인데,
저기는 범위를 지정해 두 번 부르지만 여기는 한 응답에 둘이 함께 온다.

## 계정을 이름으로 잡는다

**이 API는 `account_id`를 주지 않는다**(2026-09-04 실측). 그래서 `MULTI_ACCOUNT_METRICS`
대응표가 계정명으로 잡고 표에 없는 이름은 버린다. 금융·증권사는 `영업이익(손실)`로 오고
**삼성생명·KB금융은 매출액 행이 아예 없다** — 0으로 채우지 않고 행을 만들지 않는다.

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `report_periods` | `4` | 훑을 분기 수. 오래된 기간을 백필할 때 늘린다 |

    airflow dags trigger dart_multi_earnings_quarterly --conf '{"report_periods": 12}'

## 실패와 재시도

**항목별 실패 수집**이다. 항목은 회사가 아니라 기간이다.

- **한 기간이 실패해도 나머지 기간은 저장하고, 마지막에 하나라도 실패했으면 태스크를
  실패시킨다.** 하루 한 번 도는 수집이라 그날 같은 기간을 다시 보는 실행이 없다.
- **데이터 없음(`013`)은 실패가 아니다.** 정기보고서는 기간 종료 뒤 45일(사업보고서는 90일)
  까지 제출하므로 가장 최근 기간은 아직 없는 것이 정상이다.
- **네 기간이 전부 0건이면 실패다.** 직전 네 분기 중 최소 셋은 이미 공시돼 있어야 한다.
  0건만 이어지는 것은 대상 목록이 비었거나 응답 형식이 바뀐 것이다.
- HTTP 5xx·네트워크는 그대로 올려 재시도한다. HTTP 400/401/403/404는 설정 오류라 즉시
  실패한다. 요청 제한(`020`)은 다음 run으로 넘긴다.
- 응답은 왔는데 아는 계정이 하나도 없으면 실패시킨다. 계정명이 바뀐 것이라 0건으로 두면
  매 실행이 같은 자리에서 조용히 아무 것도 안 남긴다.

## 필요한 환경

- `DART_API_KEY`. Airflow가 읽는 건 `compose/local/airflow/.env`다.
- `CONNECTION_ID`가 가리키는 Airflow 연결. 접속 정보는 `AIRFLOW_CONN_FINANCE`가 갖는다.

**API 키가 질의 문자열에 들어간다.** 그래서 수집기는 URL을 예외 메시지와 로그에 남기지 않고
`source_record.payload`에 원본 요청을 저장하지 않는다.
"""

import logging
import os
from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import Any

import pendulum
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Param, dag, get_current_context, task
from airflow.sdk.exceptions import AirflowFailException
from pydantic import SecretStr

from modules.collectors.document.dart import (
    MULTI_ACCOUNT_PERIODS,
    STATUS_RATE_LIMIT,
    DartCollector,
    DartHTTPError,
    DartPayloadError,
    DartStatusError,
    filing_entities,
    recent_report_periods,
)
from modules.utility import CONNECTION_ID, KST_TIMEZONE, atomic

logger = logging.getLogger(__name__)

REPORT_PERIODS_PARAM = "report_periods"

# 재시도해도 같은 답이 오는 HTTP 상태. 설정·인증·주소 문제다.
UNRECOVERABLE_STATUSES = frozenset({400, 401, 403, 404})


def _collector() -> DartCollector:
    key = os.environ.get("DART_API_KEY")
    if not key:
        raise AirflowFailException("DART_API_KEY is required")
    return DartCollector(SecretStr(key))


def _connection() -> Any:
    return PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()


def _report_periods() -> int:
    params = dict(get_current_context().get("params") or {})
    periods = int(params.get(REPORT_PERIODS_PARAM) or MULTI_ACCOUNT_PERIODS)
    if periods < 1:
        raise AirflowFailException(f"{REPORT_PERIODS_PARAM} must be at least 1")
    return periods


def _classify(error: DartHTTPError | DartStatusError) -> None:
    """재시도해도 같은 오류면 즉시 실패시킨다."""
    if isinstance(error, DartHTTPError):
        if error.status in UNRECOVERABLE_STATUSES:
            raise AirflowFailException(str(error)) from error
        return
    if error.code == STATUS_RATE_LIMIT:
        # 즉시 반복하면 요청 폭주가 된다. 다음 예약 실행으로 넘긴다.
        logger.warning("DART rate limit hit; leaving the rest to the next run")
        return
    raise AirflowFailException(str(error)) from error


@dag(
    dag_id="dart_multi_earnings_quarterly",
    dag_display_name="🏭 산업 대표 20사 분기 실적 (DART)",
    description="하루 한 번 산업 대표 20사의 최근 네 분기 매출·영업이익·순이익을 호출 한 번씩으로 받아 저장한다.",
    schedule="0 20 * * 1-5",  # KST 평일 20:00 = UTC 평일 11:00
    start_date=pendulum.datetime(2026, 9, 4, tz=KST_TIMEZONE),  # KST 2026-09-04 00:00 = UTC 2026-09-03 15:00
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=10)},
    params={
        REPORT_PERIODS_PARAM: Param(
            MULTI_ACCOUNT_PERIODS,
            type="integer",
            minimum=1,
            title="훑을 분기 수",
            description=(
                "이미 끝난 최근 분기를 몇 개나 다시 볼지. 정정으로 숫자가 바뀌면 새 접수번호로 "
                "오므로 지난 기간도 계속 본다. 오래된 구간을 백필할 때만 늘린다."
            ),
        ),
    },
    doc_md=__doc__,
    tags=["dart", "earnings", "korea", "macro", "daily"],
)
def dart_multi_earnings_quarterly():
    @task(task_display_name="산업 대표 실적")
    def collect_multi_accounts() -> int:
        collector = _collector()
        periods = _report_periods()
        # 분기 경계는 KST로 센다. 정기보고서 제출 기한이 한국 달력 기준이다.
        today_kst = datetime.now(UTC).astimezone(KST_TIMEZONE).date()

        stored = 0
        failures: list[str] = []
        with closing(_connection()) as connection:
            entities = filing_entities(connection)
            if not entities:
                raise AirflowFailException("instrument has no filing entity; nothing to collect")

            for business_year, report_code in recent_report_periods(today_kst, periods):
                label = f"{business_year}/{report_code}"
                try:
                    fetch = collector.fetch_multi_accounts(entities, business_year, report_code)
                except (DartHTTPError, DartStatusError) as error:
                    # 한 기간이 실패해도 다른 기간은 저장한다. 판정은 루프 끝에서 한다.
                    _classify(error)
                    logger.warning("multi-account %s failed: %s", label, error)
                    failures.append(f"{label}({error})")
                    continue
                except ConnectionError as error:
                    logger.warning("multi-account %s failed to connect: %s", label, error)
                    failures.append(f"{label}({error})")
                    continue
                except DartPayloadError as error:
                    logger.warning("multi-account %s is malformed: %s", label, error)
                    failures.append(f"{label}({error})")
                    continue

                with atomic(connection):
                    rows = collector.store_multi_accounts(connection, fetch)

                stored += rows
                logger.info(
                    "Stored %s earnings rows for %s from %s of %s companies",
                    rows,
                    label,
                    fetch.answered_count,
                    fetch.requested_count,
                )

        if failures:
            raise AirflowFailException(
                f"{len(failures)} of {periods} DART multi-account calls failed: {'; '.join(failures)}"
            )

        # **전부 0건은 성공이 아니다.** 직전 네 분기 중 최소 셋은 이미 공시돼 있다. 0건만
        # 이어지는 것은 대상 목록이 비었거나 계정명이 바뀐 것이고, 그것을 성공으로 두면
        # 매일 초록으로 끝나면서 테이블이 빈 채로 남는다.
        if not stored:
            raise AirflowFailException(
                f"DART returned no earnings for the last {periods} quarters as of {today_kst}; "
                f"직전 네 분기가 전부 0건이면 대상 목록이나 응답 형식이 바뀐 것이다"
            )

        logger.info("Stored %s earnings rows for %s companies", stored, len(entities))
        return stored

    collect_multi_accounts()


dart_multi_earnings_quarterly = dart_multi_earnings_quarterly()
