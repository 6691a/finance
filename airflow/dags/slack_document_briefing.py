"""최근에 평가한 문서를 묶어 Slack에 보낸다.

`docs/slack-report-design.md` 2부다. `document_assessment_hourly`가 매시 채우는
`value_score`를 여기서 처음으로 읽는다. 저장 단계는 점수로 문서를 버리지 않고, 무엇을
보여 줄지는 이 리포트가 정한다.

## 0건에도 보낸다

`document_assessment_hourly`는 `source_record`를 남기지 않아 운영 리포트에서 보이지 않는다.
그래서 이 메시지가 평가 파이프라인의 생존 신호를 겸한다. 침묵이 정상 신호이면 고장으로 인한
침묵과 구분할 수 없다.

다만 0건일 때는 LLM을 부르지 않는다. 쓸 값이 없는데 요약을 시키면 없는 이야기를 지어낸다.

## 필요한 환경

- `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_DOCUMENT`. 시장 브리핑과 채널이 다르다.
- `XAI_API_KEY`. 모델은 `modules/llm.py`의 `briefing_model()`이 코드로 정한다.
- `CONNECTION_ID`가 가리키는 Airflow 연결.
"""

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import pendulum
from airflow.exceptions import AirflowFailException
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import dag, task
from pydantic import SecretStr

from modules.briefing import documents
from modules.briefing.comment import BriefingCommentator, CommentError
from modules.llm import LlmError, briefing_model
from modules.slack import SlackError, post_message
from modules.utility import CONNECTION_ID, KST_TIMEZONE

logger = logging.getLogger(__name__)

# KST 매일 08:00·17:00 = UTC 전일 23:00, 08:00. 아침에 밤사이 것을, 저녁에 낮 것을 본다.
SCHEDULE = "0 8,17 * * *"

REPORT_NAME = "문서 평가 브리핑"


def _connection() -> Any:
    return PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()


def _slack_settings() -> tuple[SecretStr, str]:
    token = os.environ.get("SLACK_BOT_TOKEN")
    channel = os.environ.get("SLACK_CHANNEL_DOCUMENT")
    if not token or not channel:
        raise AirflowFailException("SLACK_BOT_TOKEN and SLACK_CHANNEL_DOCUMENT are required")
    return SecretStr(token), channel


@dag(
    dag_id="slack_document_briefing",
    dag_display_name="💬 문서 평가 브리핑 (Slack)",
    description="최근 평가한 문서 집계와 점수 상위 문서를 Slack에 보낸다. 0건이어도 보낸다.",
    schedule=SCHEDULE,
    start_date=pendulum.datetime(2026, 8, 18, tz=KST_TIMEZONE),  # KST 2026-08-18 00:00 = UTC 2026-08-17 15:00
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 1, "retry_delay": timedelta(minutes=2)},
    doc_md=__doc__,
    tags=["slack", "briefing", "documents"],
)
def slack_document_briefing():
    @task(task_display_name="문서 평가 브리핑 발송")
    def send_briefing() -> str:
        token, channel = _slack_settings()
        now = datetime.now(UTC)

        connection = _connection()
        try:
            summary = documents.collect_summary(connection, now)
        finally:
            connection.close()

        comment, comment_error = _comment(summary)
        blocks = documents.render_blocks(summary, comment, comment_error)
        text = documents.render_text(summary)

        try:
            return post_message(token, channel, text=text, blocks=blocks)
        except SlackError as error:
            raise AirflowFailException(str(error)) from error

    def _comment(summary: documents.DocumentSummary) -> tuple[str | None, str | None]:
        """평가한 문서가 없으면 부르지 않는다. 요약할 값이 없다."""
        if summary.is_empty:
            return None, None
        try:
            return BriefingCommentator(briefing_model()).comment(REPORT_NAME, documents.comment_input(summary)), None
        except (ConnectionError, LlmError, CommentError) as error:
            logger.warning("briefing comment failed; sending the tables without it: %s", error)
            return None, str(error)

    send_briefing()


slack_document_briefing = slack_document_briefing()
