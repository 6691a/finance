"""이미 발견한 문서의 본문과 첨부를 매시간 채운다.

`document_ingestion_hourly`가 피드와 목록에서 문서를 발견하고, 이 DAG이 그 문서 하나하나의
원문을 받는다. 설계는 `docs/collection/document-body-collection.md`다.

## 왜 수집 DAG과 나뉘어 있나

**앞단과 실패 성격이 다르다.** 발견은 출처당 요청 한 번이고 여기는 문서당 요청 한 번 +
첨부 수만큼이다. 한 DAG에 넣으면 본문 200건의 요청이 다음 시간 피드 수집을 밀고, 재시도가
피드까지 다시 친다. 실패의 단위도 다르다 — 발견은 출처 하나가 죽고 여기는 문서 하나가 죽는다.

## 큐가 곧 백필이다

`document.body_status`가 NULL인 문서를 오래된 것부터 집는다. NULL이 "아직 시도하지 않았다"라서
**신규 문서와 과거 문서의 백필이 같은 코드다.** 별도 백필 스크립트가 없다.

받을 수 없는 문서(KRX처럼 문서별 딥링크가 없는 것)와 본문이 첨부에만 있는 문서도 상태를
남기므로 다음 실행이 다시 치지 않는다. `body IS NULL`로 큐를 돌면 그것들을 매시간 다시 친다.

## 실패와 재시도

**항목별 실패 수집이고, 전부 실패했을 때만 태스크를 죽인다.** 다음 실행이 같은 큐를 곧 다시
보기 때문이다 — 문서 하나로 죽이면 경보만 늘고 고쳐지는 것은 없다.
`document_ingestion_hourly`·`kis_equity_bar_reconcile`과 같은 계열이다.

- HTTP 400/401/403/404는 주소가 죽은 것이라 그 문서를 `unavailable`로 **확정**하고 넘어간다.
  다시 쳐도 같은 답이므로 실패로 세지 않는다.
- 연결 실패와 5xx는 `body_status`를 NULL로 남긴다. 다음 실행이 다시 집는다.
- **첨부 다운로드 실패가 본문 저장을 되돌리지 않는다.** 본문을 먼저 커밋하고 파일을 하나씩
  뒤따라 커밋한다. 어렵게 받은 본문을 파일 하나 때문에 버리지 않는다.

## 필요한 환경

- `CONNECTION_ID`가 가리키는 Airflow 연결. 접속 정보는 `AIRFLOW_CONN_FINANCE`가 갖는다.
- **`FILE_ROOT`(`/opt/airflow/files`)가 마운트돼 있어야 한다.** NAS 디렉터리를 그 경로로
  붙인다. 없으면 태스크를 즉시 실패시킨다 — 조용히 첨부를 건너뛰면 문서만 쌓이고 파일은
  한 건도 안 남는데 태스크는 성공으로 표시된다.
- 인증은 없다. 전부 공개 페이지다.
"""

import logging
from contextlib import closing
from datetime import timedelta
from typing import Any

import pendulum
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Param, dag, task
from airflow.sdk.exceptions import AirflowFailException

from modules.collectors.document.body import (
    DEFAULT_FILE_ROOT,
    BodyCandidate,
    DocumentBody,
    DocumentBodyCollector,
    pending_bodies,
)
from modules.collectors.document.documents import DocumentHTTPError, DocumentPayloadError
from modules.utility import CONNECTION_ID, KST_TIMEZONE, UNRECOVERABLE_STATUSES, atomic

logger = logging.getLogger(__name__)

# 한 실행이 처리할 문서 수. **본문 길이나 파일 크기의 상한이 아니라 배치 상한이다.**
# 3,598건(2026-08-30 실측)이 밀려 있어 이 값이면 백필이 18시간, 하루면 끝난다.
DEFAULT_BATCH_SIZE = 200


def _connection() -> Any:
    return PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()


