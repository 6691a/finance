"""종목 이벤트의 기대치·실제값을 추출하고 서프라이즈를 판정한다.

`docs/market-thesis/8-expectation.md`의 실행 절반이다. 삼성전자 주주환원 발표(2026-08-22)가
시장 기대치에 못 미쳐 하락했는데, 기대치가 리포트 산문에만 있어 시스템이 "기대 대비
미달"을 만들지 못했다 — 그 빈 칸을 채운다.

## 흐름

```
extract_claims (LLM)  →  judge_outcomes (LLM 없음)  →  notify_slack
```

- **extract_claims**: 평가가 끝나고 종목 태그가 붙은 문서에서 기대·실제 주장을 구조화해
  `stock_event_claim`에 쌓는다. 주장 0건도 원장(`stock_event_extraction`)에 남아 같은
  문서를 다시 뽑지 않는다. 실적(earnings)의 실제값은 뽑지 않는다 — `earnings_fact`가
  원본이다.
- **judge_outcomes**: 실제값이 생긴 이벤트를 발표 전 기대들과 대조해 beat/meet/miss 행을
  `stock_event_outcome`에 남긴다. 집계·분류는 전부 순수 함수다(thesis 숫자 규칙).
  판정은 첫 성공본 불변이라 재실행이 판정을 다시 내지 않는다.
- **notify_slack**: 이번 실행이 **새로 쓴** 판정만 보낸다. 재실행은 RETURNING 0행이라
  발송이 없다. 추출(LLM·비용 큼)과 발송을 나누는 이유는 thesis와 같다 — Slack이 죽어도
  LLM을 다시 부르지 않는다.

## 스케줄이 :45인 이유

수집이 매시 :05, 평가가 매시 :25다. 추출 대상 조건이 "평가 완료 + 종목 태그"라 평가 뒤에
돌아야 그 시간 문서가 대상에 든다. readiness guard는 없다 — thesis처럼 "이 시각의
데이터"가 아니라 "쌓인 것 중 안 뽑은 것"이 대상이라, 평가가 늦으면 다음 시간이 집는다.

## 필요한 환경

- `OPENAI_API_KEY`. 어떤 모델을 부를지는 `modules/llm.py`의 `expectation_model()`이 코드로
  정하고 키는 그 LangChain 클래스가 자기 이름으로 읽는다.
- `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_MARKET` — thesis·시장 브리핑과 같은 채널을 재사용한다.
- `CONNECTION_ID`가 가리키는 Airflow 연결.
- `LANGSMITH_TRACING`을 켜면 프롬프트와 문서 본문이 LangSmith로 나간다.

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `batch_size` | 50 | 한 번 실행에서 추출할 문서 수. 예산이 한 번에 새지 않게 막는다 |

    airflow dags trigger event_expectation_hourly --conf '{"batch_size": 10}'
"""

import logging
import os
from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import Any

import pendulum
from airflow.exceptions import AirflowFailException
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Param, dag, get_current_context, task
from pydantic import SecretStr

from modules.expectation_domain import (
    DEFAULT_BATCH_SIZE,
    ExtractionError,
)
from modules.expectation_extraction import (
    ExpectationExtractor,
    filter_claims,
)
from modules.expectation_judgment import (
    ExpectationStore,
    JudgedOutcome,
    render_blocks,
    render_text,
)
from modules.llm import LlmError, RetryableLlmError, expectation_model, model_name
from modules.slack import SlackClient, SlackError
from modules.utility import CONNECTION_ID, KST_TIMEZONE, atomic

logger = logging.getLogger(__name__)

BATCH_SIZE_PARAM = "batch_size"


def _connection() -> Any:
    # 반환 타입은 provider 버전에 따라 psycopg2/psycopg3 래퍼로 갈린다. 런타임 객체는
    # 어느 쪽이든 PEP 249 연결이라 commit·rollback을 갖는다.
    return PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()


