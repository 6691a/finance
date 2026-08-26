"""공식기관·언론 피드에서 경제 문서를 매시간 발견해 저장한다.

`docs/analysis/economic-document-archive-design.md` 2단계의 수집 절반이다. LLM 태깅은 별도 DAG이
맡는다. **모델이나 API 키가 없어도 이 DAG은 돈다.** 원문 수집이 LLM 장애에 묶이면 안 된다는
것이 설계의 첫 결정이고, 태깅이 못 돌아도 문서는 태그 없이 쌓인다.

## 수집 대상이 코드에 없다

어떤 피드를 어디까지 가져올지는 `document_source` 테이블이 정한다. 시세 수집기들이 심볼을
Enum으로 들고 있는 것과 반대인데, **이용조건은 출처마다 다르고 바뀌기 때문이다.** 한 곳이
본문 자동수집을 막았을 때 필요한 조치가 배포여서는 안 된다. `enabled`를 내리거나
`collection_mode`를 낮추면 다음 실행부터 반영된다.

지금 켜져 있는 출처는 마이그레이션 시드가 넣은 것이고, **전부 실제로 요청해 응답을 확인한
채널이다**(2026-08-15~20). 설계 초안의 Reuters와 AP는 피드 도메인이 DNS에 없어 뺐다.
대부분 RSS·Atom이지만 피드가 없는 곳(KRX·금감원)은 게시판 목록을 직접 읽는다 —
`modules.collectors.document_listings.LISTING_SOURCES`가 그 목록이다.

## 왜 매시간인가

RSS는 최근 항목만 준다. **수집을 시작하기 전 기간은 영영 비어 있고 놓친 항목도 돌아오지
않는다.** 그래서 주기는 피드가 밀어내는 속도보다 빨라야 한다. 가장 빠른 연합인포맥스
전체기사 피드가 50건에 약 2.6시간 분량(시간당 ~18건, 2026-08-19 실측)이라 1시간이면
충분하다. 특정 출처가 넘치기 시작하면 그 출처만 별도 DAG으로 뗀다.

## 실패 처리

- **출처 하나가 트랜잭션 하나다.** 한 곳이 실패해도 앞의 성공은 커밋된 채 남는다.
- HTTP 400/401/403/404는 주소나 정책이 바뀐 것이라 그 출처만 실패로 기록한다. 다른 출처를
  막지 않는다.
- 응답이 XML이 아니면 실패다. 주소가 바뀐 사이트는 404 대신 HTML 안내를 200으로 준다.
  조용히 0건으로 넘기면 몇 달째 비어 있어도 알 수 없다.
- 전부 실패하면 태스크를 실패시킨다.

## 필요한 환경

- `CONNECTION_ID`가 가리키는 Airflow 연결. 접속 정보는 `AIRFLOW_CONN_FINANCE`가 갖는다.
- 인증은 없다. 전부 공개 피드다. 네이버 리서치(`naver_research_*`)는 robots.txt가 일반 봇을
  막는 내부 JSON이고, 사용자 결정으로 수집한다(`docs/analysis/market-thesis/6-analyst.md` 1.2절).
"""

import logging
from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import Any

import pendulum
from airflow.exceptions import AirflowFailException
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import dag, task

from modules.collectors.document.document_listings import LISTING_SOURCES
from modules.collectors.document.documents import (
    DocumentHTTPError,
    DocumentPayloadError,
    FeedSource,
    SourceOutcome,
    enabled_sources,
    fetch_feed,
    parse_feed,
    store_documents,
)
from modules.utility import CONNECTION_ID, KST_TIMEZONE, UNRECOVERABLE_STATUSES, atomic

logger = logging.getLogger(__name__)


def _connection() -> Any:
    return PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()


