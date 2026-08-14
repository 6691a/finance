"""삼성전자·SK하이닉스 DART 공시·실적 수집 DAG.

지금까지의 수집이 전부 가격과 금리였다. 이 DAG는 **이벤트 축**을 채운다. 실적 발표나 공시를
모르면 그 시각의 급변을 시장 신호로 오독한다.

결과는 `disclosure_event`와 `earnings_fact`에 쌓인다.

## 태스크

- `collect_disclosures` — 두 회사의 최근 7일 공시를 받아 접수번호로 upsert한다.
- `extract_earnings` — 아직 숫자를 못 얻은 공시만 골라 원문이나 재무제표에서 추출한다.

두 태스크는 차례로 돈다. **공시 이벤트 저장이 실적 추출보다 먼저다.** 잠정실적 표 형식이
바뀌어 숫자를 못 읽어도 공시 이벤트까지 잃지 않는다.

## 왜 매번 최근 7일인가

프로세스 중단, 휴일, 늦은 정정을 별도 상태 없이 흡수한다. 멱등 키가 `(provider, rcept_no)`라
같은 공시를 다시 받아도 행이 늘지 않고, `detected_at`은 최초값을 지킨다.

## DART는 실시간이 아니다

WebSocket이 없다. 화면에 보이는 신선도는 **폴링 주기 + DART 반영 지연**이다. 그래서 저장하는
시각은 `detected_at`(우리가 처음 본 시각)이고 화면에도 "최초 감지"로 표시한다. 접수일
(`receipt_date`)에는 시·분이 없으므로 자정 같은 값으로 꾸미지 않는다.

분 단위 접수 시각은 공식 RSS에만 있는데 전 상장사 최신 50건뿐이라 실측에서 1시간 35분치만
덮었다. 과거는 못 채우고 현재는 2분 폴링의 `detected_at`이 이미 그만한 해상도를 준다.
그래서 수집하지 않는다.

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `lookback_days` | `7` | 공시 목록과 실적 재시도가 되돌아볼 일수 |

    airflow dags trigger dart_disclosure_intraday --conf '{"lookback_days": 30}'

## 실패와 재시도

- **한 회사가 실패해도 다른 회사는 저장한다.** 둘 다 실패해야 태스크가 실패한다.
- HTTP 5xx·네트워크: 그대로 올려 재시도한다.
- HTTP 400/401/403/404: 설정 오류라 즉시 실패한다.
- 본문 `status` 오류: 인증·요청 오류(`0100`대)는 즉시 실패, 요청 제한(`020`)은 다음 run으로
  넘긴다. 즉시 반복하면 요청 폭주를 만든다.
- 데이터 없음(`013`)과 원문 없음(`014`)은 실패가 아니다. 전자는 0건이고 후자는 새 공시
  직후라 다음 run이 다시 본다.
- **공시가 0건인 것은 정상이다.** 빈 목록도 성공한 `source_record`로 남는다.
- 실적 추출 실패는 그 공시만 건너뛴다. 다음 run의 재시도 목록에 그대로 남는다.

## 필요한 환경

- `DART_API_KEY`. Airflow가 읽는 건 `compose/local/airflow/.env`다.
- `CONNECTION_ID`가 가리키는 Airflow 연결. 접속 정보는 `AIRFLOW_CONN_NEWS`가 갖는다.

**API 키가 질의 문자열에 들어간다.** 그래서 수집기는 URL을 예외 메시지와 로그에 남기지 않고
`source_record.payload`에 원본 요청을 저장하지 않는다.
"""

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import pendulum
from airflow.exceptions import AirflowFailException
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Param, dag, get_current_context, task
from pydantic import SecretStr

from modules.collectors.dart import (
    STATUS_RATE_LIMIT,
    DartCompany,
    DartHTTPError,
    DartPayloadError,
    DartStatusError,
    Disclosure,
    fetch_disclosures,
    fetch_financials,
    fetch_provisional,
    is_provisional,
    pending_earnings,
    periodic_report,
    store_disclosures,
    store_earnings,
)
from modules.utility import KST_TIMEZONE

logger = logging.getLogger(__name__)

CONNECTION_ID = "news"

LOOKBACK_DAYS = 7
LOOKBACK_DAYS_PARAM = "lookback_days"

# 설정 오류라 재시도해도 같은 결과인 HTTP 상태.
UNRECOVERABLE_STATUSES = frozenset({400, 401, 403, 404})


def _api_key() -> SecretStr:
    key = os.environ.get("DART_API_KEY")
    if not key:
        raise AirflowFailException("DART_API_KEY is required")
    return SecretStr(key)


def _lookback_days() -> int:
    params = dict(get_current_context().get("params") or {})
    days = int(params.get(LOOKBACK_DAYS_PARAM) or LOOKBACK_DAYS)
    if days < 1:
        raise AirflowFailException(f"{LOOKBACK_DAYS_PARAM} must be at least 1")
    return days