@dag(
    dag_id="event_expectation_hourly",
    dag_display_name="📐 종목 이벤트 기대치·서프라이즈",
    description="문서에서 이벤트 기대·실제 주장을 추출하고 기대 대비 발표를 판정해 알린다.",
    # KST 매시 45분 = UTC 매시 45분. 수집(:05)·평가(:25)가 끝난 뒤에 돈다.
    schedule="45 * * * *",
    start_date=pendulum.datetime(2026, 8, 24, tz=KST_TIMEZONE),  # KST 2026-08-24 00:00 = UTC 2026-08-23 15:00
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
            title="한 번에 추출할 문서 수",
            description="예산이 한 번에 새지 않게 막는 상한이다. 밀린 문서는 다음 실행이 집는다.",
        ),
    },
    doc_md=__doc__,
    tags=["documents", "llm", "hourly", "events"],
)
def event_expectation_hourly():
    @task(task_display_name="기대·실제 주장 추출")
    def extract_claims() -> int:
        context = get_current_context()
        params = dict(context.get("params") or {})
        batch_size = int(params.get(BATCH_SIZE_PARAM) or DEFAULT_BATCH_SIZE)

        connection = _connection()
        try:
            documents = ExpectationStore(connection).pending(batch_size)
        finally:
            connection.close()

        if not documents:
            logger.info("No documents are waiting for claim extraction")
            return 0

        # 어떤 모델을 부를지는 `modules/llm.py`가 정한다. 키는 그쪽 LangChain 클래스가 읽는다.
        model = expectation_model()
        extractor = ExpectationExtractor(model)
        extracted_at = datetime.now(UTC)

        stored = 0
        claim_total = 0
        format_failures: list[str] = []
        retryable_failures: list[str] = []
        for document in documents:
            try:
                response = extractor.extract(document)
            except ExtractionError as error:
                # 문서는 원장에 오르지 않고 다음 실행이 다시 집는다.
                logger.warning("document %s could not be extracted: %s", document.id, error)
                format_failures.append(str(error))
                continue
            except (RetryableLlmError, ConnectionError) as error:
                logger.warning("document %s hit a retryable LLM error: %s", document.id, error)
                retryable_failures.append(str(error))
                continue
            except LlmError as error:
                # 인증·잘못된 요청은 재시도해도 같다. 이미 저장한 문서는 커밋된 채로 남는다.
                raise AirflowFailException(f"Non-retryable LLM failure at document {document.id}: {error}") from error

            claims = filter_claims(response, document)
            # 문서 하나가 트랜잭션 하나다. 앞의 성공을 뒤의 실패가 되돌리지 않는다.
            with closing(_connection()) as store_connection, atomic(store_connection):
                ExpectationStore(store_connection).store_extraction(
                    document, claims, model_name(model), extracted_at
                )
            stored += 1
            claim_total += len(claims)

        if retryable_failures:
            raise RetryableLlmError(
                f"Retryable LLM failure ({len(retryable_failures)} documents): {retryable_failures[0]}"
            )
        if stored == 0 and format_failures:
            # 모델에 닿긴 했는데 응답 형식이 전부 깨졌다. 문서 하나의 문제가 아니라
            # 프롬프트나 모델 쪽 문제이므로 재시도해도 같다.
            raise AirflowFailException(
                f"Every extraction failed ({len(format_failures)} documents): {format_failures[0]}"
            )

        logger.info(
            "Extracted %s claims from %s documents (%s format failures)", claim_total, stored, len(format_failures)
        )
        return stored

    @task(task_display_name="서프라이즈 판정")
    def judge_outcomes(extracted: int) -> list[dict[str, Any]]:
        del extracted  # 의존성 선언용이다. 추출이 끝난 뒤의 주장까지 판정에 들어가야 한다.
        context = get_current_context()
        dag_run_id = str(context["run_id"])
        with closing(_connection()) as connection, atomic(connection):
            judged = ExpectationStore(connection).judge(dag_run_id)
        logger.info("Wrote %s new outcome rows", len(judged))
        return [outcome.model_dump(mode="json") for outcome in judged]

    @task(task_display_name="Slack 알림")
    def notify_slack(judged: list[dict[str, Any]]) -> str:
        if not judged:
            logger.info("No new outcomes to notify")
            return ""
        outcomes = [JudgedOutcome.model_validate(item) for item in judged]
        token = os.environ.get("SLACK_BOT_TOKEN")
        channel = os.environ.get("SLACK_CHANNEL_MARKET")
        if not token or not channel:
            raise AirflowFailException("SLACK_BOT_TOKEN and SLACK_CHANNEL_MARKET are required")
        try:
            return SlackClient(SecretStr(token)).post_message(
                channel,
                text=render_text(outcomes),
                blocks=render_blocks(outcomes),
            )
        except SlackError as error:
            # 토큰·채널·블록이 틀렸다. 다시 보내도 같은 결과다.
            raise AirflowFailException(str(error)) from error

    notify_slack(judge_outcomes(extract_claims()))


event_expectation_hourly = event_expectation_hourly()
