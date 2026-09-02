"""받아 둔 첨부 PDF에서 텍스트를 뽑아 첨부 행에 채운다.

`document_body_hourly`가 파일을 받고, 이 DAG이 그 파일 안의 글자를 꺼낸다. 설계는
`docs/analysis/pdf-parsing-bm25.md`다.

첨부에만 본문이 있는 문서가 469~772건이다(한국은행·금감원·BOJ·네이버 리서치). 그 문서들은
지금 제목과 요약만으로 검색된다. 이 DAG이 채우는 것이 그 빈칸이다.

## 왜 본문 수집과 나뉘어 있나

**앞단과 실패 성격이 다르다.** 본문 수집은 문서마다 HTTP 요청이라 네트워크가 병목이고,
여기는 파일을 읽고 CPU로 파싱하는 일이다. 한 DAG에 넣으면 PDF 수백 쪽을 파싱하는 동안
다음 시간 본문 수집이 밀린다. 그래서 pool(`pdf_parse`, 슬롯 1)도 여기만 문다 — 나중에 NAS
CPU를 같이 쓰는 DAG이 생기면 같은 pool에 물린다.

## 큐가 곧 백필이다

`document_attachment.parse_status`가 NULL인 첨부를 오래된 것부터 집는다. NULL이 "아직 해 보지
않았다"라서 **신규와 과거 백필이 같은 코드다.** 파일이 바뀐 첨부(`parsed_sha256`이 현재
`sha256`과 다른 것)도 같은 큐에 선다.

## 실패와 재시도

**가르는 축은 "다시 하면 되나"다.** 다음 실행이 고칠 수 있는 실패는 세고 넘어가고, 다시
해도 같은 답인 실패는 태스크를 죽인다. 어느 쪽이든 **배치는 끝까지 돈다** — 첨부 하나가
나머지 마흔아홉 건의 저장을 막아서는 안 된다(예외 하나가 나머지를 통째로 막은 적이 있다,
`document_ingestion_hourly` 2026-08-15).

### 상태로 확정하고 큐에서 빼는 것 — 실패로 세지 않는다

- 열리지 않는 파일은 `failed`, 암호가 걸렸으면 `unsupported`로 **확정**한다. 제공처의 파일이
  그런 것이라 우리가 고칠 것이 없고, 다시 열어도 같은 답이다. **DB의 행이 그 기록이라 로그보다
  오래 간다** — `parse_status`로 언제든 조회된다.
- 페이지 일부가 깨지면 `partial`로 저장하고 **읽은 만큼은 남긴다.**
- 다만 **건수는 요약 로그에 싣는다.** 안 그러면 50건이 전부 `failed`인 배치와 50건이 전부
  성공한 배치의 로그가 같아 보인다.

### 다시 하면 되는 실패 — 세고 넘어간다

다음 실행이 같은 큐를 곧 다시 보기 때문이다 — 첨부 하나로 죽이면 경보만 늘고 고쳐지는 것은
없다. `document_body_hourly`·`document_ingestion_hourly`와 같은 계열이다.

- 파일이 없거나 디스크 I/O가 죽으면(`OSError`) 상태를 남기지 않는다. 마운트가 흔들린 것이라
  다음 실행이 다시 집는다.
- 디스크의 파일이 행의 SHA와 다르면 UPDATE가 0행이라 저장되지 않고, 그 첨부를 실패로 센다.
  행이 새 SHA로 갱신되면 다음 실행이 다시 집는다.
- 전부 이 실패면 `OSError`로 올려 `retries`가 산다 — 마운트·권한처럼 잠시 뒤 풀리는 문제다.
  마운트 자체가 없을 때만 `AirflowFailException`으로 즉시 죽인다.

### 다시 해도 같은 답인 실패 — 하나라도 있으면 죽인다

**우리 쪽(코드·데이터·DB) 문제다.** 재시도가 고칠 수 없으니 조용히 넘기면 아무도 안 고친다.
잡아서 배치는 끝내되 마지막에 태스크를 죽인다.

- **예상하지 못한 파서 예외.** `OSError`가 아닌 것은 전부 여기다 — 파서 버그이거나 우리가
  모르는 입력이고, 다음 실행도 같은 파일에 같은 코드를 돌린다.
- **저장 실패.** 파싱은 됐는데 못 썼다. 2026-09-01에 PyMuPDF가 낸 NUL(0x00)을 PostgreSQL의
  `text`가 못 담아 psycopg가 죽었다. 그때 이 자리가 예외를 삼켰다면 run은 초록이고 첨부
  스물두 건은 텍스트 없이 남았을 것이다 — **터졌기 때문에 발견됐다.**

원인은 잃지 않는다. 첨부마다 `logger.exception`으로 traceback을 남기고(Sentry 이벤트가 된다),
마지막 예외는 첫 원인을 `raise ... from`으로 잇는다.

## 무엇을 세는가

첨부마다 전체 페이지 수와 **글자가 안 나온 페이지 수**를 남긴다. 그 비율이 외부 Vision을
켤지 정하는 유일한 근거다(`docs/analysis/pdf-vision-analysis.md` 5절). 지금은 아무 것도
외부로 보내지 않는다 — 이 DAG에는 네트워크 클라이언트도 API 키도 없다.

## 필요한 환경

- `CONNECTION_ID`가 가리키는 Airflow 연결. 접속 정보는 `AIRFLOW_CONN_FINANCE`가 갖는다.
- **`FILE_ROOT`(`/opt/airflow/files`)가 마운트돼 있어야 한다.** 없으면 태스크를 즉시
  실패시킨다 — 조용히 건너뛰면 큐만 돌고 텍스트는 한 건도 안 남는데 성공으로 표시된다.
- 이미지에 PyMuPDF(`==1.28.2`)가 들어 있어야 한다.
- **Airflow pool `pdf_parse`(슬롯 1).** 코드로 생기지 않는다 — UI나 `airflow pools set`으로
  만든다. 없으면 태스크가 scheduled에 멈춘 채 `non-existent pool` 경고만 남고 실패도 경보도
  없다. 운영에는 있다(2026-09-01 확인).
"""

