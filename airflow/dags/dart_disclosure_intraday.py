"""산업 대표 20사 DART 공시·실적 수집 DAG.

지금까지의 수집이 전부 가격과 금리였다. 이 DAG는 **이벤트 축**을 채운다. 실적 발표나 공시를
모르면 그 시각의 급변을 시장 신호로 오독한다.

결과는 `disclosure_event`와 `earnings_fact`에 쌓인다.

## 대상은 DB가 정한다

`instrument.filing_entity_id`가 채워진 행이 대상이다(`filing_entities`). 전에는
`DartCompany` StrEnum 두 줄이었는데 산업 대표 20사로 넓히면서 마스터로 옮겼다(2026-09-04).

**`is_watched`(시세 대상)와 다른 축이다.** 한 플래그에 묶어 두면 공시 대상을 늘릴 때
KIS 분봉·수급·실시간 구독까지 함께 끌려온다. 시세 대상은 여전히 둘이다.

호출은 회사마다 하나이므로 하루 420 run × 20사 = 8,400콜이다. DART 일 한도 2만의 42%이고,
**50사를 넘어가면** 회사별 조회 대신 `corp_cls`로 시장 전체를 받는 형태로 바꾼다
(`docs/collection/korea-industry-macro-expansion.md`).

## 태스크

- `collect_disclosures` — 대상 회사의 최근 7일 공시를 받아 접수번호로 upsert한다.
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

- **한 회사가 실패해도 다른 회사는 저장한다.** 전부 실패해야 태스크가 실패한다. 2분마다
  같은 창을 다시 보므로 하나로 죽이면 경보만 늘고 고쳐지는 것은 없다.
- HTTP 5xx·네트워크: 그대로 올려 재시도한다.
- HTTP 400/401/403/404: 설정 오류라 즉시 실패한다.
- 본문 `status` 오류: 인증·요청 오류(`0100`대)는 즉시 실패, 요청 제한(`020`)은 다음 run으로
  넘긴다. 즉시 반복하면 요청 폭주를 만든다.
- 데이터 없음(`013`)과 원문 없음(`014`)은 실패가 아니다. 전자는 0건이고 후자는 새 공시
  직후라 다음 run이 다시 본다.
- **공시가 0건인 것은 정상이다.** 빈 목록도 성공한 `source_record`로 남는다.
- 실적 추출 실패는 그 공시만 건너뛴다. 다음 run의 재시도 목록에 그대로 남는다.
- 본문 수집도 같다. `body IS NULL`이 재시도 목록이라 실패한 공시는 다음 run이 다시 본다.
  전부 실패하면 원문 형식이나 자격 증명이 바뀐 것이라 태스크를 실패시킨다.

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
    STATUS_RATE_LIMIT,
    DartCollector,
    DartHTTPError,
    DartPayloadError,
    DartStatusError,
    Disclosure,
    FilingEntity,
    filing_entities,
    is_provisional,
    pending_bodies,
    pending_earnings,
    periodic_report,
)
from modules.utility import CONNECTION_ID, KST_TIMEZONE, UNRECOVERABLE_STATUSES, atomic

logger = logging.getLogger(__name__)

LOOKBACK_DAYS = 7
LOOKBACK_DAYS_PARAM = "lookback_days"


def _collector() -> DartCollector:
    key = os.environ.get("DART_API_KEY")
    if not key:
        raise AirflowFailException("DART_API_KEY is required")
    return DartCollector(SecretStr(key))


def _lookback_days() -> int:
    params = dict(get_current_context().get("params") or {})
    days = int(params.get(LOOKBACK_DAYS_PARAM) or LOOKBACK_DAYS)
    if days < 1:
        raise AirflowFailException(f"{LOOKBACK_DAYS_PARAM} must be at least 1")
    return days


def _connection() -> Any:
    return PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()


def _entities() -> tuple[FilingEntity, ...]:
    """수집 대상. **마스터가 비면 실패다** — 0건 성공으로 두면 매 2분이 조용히 아무 것도 안 한다."""
    with closing(_connection()) as connection:
        entities = filing_entities(connection)
    if not entities:
        raise AirflowFailException("instrument has no filing entity; nothing to collect")
    return entities


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
    dag_display_name="📄 산업 대표 20사 공시·실적 (DART)",
    description="2분마다 DART에서 산업 대표 20사의 새 공시를 받고 잠정·정기 실적을 추출한다.",
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
        collector = _collector()
        end_date = datetime.now(UTC).astimezone(KST_TIMEZONE).date()
        begin_date = end_date - timedelta(days=_lookback_days() - 1)

        entities = _entities()

        stored = 0
        failures: list[str] = []
        for entity in entities:
            try:
                fetch = collector.fetch_disclosures(entity, begin_date, end_date)
            except (DartHTTPError, DartStatusError) as error:
                # 한 회사가 실패해도 다른 회사는 저장한다.
                _classify(error)
                logger.warning("%s disclosure list failed: %s", entity.name, error)
                failures.append(f"{entity.stock_code}({error})")
                continue
            except ConnectionError as error:
                logger.warning("%s disclosure list failed to connect: %s", entity.name, error)
                failures.append(f"{entity.stock_code}({error})")
                continue

            with closing(_connection()) as connection:
                try:
                    with atomic(connection):
                        stored += collector.store_disclosures(connection, fetch)
                except DartPayloadError as error:
                    raise AirflowFailException(str(error)) from error

            logger.info("Stored %s disclosures for %s", len(fetch.disclosures), entity.name)

        if len(failures) == len(entities):
            raise ConnectionError(f"Every DART disclosure request failed: {'; '.join(failures)}")
        if failures:
            logger.warning("%s of %s disclosure lists failed: %s", len(failures), len(entities), "; ".join(failures))
        return stored

    @task(task_display_name="실적 추출")
    def extract_earnings() -> int:
        collector = _collector()
        since = datetime.now(UTC).astimezone(KST_TIMEZONE).date() - timedelta(days=_lookback_days() - 1)

        entities = _entities()
        connection = _connection()
        try:
            waiting = pending_earnings(connection, tuple(entity.stock_code for entity in entities), since)
        finally:
            connection.close()

        stored = 0
        failures: list[str] = []
        for disclosure in waiting:
            try:
                fetch = _extract(collector, disclosure, entities)
            except (DartHTTPError, DartStatusError) as error:
                _classify(error)
                logger.warning("%s earnings extraction failed: %s", disclosure.rcept_no, error)
                failures.append(f"{disclosure.rcept_no}({error})")
                continue
            except DartPayloadError as error:
                # 원문 형식이 바뀐 것이다. 이 공시만 건너뛰고 공시 이벤트는 그대로 둔다.
                logger.warning("%s earnings parsing failed: %s", disclosure.rcept_no, error)
                failures.append(f"{disclosure.rcept_no}({error})")
                continue
            except ConnectionError as error:
                logger.warning("%s earnings extraction failed to connect: %s", disclosure.rcept_no, error)
                failures.append(f"{disclosure.rcept_no}({error})")
                continue

            if fetch is None:
                continue

            with closing(_connection()) as connection, atomic(connection):
                stored += collector.store_earnings(connection, fetch)

            logger.info(
                "Stored %s %s metrics from %s (%s)",
                len(fetch.values),
                fetch.release_type,
                disclosure.rcept_no,
                disclosure.report_name,
            )

        # 매시간 도는 수집이라 한 건의 실패로 죽이지 않는다. 전부 실패하면 원문 형식이나
        # 자격 증명이 바뀐 것이라 다음 실행도 같은 자리에서 멈춘다.
        if failures and len(failures) == len(waiting):
            raise AirflowFailException(f"Every earnings extraction failed: {'; '.join(failures)}")
        if failures:
            logger.warning("%s of %s earnings extractions failed: %s", len(failures), len(waiting), "; ".join(failures))
        return stored

    @task(task_display_name="공시 본문 수집")
    def collect_bodies() -> int:
        """시장이 반응하는 종류의 공시 본문을 채운다.

        **목록 API는 보고서명까지만 준다.** 인과 그래프가 그 한 줄로 사건을 만들려다 내용을
        지어냈다(2026-08-28) — 그래서 원문에서 태그를 걷어낸 문장을 함께 저장한다.
        """
        collector = _collector()
        since = datetime.now(UTC).astimezone(KST_TIMEZONE).date() - timedelta(days=_lookback_days() - 1)

        with closing(_connection()) as connection:
            entities = filing_entities(connection)
            if not entities:
                raise AirflowFailException("instrument has no filing entity; nothing to collect")
            waiting = pending_bodies(connection, tuple(entity.stock_code for entity in entities), since)

        stored = 0
        failures: list[str] = []
        for disclosure in waiting:
            try:
                body = collector.fetch_body(disclosure)
            except (DartHTTPError, DartStatusError) as error:
                _classify(error)
                logger.warning("%s body fetch failed: %s", disclosure.rcept_no, error)
                failures.append(f"{disclosure.rcept_no}({error})")
                continue
            except DartPayloadError as error:
                # 원문 형식이 바뀐 것이다. 이 공시만 건너뛰고 공시 이벤트는 그대로 둔다.
                logger.warning("%s body parsing failed: %s", disclosure.rcept_no, error)
                failures.append(f"{disclosure.rcept_no}({error})")
                continue
            except ConnectionError as error:
                logger.warning("%s body fetch failed to connect: %s", disclosure.rcept_no, error)
                failures.append(f"{disclosure.rcept_no}({error})")
                continue

            if body is None:
                continue

            with closing(_connection()) as connection, atomic(connection):
                stored += collector.store_body(connection, disclosure.rcept_no, body)

            logger.info(
                "Stored %s characters of body for %s (%s)",
                len(body),
                disclosure.rcept_no,
                disclosure.report_name,
            )

        # 매시간 도는 수집이라 한 건의 실패로 죽이지 않는다. 전부 실패하면 원문 형식이나
        # 자격 증명이 바뀐 것이라 다음 실행도 같은 자리에서 멈춘다.
        if failures and len(failures) == len(waiting):
            raise AirflowFailException(f"Every disclosure body fetch failed: {'; '.join(failures)}")
        if failures:
            logger.warning("%s of %s body fetches failed: %s", len(failures), len(waiting), "; ".join(failures))
        return stored

    collect_disclosures() >> extract_earnings() >> collect_bodies()


def _extract(collector: DartCollector, disclosure: Disclosure, entities: tuple[FilingEntity, ...]):
    """공시 하나에서 실적을 뽑는다. **대상이 아니면** `None`이다.

    **실패는 삼키지 않는다** — 부르는 쪽이 세고 판정한다. 여기서 warning 뒤 `None`을
    돌려주면 "대상 아님"과 "실패"가 같아 보여서, 대기 공시 전부가 실패해도 태스크가
    `stored=0`으로 성공했다.

    한 공시의 실패가 나머지를 막지는 않는다. 그 공시는 다음 run의 재시도 목록에 그대로
    남아 있다.
    """
    if is_provisional(disclosure.report_name):
        return collector.fetch_provisional(disclosure)
    if periodic_report(disclosure.report_name) is not None:
        return collector.fetch_financials(disclosure, entities)
    return None


dart_disclosure_intraday = dart_disclosure_intraday()