def _connection() -> Any:
    # 반환 타입은 provider 버전에 따라 psycopg2/psycopg3 래퍼로 갈린다. 런타임 객체는
    # 어느 쪽이든 PEP 249 연결이라 commit·rollback을 갖는다.
    return PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()


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
    dag_id="dart_disclosure_intraday",
    dag_display_name="📄 삼성전자·SK하이닉스 공시·실적 (DART)",
    description="2분마다 DART에서 두 회사의 새 공시를 받고 잠정·정기 실적을 추출한다.",
    # KST 평일 07:00~20:59 = UTC 평일 22:00~11:59. 장 시작 전부터 장 마감 뒤 공시까지 덮는다.
    schedule="*/2 7-20 * * 1-5",
    start_date=pendulum.datetime(2026, 8, 13, tz=KST_TIMEZONE),  # KST 2026-08-13 00:00 = UTC 2026-08-12 15:00
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 1, "retry_delay": timedelta(minutes=2)},
    params={
        LOOKBACK_DAYS_PARAM: Param(
            LOOKBACK_DAYS,
            type="integer",
            minimum=1,
            title="되돌아볼 일수",
            description="공시 목록과 실적 재시도가 함께 쓴다. 늦은 정정을 흡수하려고 매번 다시 조회한다.",
        ),
    },
    doc_md=__doc__,
    tags=["dart", "disclosure", "earnings", "korea"],
)
def dart_disclosure_intraday():
    @task(task_display_name="공시 목록")
    def collect_disclosures() -> int:
        api_key = _api_key()
        end_date = datetime.now(UTC).astimezone(KST_TIMEZONE).date()
        begin_date = end_date - timedelta(days=_lookback_days() - 1)

        stored = 0
        failures: list[str] = []
        for company in DartCompany:
            try:
                fetch = fetch_disclosures(api_key, company, begin_date, end_date)
            except (DartHTTPError, DartStatusError) as error:
                # 한 회사가 실패해도 다른 회사는 저장한다.
                _classify(error)
                logger.warning("%s disclosure list failed: %s", company.label, error)
                failures.append(company.value)
                continue
            except ConnectionError as error:
                logger.warning("%s disclosure list failed to connect: %s", company.label, error)
                failures.append(company.value)
                continue

            connection = _connection()
            try:
                stored += store_disclosures(connection, fetch)
                connection.commit()
            except DartPayloadError as error:
                connection.rollback()
                raise AirflowFailException(str(error)) from error
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

            logger.info("Stored %s disclosures for %s", len(fetch.disclosures), company.label)

        if len(failures) == len(DartCompany):
            raise ConnectionError("Every DART disclosure request failed")
        return stored

    @task(task_display_name="실적 추출")
    def extract_earnings() -> int:
        api_key = _api_key()
        since = datetime.now(UTC).astimezone(KST_TIMEZONE).date() - timedelta(days=_lookback_days() - 1)

        connection = _connection()
        try:
            waiting = pending_earnings(connection, tuple(company.value for company in DartCompany), since)
        finally:
            connection.close()

        stored = 0
        for disclosure in waiting:
            fetch = _extract(api_key, disclosure)
            if fetch is None:
                continue

            connection = _connection()
            try:
                stored += store_earnings(connection, fetch)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

            logger.info(
                "Stored %s %s metrics from %s (%s)",
                len(fetch.values),
                fetch.release_type,
                disclosure.rcept_no,
                disclosure.report_name,
            )
        return stored

    collect_disclosures() >> extract_earnings()


def _extract(api_key: SecretStr, disclosure: Disclosure):
    """공시 하나에서 실적을 뽑는다. 대상이 아니거나 아직 준비 전이면 `None`.

    **한 공시의 실패가 나머지를 막지 않는다.** 그 공시는 다음 run의 재시도 목록에 그대로
    남아 있다.
    """
    try:
        if is_provisional(disclosure.report_name):
            return fetch_provisional(api_key, disclosure)
        if periodic_report(disclosure.report_name) is not None:
            return fetch_financials(api_key, disclosure)
    except (DartHTTPError, DartStatusError) as error:
        _classify(error)
        logger.warning("%s earnings extraction failed: %s", disclosure.rcept_no, error)
    except DartPayloadError as error:
        # 원문 형식이 바뀐 것이다. 이 공시만 건너뛰고 공시 이벤트는 그대로 둔다.
        logger.warning("%s earnings parsing failed: %s", disclosure.rcept_no, error)
    except ConnectionError as error:
        logger.warning("%s earnings extraction failed to connect: %s", disclosure.rcept_no, error)
    return None


dart_disclosure_intraday = dart_disclosure_intraday()
