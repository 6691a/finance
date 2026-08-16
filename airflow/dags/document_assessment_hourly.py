"""수집한 문서를 LLM으로 태깅하고 점수를 매긴다.

`docs/economic-document-archive-design.md` 2단계의 LLM 절반이다. 수집
(`document_ingestion_hourly`)과 나뉘어 있어 **여기가 못 돌아도 원문 수집은 계속된다.** 모델
장애가 수집을 막지 않는다는 것이 설계의 첫 결정이다.

## 무엇을 남기고 무엇을 남기지 않나

문서를 종목·지표에 연결하고(`document_instrument`, `document_indicator`) 방향과 0~8점을
`document`에 적는다. **점수로 문서를 버리지 않는다.** 무엇을 쓸지는 4단계 리포트 프롬프트가
정한다. 지금 버리면 나중에 기준을 바꿀 때 되돌릴 수 없다.

## 실패는 상태가 아니다

한 문서의 평가가 실패하면 그 문서는 `assessed_at`이 NULL인 채로 남고 다음 실행이 다시
집는다. 실패를 "보류" 같은 상태로 바꾸지 않는다. 응답 형식이 깨지면 한 번만 교정을 요청하고,
두 번째도 실패하면 넘어간다.

## 필요한 환경

- `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_CHAT_MODEL`. **셋 중 하나라도 없으면 태스크를 즉시
  실패시킨다.** DAG은 `config.yaml`을 읽지 못하므로 환경변수로 준다.
- `LLM_PERSPECTIVE`는 선택이고 기본이 `global`이다. 세계에서 일어난 일이 한국 시장에 닿는
  경로까지 보라는 뜻이다. `korea`는 국내 직접 관련만, `us`는 미국 시장의 눈으로 본다.
  **바꾸면 이미 평가한 문서가 전부 재평가 대상이 된다**(`prompt_version`에 관점이 들어간다).
- `CONNECTION_ID`가 가리키는 Airflow 연결.

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `batch_size` | 50 | 한 번 실행에서 평가할 문서 수. 예산이 한 번에 새지 않게 막는다 |

    airflow dags trigger document_assessment_hourly --conf '{"batch_size": 10}'
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pendulum
from airflow.exceptions import AirflowFailException
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Param, dag, get_current_context, task

from modules.assessment import (
    DEFAULT_BATCH_SIZE,
    AssessmentError,
    LlmSettings,
    assess,
    filter_tags,
    load_candidates,
    pending_documents,
    store_assessment,
)
from modules.llm import chat_client
from modules.utility import CONNECTION_ID, KST_TIMEZONE

logger = logging.getLogger(__name__)

BATCH_SIZE_PARAM = "batch_size"


def _connection() -> Any:
    # 반환 타입은 provider 버전에 따라 psycopg2/psycopg3 래퍼로 갈린다. 런타임 객체는
    # 어느 쪽이든 PEP 249 연결이라 commit·rollback을 갖는다.
    return PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()


@dag(
    dag_id="document_assessment_hourly",
    dag_display_name="🏷️ 경제 문서 LLM 태깅",
    description="수집한 문서를 LLM으로 종목·지표에 연결하고 점수를 매긴다. 문서를 버리지 않는다.",
    # KST 매시 25분 = UTC 매시 25분. 수집(매시 05분)이 끝난 뒤에 돈다.
    schedule="25 * * * *",
    start_date=pendulum.datetime(2026, 8, 15, tz=KST_TIMEZONE),  # KST 2026-08-15 00:00 = UTC 2026-08-14 15:00
    catchup=False,
    max_active_runs=1,
    # 실패해도 다음 실행이 밀린 문서를 다시 집는다. 재시도를 길게 끌 이유가 없다.
    default_args={"retries": 1, "retry_delay": timedelta(minutes=10)},
    params={
        BATCH_SIZE_PARAM: Param(
            DEFAULT_BATCH_SIZE,
            type="integer",
            minimum=1,
            maximum=500,
            title="한 번에 평가할 문서 수",
            description="예산이 한 번에 새지 않게 막는 상한이다. 밀린 문서는 다음 실행이 집는다.",
        ),
    },
    doc_md=__doc__,
    tags=["documents", "llm", "hourly"],
)
def document_assessment_hourly():
    @task(task_display_name="문서 태깅·점수")
    def evaluate() -> int:
        context = get_current_context()
        params = dict(context.get("params") or {})
        batch_size = int(params.get(BATCH_SIZE_PARAM) or DEFAULT_BATCH_SIZE)

        try:
            settings = LlmSettings.from_environment()
        except AssessmentError as error:
            # 설정 누락이라 재시도해도 같다. 메시지에 키 값은 들어가지 않는다.
            raise AirflowFailException(str(error)) from error

        connection = _connection()
        try:
            candidates = load_candidates(connection)
            documents = pending_documents(connection, batch_size, settings.prompt_revision)
        finally:
            connection.close()

        if not documents:
            logger.info("No documents are waiting for assessment")
            return 0
        if not candidates.instruments and not candidates.indicators:
            # 후보가 비면 태그를 만들 수 없고, 그건 마스터 시드가 빠진 상태다.
            raise AirflowFailException("No instrument or indicator candidates; seed the masters first")

        client = chat_client(settings.base_url, settings.api_key.get_secret_value())
        assessed_at = datetime.now(UTC)

        assessed = 0
        failures = 0
        for document in documents:
            try:
                assessment = assess(client, settings, document, candidates)
            except AssessmentError as error:
                # 문서는 태그 없이 남는다. 다음 실행이 다시 집는다.
                logger.warning("document %s could not be assessed: %s", document.id, error)
                failures += 1
                continue
            except Exception as error:  # noqa: BLE001 - 제공처 예외 종류가 열려 있다
                logger.warning("document %s failed to reach the model: %s", document.id, type(error).__name__)
                failures += 1
                continue

            instruments, indicators = filter_tags(assessment, candidates, document.id)

            # 문서 하나가 트랜잭션 하나다. 앞의 성공을 뒤의 실패가 되돌리지 않는다.
            connection = _connection()
            try:
                store_assessment(
                    connection,
                    document,
                    assessment,
                    instruments,
                    indicators,
                    settings.chat_model,
                    assessed_at,
                    settings.prompt_revision,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
            assessed += 1

        if assessed == 0 and failures:
            # 하나도 성공하지 못했으면 모델이나 설정 쪽 문제다. 재시도할 값어치가 있다.
            raise ConnectionError(f"Every assessment failed ({failures} documents)")

        logger.info("Assessed %s documents with %s (%s failed)", assessed, settings.prompt_revision, failures)
        return assessed

    evaluate()


document_assessment_hourly = document_assessment_hourly()
