"""새로 감지된 공시를 Slack에 알린다.

`docs/briefing/disclosure-briefing.md`의 구현이다. `dart_disclosure_intraday`가 평일
2분마다 삼성전자·SK하이닉스의 새 공시를 `disclosure_event`에 넣는데, 그 행을 읽는 곳이
추론 툴 `recent_disclosures` 하나뿐이었다. 모델이 근거로 쓰는 경로지 사람이 "방금 뭐가
올라왔나"를 보는 경로가 아니다.

## 요약이 아니라 알림이다

하루 한 번 묶어 보내지 않는다. 공시는 시간이 값어치다. 10분마다 돌면서 **그 창에서 처음
감지된 공시만** 보내므로, 감지에서 발송까지 지연은 수집 주기(2분)와 발송 주기(10분)의
합인 최대 12분이다.

창은 벽시계가 아니라 **DAG의 data interval**이다. `(start, end]` 반열림이라 실행이 밀려도
창이 이어지고 한 공시가 두 창에 걸치지 않는다.

## "이미 보냈나"를 DB에 남기지 않는다

`disclosure_event.notified_at` 같은 칸을 더하면 정확해지지만 리비전이 하나 는다. 창이
data interval이라 정상 실행에서는 한 공시가 정확히 한 창에만 들고, 태스크 재시도로 인한
중복은 저장소가 이미 허용하는 수준이다(`docs/briefing/slack-report-design.md`).

대가는 하나다 — DAG를 pause 했다 풀면 그동안의 공시는 안 나간다(`catchup=False`).
알림에는 그것이 맞는 동작이다.

## 0건이면 아무 것도 하지 않는다

문서 브리핑은 0건에도 보내 생존 신호를 겸하지만 여기서 그러면 하루의 대부분이 빈
메시지다. 수집 생존은 `slack_ops_briefing`이 이미 보고한다.

## 강조는 모델이, 숫자는 SQL이

공시는 **전부 실린다.** 모델은 그중 무엇에 별을 붙일지와 이유 한 줄만 정한다. 실적 금액과
전년 대비는 `earnings_fact`에서 읽어 순수 함수가 계산한다.

## 필요한 환경

- `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_DOCUMENT`. 문서 브리핑과 같은 채널이다.
- `XAI_API_KEY`. 모델은 `modules/llm.py`의 `briefing_model()`이 코드로 정한다.
  **없거나 무효여도 이 DAG는 죽지 않는다** — 강조 없이 목록이 나간다.
- `CONNECTION_ID`가 가리키는 Airflow 연결.

## 실패와 재시도

**단일 요청 형태다.** 태스크 하나가 조회·강조·발송을 차례로 하고 판정할 항목별 실패가 없다.
조회와 Slack 실패는 올려 재시도하고, 강조 실패만 잡아 폴백한다 — 알림을 늦추는 것보다
강조 없이 제때 가는 편이 낫다.
"""

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pendulum
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import dag, get_current_context, task
from airflow.sdk.exceptions import AirflowFailException
from pydantic import SecretStr

from modules.briefing import disclosures
from modules.slack import SlackClient, SlackError

if TYPE_CHECKING:
    from modules.briefing.disclosures import Highlight
from modules.utility import CONNECTION_ID, KST_TIMEZONE

logger = logging.getLogger(__name__)

# 수집(`dart_disclosure_intraday`)이 평일 KST 07:00~20:58에 2분마다 돈다. 그보다 느슨하게
# 10분마다 돌며 그 창의 새 공시만 보낸다. 20:50이 마지막이라 20:58 수집분은 다음 날 첫
# 실행이 아니라 이 날의 마지막 창에 들지 못한다 — 장이 닫힌 뒤라 늦어도 값어치가 같다.
SCHEDULE = "*/10 7-20 * * 1-5"  # KST 평일 07:00~20:50, 10분마다 = UTC 전일 22:00~11:50


def _connection() -> Any:
    return PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()


def _slack_settings() -> tuple[SecretStr, str]:
    token = os.environ.get("SLACK_BOT_TOKEN")
    channel = os.environ.get("SLACK_CHANNEL_DOCUMENT")
    if not token or not channel:
        raise AirflowFailException("SLACK_BOT_TOKEN and SLACK_CHANNEL_DOCUMENT are required")
    return SecretStr(token), channel


def _window() -> tuple[datetime, datetime]:
    """이 실행이 보는 창. 벽시계가 아니라 data interval이다."""
    context = get_current_context()
    start = context.get("data_interval_start")
    end = context.get("data_interval_end")
    if start is None or end is None:
        raise AirflowFailException("data interval is required; this DAG must not run without one")
    return start, end


def _highlight(
    batch: "disclosures.DisclosureBatch",
) -> "tuple[tuple[Highlight, ...] | None, str | None]":
    """강조할 공시들. 실패하면 `None`을 돌려 강조 없이 보낸다.

    발송이 태스크의 마지막 단계라 여기서 태스크를 죽이면 재시도가 같은 공시를 한 번 더
    채널에 보낸다. 문서 브리핑의 선별 폴백과 같은 판단이다.
    """
    # LangChain import는 무겁다. DAG 파일 최상단에 두면 NAS dag-processor가
    # DagBag 30초 타임아웃으로 죽는다(2026-08-19 실측). 태스크 실행 때만 읽는다.
    from modules.briefing.disclosure_picks import DisclosurePicker
    from modules.briefing.disclosures import HighlightError
    from modules.llm import LlmError, briefing_model

    try:
        # 흐름 이름으로 고정한다. 평일 10분마다 도는 흐름이라 시스템 프롬프트 접두가
        # 같은 서버 캐시에 남는 값어치가 크다(`modules/llm.py`).
        highlights = DisclosurePicker(briefing_model("disclosure-picks")).highlight(
            disclosures.pick_input(batch),
            batch.allowed_ids,
        )
    except (ConnectionError, LlmError, HighlightError) as error:
        logger.warning("disclosure highlighting failed; sending the plain list: %s", error)
        return None, str(error)
    return highlights, None


@dag(
    dag_id="slack_disclosure_briefing",
    dag_display_name="📄 새 공시 알림 (Slack)",
    description="방금 감지된 DART 공시를 실적 숫자와 함께 Slack에 알린다. 0건이면 보내지 않는다.",
    schedule=SCHEDULE,
    start_date=pendulum.datetime(2026, 8, 27, tz=KST_TIMEZONE),  # KST 2026-08-27 00:00 = UTC 2026-08-26 15:00
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 1, "retry_delay": timedelta(minutes=2)},
    doc_md=__doc__,
    tags=["slack", "briefing", "disclosure"],
)
def slack_disclosure_briefing():
    @task(task_display_name="새 공시 알림 발송")
    def send_alert() -> str:
        token, channel = _slack_settings()
        window_start, window_end = _window()
        now = datetime.now(UTC)

        connection = _connection()
        try:
            batch = disclosures.collect_batch(connection, now, window_start, window_end)
        finally:
            connection.close()

        if batch.is_empty:
            # 정상 종료다. 모델도 Slack도 부르지 않는다.
            logger.info("no new disclosures between %s and %s", window_start, window_end)
            return ""

        highlights, highlight_error = _highlight(batch)
        blocks = disclosures.render_blocks(batch, highlights, highlight_error)
        text = disclosures.render_text(batch, highlights)

        try:
            return SlackClient(token).post_message(channel, text=text, blocks=blocks)
        except SlackError as error:
            raise AirflowFailException(str(error)) from error

    send_alert()


slack_disclosure_briefing = slack_disclosure_briefing()