def collect_source(source: FeedSource, detected_at: datetime) -> tuple[int, SourceOutcome]:
    """출처 하나를 받아 저장한다. 이 함수 하나가 트랜잭션 하나다."""
    listing = LISTING_SOURCES.get(source.slug)
    if listing is not None:
        # 피드가 없는 출처(KRX·금감원·네이버 리서치)는 목록을 직접 읽는다.
        response = listing.fetch(source)
        items, truncated = listing.parse(response.body)
    else:
        response = fetch_feed(source)
        items, truncated = parse_feed(response.body, source.slug, source.feed_url)

    with closing(_connection()) as connection:
        if listing is not None and listing.enrich is not None:
            # 상세 요청(HTTP)은 트랜잭션 바깥이다. 기존 항목을 빼고 새 항목만 채운다.
            items = listing.enrich(connection, source, items)
        with atomic(connection):
            stored, outcome = store_documents(connection, source, response, items, truncated, detected_at)

    if truncated:
        logger.warning("%s returned more items than we take in one run", source.slug)
    return stored, outcome


@dag(
    dag_id="document_ingestion_hourly",
    dag_display_name="📰 경제 문서 수집 (공식기관·언론)",
    description="매시간 공식기관·언론 피드에서 경제 문서를 발견해 정규화하고 저장한다. LLM 태깅은 별도 DAG이다.",
    # KST 매시 05분 = UTC 매시 05분. 정각을 피해 다른 수집과 겹치지 않게 둔다.
    schedule="5 * * * *",
    start_date=pendulum.datetime(2026, 8, 15, tz=KST_TIMEZONE),  # KST 2026-08-15 00:00 = UTC 2026-08-14 15:00
    catchup=False,
    max_active_runs=1,
    # 1시간 주기라 다음 run이 멀지 않다. 짧게 두 번 시도한다.
    default_args={"retries": 2, "retry_delay": timedelta(minutes=10)},
    doc_md=__doc__,
    tags=["documents", "rss", "hourly"],
)
def document_ingestion_hourly():
    @task(task_display_name="피드 수집·저장")
    def collect() -> int:
        connection = _connection()
        try:
            sources = enabled_sources(connection)
        finally:
            connection.close()

        if not sources:
            # 마스터가 비어 있으면 수집할 것이 없다. 시드가 빠진 상태이므로 조용히 넘기지 않는다.
            raise AirflowFailException("No enabled document_source rows; seed the catalogue first")

        # 한 run 안의 모든 문서가 같은 발견 시각을 갖는다. 출처마다 몇 초씩 어긋나면
        # "언제 처음 봤나"로 정렬할 때 순서가 수집 순서를 따라간다.
        detected_at = datetime.now(UTC)

        stored = 0
        failures: list[str] = []
        for source in sources:
            try:
                count, outcome = collect_source(source, detected_at)
            except DocumentHTTPError as error:
                if error.status in UNRECOVERABLE_STATUSES:
                    # 주소나 정책이 바뀐 것이다. 이 출처만 실패로 두고 나머지를 계속한다.
                    logger.warning("%s is unreachable with HTTP %s; check the feed URL", source.slug, error.status)
                else:
                    logger.warning("%s failed with HTTP %s", source.slug, error.status)
                failures.append(f"{source.slug}({error})")
                continue
            except DocumentPayloadError as error:
                logger.warning("%s returned something that is not a feed: %s", source.slug, error)
                failures.append(f"{source.slug}({error})")
                continue
            except ConnectionError as error:
                logger.warning("%s failed to connect: %s", source.slug, error)
                failures.append(f"{source.slug}({error})")
                continue
            except Exception as error:
                # 우리가 예상하지 못한 예외도 이 출처에서 멈춰야 한다. 실제로 BEA 요약의
                # escape되지 않은 `<` 하나가 파서 예외로 새어 나와 나머지 출처를 통째로
                # 막은 적이 있다(2026-08-15). 격리를 예외 목록에 맡기지 않는다.
                logger.exception("%s raised an unexpected error", source.slug)
                failures.append(f"{source.slug}({error})")
                continue

            stored += count
            logger.info("%s stored %s documents", source.slug, outcome.item_count)

        if len(failures) == len(sources):
            # 전부 실패했으면 우리 쪽 문제일 가능성이 크다. 재시도할 값어치가 있다.
            raise ConnectionError(f"Every feed failed: {'; '.join(failures)}")
        if failures:
            logger.warning("%s of %s feeds failed: %s", len(failures), len(sources), "; ".join(failures))

        logger.info("Stored %s documents from %s feeds", stored, len(sources) - len(failures))
        return stored

    collect()


document_ingestion_hourly = document_ingestion_hourly()
