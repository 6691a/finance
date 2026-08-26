"""최근에 평가한 문서를 묶어 Slack에 보낸다.

`docs/briefing/slack-report-design.md` 2부다. `document_assessment_hourly`가 매시 채우는
`value_score`를 여기서 처음으로 읽는다. 저장 단계는 점수로 문서를 버리지 않고, 무엇을
보여 줄지는 이 리포트가 정한다.

하루 네 번(KST 08:00·12:00·15:30·20:00) 보내고, 창은 직전 발송 이후만 본다.
원래 아침 한 번에 24시간이었는데, 시장에 바로 반영되는 기사(자사주 매입 공시 등)가
다음날 아침에야 실려 늦었다. 슬롯 사이 창이 이어지므로 한 문서는 한 번만 실린다.

## 고르기는 모델이 한다

점수로는 후보 몇십 건을 자르고, 그 안에서 읽을 것과 주의할 것을 고르는 일은
`modules/briefing/picks.py`가 목록 전체를 한 번에 모델에 보여 주고 시킨다. 점수가 못 쓸 값이라서가
아니라 **상위 구간이 거의 동점이라** 그 순서에 뜻이 없고, 위험 여부·고른 이유·중복 제거는
점수가 답하지 않는 질문이기 때문이다. 선별이 실패하면 점수 순서 상위 몇 건으로 떨어지고
리포트는 그대로 나간다.

## 0건에도 보낸다

`document_assessment_hourly`는 `source_record`를 남기지 않아 운영 리포트에서 보이지 않는다.
그래서 이 메시지가 평가 파이프라인의 생존 신호를 겸한다. 침묵이 정상 신호이면 고장으로 인한
침묵과 구분할 수 없다.

다만 0건일 때는 LLM을 부르지 않는다. 고를 것이 없는데 고르라고 시키면 없는 이야기를 지어낸다.

## 필요한 환경

- `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_DOCUMENT`. 시장 브리핑과 채널이 다르다.
- `XAI_API_KEY`. 모델은 `modules/llm.py`의 `briefing_model()`이 코드로 정한다.
- `CONNECTION_ID`가 가리키는 Airflow 연결.
"""

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pendulum
from airflow.exceptions import AirflowFailException
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import dag, task
from airflow.timetables.trigger import MultipleCronTriggerTimetable
from pydantic import SecretStr

from modules.briefing import documents
from modules.slack import SlackClient, SlackError

if TYPE_CHECKING:
    from modules.briefing.picks import Pick
from modules.utility import CONNECTION_ID, KST_TIMEZONE

logger = logging.getLogger(__name__)

# 하루 네 번, 창은 직전 발송 이후만(`documents.window_hours_at`). 자사주 매입 공시처럼
# 시장에 바로 반영되는 기사가 다음날 아침에야 실리면 늦다(2026-08-19 SK하이닉스 실측).
# **`documents.SEND_SLOTS_KST`와 같은 목록이어야 한다** — 창 계산이 이 슬롯을 기준으로 잇는다.
SCHEDULE = MultipleCronTriggerTimetable(
    "0 8 * * *",  # KST 08:00 장 전 = UTC 전일 23:00
    "0 12 * * *",  # KST 12:00 점심 = UTC 03:00
    "30 15 * * *",  # KST 15:30 KRX 마감 = UTC 06:30
    "0 20 * * *",  # KST 20:00 NXT 마감 = UTC 11:00
    timezone=KST_TIMEZONE,
)


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
            summary = documents.collect_summary(
                connection, now, window_hours=documents.window_hours_at(now)
            )
        finally:
            connection.close()

        picks, pick_error = _pick(summary)
        blocks = documents.render_blocks(summary, picks, pick_error)
        text = documents.render_text(summary, picks)

        try:
            return SlackClient(token).post_message(channel, text=text, blocks=blocks)
        except SlackError as error:
            raise AirflowFailException(str(error)) from error

    def _pick(summary: documents.DocumentSummary) -> "tuple[tuple[Pick, ...] | None, str | None]":
        """평가한 문서가 없으면 부르지 않는다. 고를 것이 없다.

        실패하면 `None`을 돌려 점수 순서로 떨어진다. 발송이 태스크의 마지막 단계라 여기서
        태스크를 죽이면 재시도가 같은 표를 한 번 더 채널에 보낸다.
        """
        # LangChain import는 무겁다. DAG 파일 최상단에 두면 NAS dag-processor가
        # DagBag 30초 타임아웃으로 죽는다(2026-08-19 실측). 태스크 실행 때만 읽는다.
        from modules.briefing.picks import DocumentPicker, PickError
        from modules.llm import LlmError, briefing_model

        if summary.is_empty:
            return None, None
        try:
            picks = DocumentPicker(briefing_model()).pick(
                summary.window_hours,
                documents.pick_input(summary),
                summary.allowed_ids,
            )
        except (ConnectionError, LlmError, PickError) as error:
            logger.warning("document picking failed; falling back to the score order: %s", error)
            return None, str(error)
        return picks, None

    send_briefing()


slack_document_briefing = slack_document_briefing()
