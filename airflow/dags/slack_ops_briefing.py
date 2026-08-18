"""수집이 지난 하루 제대로 돌았는지를 Slack에 보낸다.

`docs/slack-report-design.md` 3부다. `source_record` 한 테이블이 거의 모든 답을 준다.

## 왜 올그린에도 보내나

이 리포트가 나머지를 감시하는 쪽이다. 침묵을 정상 신호로 쓰면 고장으로 인한 침묵과 구분할
수 없다. 하루 한 번은 견딜 만한 소음이다. 다만 정상일 때는 LLM을 부르지 않는다. 템플릿
한 줄이 이미 할 말을 다 한다.

## source_record에 안 잡히는 둘

환율(`exchange_rate_daily`)은 계보 컬럼이 없어 고시 신선도로, 문서 평가
(`document_assessment_hourly`)는 새 수집이 아니라 밀린 건수로 본다. `modules/briefing/ops.py`
docstring에 이유가 있다.

## 필요한 환경

- `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_OPS`.
- `XAI_API_KEY`. 문제가 있을 때만 부른다.
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

from modules.briefing import ops
from modules.briefing.comment import BriefingCommentator, CommentError
from modules.llm import LlmError, briefing_model
from modules.slack import SlackError, post_message
from modules.utility import CONNECTION_ID, KST_TIMEZONE

logger = logging.getLogger(__name__)

# KST 매일 08:00 = UTC 전일 23:00. 아침 수집 DAG들이 끝난 뒤라 밤사이 실행이 전부 잡힌다.
SCHEDULE = "0 8 * * *"

REPORT_NAME = "수집 운영 현황"


def _connection() -> Any:
    return PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()


def _slack_settings() -> tuple[SecretStr, str]:
    token = os.environ.get("SLACK_BOT_TOKEN")
    channel = os.environ.get("SLACK_CHANNEL_OPS")
    if not token or not channel:
        raise AirflowFailException("SLACK_BOT_TOKEN and SLACK_CHANNEL_OPS are required")
    return SecretStr(token), channel


@dag(
    dag_id="slack_ops_briefing",
    dag_display_name="💬 수집 운영 현황 (Slack)",
    description="지난 24시간 수집 성공·실패·무소식을 Slack에 보낸다. 정상이어도 보낸다.",
    schedule=SCHEDULE,
    start_date=pendulum.datetime(2026, 8, 18, tz=KST_TIMEZONE),  # KST 2026-08-18 00:00 = UTC 2026-08-17 15:00
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 1, "retry_delay": timedelta(minutes=2)},
    doc_md=__doc__,
    tags=["slack", "briefing", "ops"],
)
def slack_ops_briefing():
    @task(task_display_name="수집 운영 현황 발송")
    def send_briefing() -> str:
        token, channel = _slack_settings()
        now = datetime.now(UTC)

        connection = _connection()
        try:
            summary = ops.collect_summary(connection, now)
        finally:
            connection.close()

        comment, comment_error = _comment(summary)
        blocks = ops.render_blocks(summary, comment, comment_error)
        text = ops.render_text(summary)

        try:
            return post_message(token, channel, text=text, blocks=blocks)
        except SlackError as error:
            raise AirflowFailException(str(error)) from error

    def _comment(summary: ops.OpsSummary) -> tuple[str | None, str | None]:
        """정상이면 부르지 않는다. "모든 수집 정상" 위에 덧붙일 말이 없다."""
        if summary.is_healthy:
            return None, None
        try:
            return BriefingCommentator(briefing_model()).comment(REPORT_NAME, ops.comment_input(summary)), None
        except (ConnectionError, LlmError, CommentError) as error:
            logger.warning("briefing comment failed; sending the tables without it: %s", error)
            return None, str(error)

    send_briefing()


slack_ops_briefing = slack_ops_briefing()