import logging
from contextlib import closing
from datetime import timedelta
from typing import Any

import pendulum
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Param, dag, task
from airflow.sdk.exceptions import AirflowFailException

from modules.collectors.document.pdf import (
    DEFAULT_FILE_ROOT,
    AttachmentPdfParser,
    pending_attachments,
)
from modules.utility import CONNECTION_ID, KST_TIMEZONE, atomic

logger = logging.getLogger(__name__)

# 한 실행이 처리할 첨부 수. 파일 하나가 수십 쪽이라 본문 수집(200건)보다 작게 잡는다.
DEFAULT_BATCH_SIZE = 50

# 제공처 파일이 그런 것이라 우리가 고칠 것이 없는 상태. 상태로 확정해 큐에서 빼되 건수는 센다.
SETTLED_BAD_STATUSES = ("failed", "unsupported")


def _connection() -> Any:
    return PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()


@dag(
    dag_id="document_attachment_parse_hourly",
    dag_display_name="📄 첨부 PDF 파싱",
    description="받아 둔 첨부 PDF에서 텍스트와 표를 뽑아 첨부 행에 채운다. 외부 호출은 없다.",
    # 분 단위 cron이라 시간대가 값을 바꾸지 않는다. 15분(본문·첨부 수집) 뒤에 선다.
    schedule="20 * * * *",
    start_date=pendulum.datetime(2026, 8, 31, tz=KST_TIMEZONE),  # KST 2026-08-31 00:00 = UTC 2026-08-30 15:00
    catchup=False,
    max_active_runs=1,
    # 1시간 주기라 다음 run이 멀지 않다. 짧게 두 번 시도한다.
    default_args={"retries": 2, "retry_delay": timedelta(minutes=10)},
    params={
        "batch_size": Param(
            DEFAULT_BATCH_SIZE,
            type="integer",
            minimum=1,
            maximum=500,
            title="한 실행이 처리할 첨부 수",
            description="아직 파싱해 보지 않은 첨부를 오래된 것부터 이만큼 집는다. 백필을 서두르려면 올린다.",
        ),
    },
    doc_md=__doc__,
    tags=["documents", "hourly"],
)
def document_attachment_parse_hourly():
    @task(task_display_name="첨부 PDF 파싱", pool="pdf_parse")
    def parse(**context) -> int:
        file_root = DEFAULT_FILE_ROOT
        if not file_root.is_dir():
            # 마운트가 없는데 조용히 넘기면 큐만 돌고 텍스트는 한 건도 안 남는다.
            raise AirflowFailException(f"{file_root} is not mounted; attach the file volume first")

        parser = AttachmentPdfParser(file_root)
        batch_size = int(context["params"]["batch_size"])

        with closing(_connection()) as connection:
            waiting = pending_attachments(connection, batch_size)

        if not waiting:
            logger.info("No attachment is waiting to be parsed")
            return 0

        stored = 0
        pages = 0
        unreadable = 0
        # 다음 실행이 고칠 수 있는 실패. 전부 이것일 때만 죽인다.
        failures: list[str] = []
        # 다시 해도 같은 답인 실패(파서 버그·저장 실패). **하나라도 있으면** 배치를 끝내고 죽인다.
        unrecoverable: list[str] = []
        first_error: Exception | None = None
        # 제공처 파일이 손상됐거나 암호가 걸린 것. 상태로 확정해 큐에서 빼되 건수는 남긴다.
        settled_bad = 0
        for candidate in waiting:
            try:
                result = parser.parse(candidate)
            except OSError as error:
                # 파일이 없거나 디스크가 죽었다. 마운트가 흔들린 것이라 다음 실행이 다시 집는다.
                logger.warning("attachment %s could not be read: %s", candidate.id, error)
                failures.append(f"{candidate.id}({error})")
                continue
            except Exception as error:
                # `OSError`가 아닌 것은 파서 버그이거나 우리가 모르는 입력이다. 다음 실행도 같은
                # 파일에 같은 코드를 돌리므로 아래에서 죽인다. 배치는 계속 돈다.
                logger.exception("attachment %s hit an error we did not foresee", candidate.id)
                unrecoverable.append(f"{candidate.id}({error})")
                first_error = first_error or error
                continue

            if result.status in SETTLED_BAD_STATUSES:
                # 제공처 파일이 손상됐거나 암호가 걸렸다. 상태를 남겨 큐에서 빼고 건수만 센다.
                settled_bad += 1

            if result.failures:
                # 페이지 단위 실패는 저장하지 않으므로 여기서 올린다. 첨부는 성공으로 센다.
                logger.warning("attachment %s parsed partially: %s", candidate.id, "; ".join(result.failures))

            try:
                with closing(_connection()) as connection, atomic(connection):
                    updated = parser.store(connection, result)
            except Exception as error:
                # 파싱은 됐는데 못 썼다. **삼키지 않는다** — 나머지 첨부는 계속 저장하되
                # 아래에서 태스크를 죽인다. 여기서 traceback을 남겨 원인이 Sentry에 간다.
                logger.exception("attachment %s could not be stored", candidate.id)
                unrecoverable.append(f"{candidate.id}({error})")
                first_error = first_error or error
                continue

            if not updated:
                # 읽는 동안 행의 SHA가 바뀌었다. 텍스트는 버리고 다음 실행이 새 파일로 다시 집는다.
                logger.warning("attachment %s changed while parsing; nothing stored", candidate.id)
                failures.append(f"{candidate.id}(sha changed while parsing)")
                continue

            stored += updated
            pages += result.page_count
            unreadable += result.unreadable_page_count

        # 페이지 두 수가 외부 Vision을 켤지 정하는 근거다. 분모 없이 세지 않는다.
        # `settled_bad`가 없으면 50건이 전부 손상인 배치와 전부 성공인 배치의 로그가 같아 보인다.
        # 죽기 전에 남긴다.
        logger.info(
            "Parsed %s of %s attachments: %s pages, %s unreadable, %s settled as unreadable files",
            stored,
            len(waiting),
            pages,
            unreadable,
            settled_bad,
        )

        if unrecoverable:
            # 파서 버그이거나 파싱한 텍스트를 못 쓴 것이다. 그 첨부는 상태가 안 남아 다음 실행이
            # 다시 집지만 같은 코드가 같은 답을 낸다 — 초록으로 끝내면 아무도 고치지 않는다.
            # 첫 원인을 이어 붙여 추적이 여기서 끊기지 않게 한다.
            raise RuntimeError(
                f"{len(unrecoverable)} of {len(waiting)} attachments failed in a way that "
                f"cannot be retried: {'; '.join(unrecoverable)}"
            ) from first_error
        if len(failures) == len(waiting):
            # 전부 실패했으면 우리 쪽 문제일 가능성이 크다(마운트·권한). 잠시 뒤 풀릴 수 있어
            # 재시도가 살아 있는 예외로 올린다 — AirflowFailException은 retries를 건너뛴다.
            raise OSError(f"Every attachment failed: {'; '.join(failures)}")
        if failures:
            logger.warning("%s of %s attachments failed: %s", len(failures), len(waiting), "; ".join(failures))

        return stored

    parse()


document_attachment_parse_hourly = document_attachment_parse_hourly()