@dag(
    dag_id="document_body_hourly",
    dag_display_name="📄 문서 본문·첨부 수집",
    description="발견해 둔 문서의 본문을 받아 채우고 첨부 파일을 내려받으며 영상 링크를 남긴다.",
    # KST 매시 15분 = UTC 매시 15분. 05분(발견)·25분(평가)·45분(사건 기대)을 피한다.
    schedule="15 * * * *",
    start_date=pendulum.datetime(2026, 8, 30, tz=KST_TIMEZONE),  # KST 2026-08-30 00:00 = UTC 2026-08-29 15:00
    catchup=False,
    max_active_runs=1,
    # 1시간 주기라 다음 run이 멀지 않다. 짧게 두 번 시도한다.
    default_args={"retries": 2, "retry_delay": timedelta(minutes=10)},
    params={
        "batch_size": Param(
            DEFAULT_BATCH_SIZE,
            type="integer",
            minimum=1,
            maximum=2000,
            title="한 실행이 처리할 문서 수",
            description="본문을 아직 안 받아 본 문서를 오래된 것부터 이만큼 집는다. 백필을 서두르려면 올린다.",
        ),
    },
    doc_md=__doc__,
    tags=["documents", "hourly"],
)
def document_body_hourly():
    @task(task_display_name="본문·첨부 수집")
    def collect(**context) -> int:
        file_root = DEFAULT_FILE_ROOT
        if not file_root.is_dir():
            # 마운트가 없는데 조용히 넘기면 첨부가 한 건도 안 남는데 태스크는 성공으로 보인다.
            raise AirflowFailException(f"{file_root} is not mounted; attach the file volume first")

        collector = DocumentBodyCollector(file_root)
        batch_size = int(context["params"]["batch_size"])

        with closing(_connection()) as connection:
            waiting = pending_bodies(connection, batch_size)

        if not waiting:
            logger.info("No document is waiting for its body")
            return 0

        stored = 0
        failures: list[str] = []
        # 첨부 실패는 따로 센다. 문서 하나에 파일이 여럿이라 한 줄로 섞으면
        # "전부 실패했나"의 분모가 문서 수와 어긋난다.
        attachment_failures: list[str] = []
        for candidate in waiting:
            try:
                result = collector.collect(candidate)
            except DocumentHTTPError as error:
                if error.status not in UNRECOVERABLE_STATUSES:
                    logger.warning("%s failed with HTTP %s", candidate.canonical_url, error.status)
                    failures.append(f"{candidate.id}({error})")
                    continue
                # 주소가 죽은 것이다. 다시 쳐도 같은 답이라 확정하고 넘어간다.
                logger.info("%s is gone (HTTP %s); settling as unavailable", candidate.canonical_url, error.status)
                result = DocumentBody(document_id=candidate.id, status="unavailable")
            except DocumentPayloadError as error:
                # 제공처 응답 형식이 바뀌었다. 상태를 남기지 않아 다음 실행이 다시 집는다.
                logger.warning("%s returned something we cannot read: %s", candidate.canonical_url, error)
                failures.append(f"{candidate.id}({error})")
                continue
            except ConnectionError as error:
                logger.warning("%s failed to connect: %s", candidate.canonical_url, error)
                failures.append(f"{candidate.id}({error})")
                continue
            except Exception as error:
                # 예상하지 못한 예외도 이 문서에서 멈춰야 한다. 파서 예외 하나가 나머지
                # 문서를 통째로 막은 적이 있다(`document_ingestion_hourly`, 2026-08-15).
                logger.exception("%s raised an unexpected error", candidate.canonical_url)
                failures.append(f"{candidate.id}({error})")
                continue

            with closing(_connection()) as connection, atomic(connection):
                stored += collector.store_body(connection, result)

            _attach_files(collector, candidate, result, attachment_failures)

        if failures and len(failures) == len(waiting):
            # 전부 실패했으면 우리 쪽 문제일 가능성이 크다. 재시도할 값어치가 있다.
            raise ConnectionError(f"Every document failed: {'; '.join(failures)}")
        if failures:
            logger.warning("%s of %s documents failed: %s", len(failures), len(waiting), "; ".join(failures))
        if attachment_failures:
            logger.warning("%s attachments failed: %s", len(attachment_failures), "; ".join(attachment_failures))

        logger.info("Filled %s of %s documents", stored, len(waiting))
        return stored

    collect()


def _attach_files(
    collector: DocumentBodyCollector,
    candidate: BodyCandidate,
    result: DocumentBody,
    failures: list[str],
) -> None:
    """첨부 파일을 하나씩 받아 붙인다. **파일 하나가 트랜잭션 하나다.**

    본문은 이미 커밋됐다. 파일 하나가 죽어도 그것을 되돌리지 않고, 그 파일만 빠진다.
    """
    for position, url in enumerate(result.file_urls):
        try:
            attachment = collector.download(url, position)
        except (DocumentHTTPError, ConnectionError) as error:
            logger.warning("attachment %s of document %s failed: %s", url, candidate.id, error)
            failures.append(f"{candidate.id}:{url}({error})")
            continue
        except OSError as error:
            # 마운트가 중간에 사라지거나 디스크가 찬 것이다. 이 문서만 세고 넘어간다.
            logger.warning("attachment %s of document %s could not be written: %s", url, candidate.id, error)
            failures.append(f"{candidate.id}:{url}({error})")
            continue

        with closing(_connection()) as connection, atomic(connection):
            collector.store_attachment(connection, candidate.id, attachment)


document_body_hourly = document_body_hourly()
