"""수집한 문서를 LLM으로 태깅하고 점수를 매긴다.

`docs/economic-document-archive-design.md` 2단계의 LLM 절반이다. 수집
(`document_ingestion_hourly`)과 나뉘어 있어 **여기가 못 돌아도 원문 수집은 계속된다.** 모델
장애가 수집을 막지 않는다는 것이 설계의 첫 결정이다.

## 무엇을 남기고 무엇을 남기지 않나

문서를 종목·지표에 연결하고(`document_instrument`, `document_indicator`) 방향과 0~8점을
`document`에 적는다. **점수로 문서를 버리지 않는다.** 무엇을 쓸지는 4단계 리포트 프롬프트가
정한다. 지금 버리면 나중에 기준을 바꿀 때 되돌릴 수 없다.

## 평가 전에 중복을 연결한다

같은 출처에서 제목만 조금 다른 같은 기사([속보] 스텁 vs 본기사)를 `modules/dedup.py`가
제목 유사도로 묶어 `canonical_document_id`에 연결한다. 연결된 문서는 평가와 브리핑에서
빠진다 — 같은 기사를 두 번 평가하지 않고 브리핑에 두 번 싣지 않는다. **버리는 것이
아니다.** 문서는 그대로 남고, 오판이면 컬럼을 NULL로 되돌리면 끝이다. 중복 연결이
실패해도 평가는 돈다(`trigger_rule="all_done"`).

## 실패는 상태가 아니다

한 문서의 평가가 실패하면 그 문서는 `assessed_at`이 NULL인 채로 남고 다음 실행이 다시
집는다. 실패를 "보류" 같은 상태로 바꾸지 않는다. 응답 형식이 깨지면 한 번만 교정을 요청하고,
두 번째도 실패하면 넘어간다.

**넘어가는 것은 그 문서만의 문제일 때다.** 모델에 닿지 못한 실패는 삼키지 않는다. 키가
틀렸거나 네트워크가 끊긴 것은 남은 문서 전부가 똑같이 실패할 문제라, 예외를 결과로 바꾸지
않고 그대로 올려 태스크를 죽인다. 원인이 로그에 스택과 함께 남는 편이 "0건 처리" 성공보다
낫다. 재시도할 값어치가 없는 것(`LlmError`)만 `AirflowFailException`으로 바꾸고,
`ConnectionError`는 그대로 두어 Airflow가 재시도하게 한다.

팬아웃은 중간에 끊기지 않는다. `Send`로 갈라진 노드가 한 superstep에서 다 돌고 나서 예외가
올라오므로, 키가 틀린 실행은 배치 전체를 한 번씩 부르고 죽는다. 그 낭비의 상한이
`batch_size`다.

## 필요한 환경

- `XAI_API_KEY`. 어떤 모델을 부를지는 `modules/llm.py`의 `document_model()`이 코드로 정하고
  키는 그 LangChain 클래스가 자기 이름으로 읽는다. 키가 없으면 모델을 만들 때 실패한다.
  DAG은 `config.yaml`을 읽지 못하므로 환경변수로 준다.
- `LLM_PERSPECTIVE`는 선택이고 기본이 `global`이다. 세계에서 일어난 일이 한국 시장에 닿는
  경로까지 보라는 뜻이다. `korea`는 국내 직접 관련만, `us`는 미국 시장의 눈으로 본다.
  **바꾸면 이미 평가한 문서가 전부 재평가 대상이 된다**(`prompt_version`에 관점이 들어간다).
- `LLM_MAX_CONCURRENCY`는 선택이고 기본이 4다. 한 실행에서 동시에 부를 문서 수이며 제공처
  rate limit에 걸리면 내린다. 1이면 순차다.
- `LANGSMITH_TRACING`과 키를 주면 프롬프트·응답·토큰이 LangSmith에 남는다. 비우면 아무 것도
  보내지 않는다. **켜면 문서 본문이 외부로 나간다.**
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
    AssessmentBatch,
    AssessmentError,
    DocumentAssessor,
    LlmSettings,
    filter_tags,
    load_candidates,
    pending_documents,
    store_assessment,
)
from modules.dedup import link_duplicates
from modules.llm import LlmError, document_model, model_name
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
    @task(task_display_name="중복 연결")
    def dedup() -> int:
        # 평가 전에 같은 기사([속보] 스텁 vs 본기사)를 대표에 연결한다. 연결된 문서는
        # 평가·브리핑에서 빠진다. 외부 API가 없어 실패는 DB 오류뿐이고, 그건 그대로 올려
        # Airflow가 재시도한다.
        connection = _connection()
        try:
            outcome = link_duplicates(connection)
        finally:
            connection.close()
        logger.info("Checked %s documents, linked %s duplicates", outcome.checked, outcome.linked)
        return outcome.linked

    # 중복 연결이 실패해도 평가는 돈다. 못 걸러진 문서는 평가되더라도 다음 실행이
    # 연결하고 브리핑 필터가 가린다. 손해는 LLM 호출 몇 건이다.
    @task(task_display_name="문서 태깅·점수", trigger_rule="all_done")
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

        # 어떤 모델을 부를지는 `modules/llm.py`가 정한다. 키는 그쪽 LangChain 클래스가 읽는다.
        model = document_model()
        batch = AssessmentBatch(DocumentAssessor(model, settings), settings.max_concurrency)
        # 평가는 그래프가 한 번에 돌린다. 응답 형식이 깨진 문서만 결과 한 건으로 돌아오고,
        # 모델에 닿지 못한 실패는 예외 그대로 여기까지 올라온다.
        try:
            results = batch.run(documents, candidates)
        except LlmError as error:
            # 키·요청 형식·권한 문제라 10분 뒤에 다시 불러도 같은 답이다.
            raise AirflowFailException(str(error)) from error
        # `ConnectionError`는 잡지 않는다. 네트워크·타임아웃은 재시도할 값어치가 있어
        # Airflow가 그대로 재시도한다.
        by_id = {document.id: document for document in documents}
        assessed_at = datetime.now(UTC)

        assessed = 0
        failures = 0
        for result in results:
            document = by_id[result.document_id]
            if result.assessment is None:
                # 문서는 태그 없이 남는다. 다음 실행이 다시 집는다.
                failures += 1
                continue

            assessment = result.assessment
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
                    model_name(model),
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
            # 여기까지 왔다는 것은 모델에 닿긴 했는데 응답 형식이 전부 깨졌다는 뜻이다.
            # 문서 하나의 문제가 아니라 프롬프트나 모델 쪽 문제이므로 재시도해도 같다.
            # 원인을 지어내지 않고 첫 문서가 남긴 이유를 그대로 싣는다.
            reason = next((result.error for result in results if result.error), "unknown")
            raise AirflowFailException(f"Every assessment failed ({failures} documents): {reason}")

        logger.info("Assessed %s documents with %s (%s failed)", assessed, settings.prompt_revision, failures)
        return assessed

    dedup() >> evaluate()


document_assessment_hourly = document_assessment_hourly()
