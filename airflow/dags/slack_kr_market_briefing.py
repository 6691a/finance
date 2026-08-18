"""국내 정규장 시장 브리핑을 Slack에 보낸다.

`docs/slack-report-design.md` 1부의 한국장 절반이다. 표는 SQL 집계가 만들고 LLM은 그 위에
요약만 쓴다. **숫자는 LLM이 만들지 않는다.**

## 왜 미국장과 나뉘어 있나

미국 정규장은 KST로 밤이라 장중에 알릴 것이 없다. 그쪽은 `slack_us_market_briefing`이
다음날 아침에 한 번 보낸다. 휴장 판정도 서로 다른 달력을 본다(KRX vs 미국). 한 DAG에 묶으면
한쪽 달력이 다른 쪽 발송을 막는다.

여기서도 미국 **선물**은 그린다. 선물은 한국장 시간에도 거래되고 `yahoo_quote_intraday`가
5분마다 받고 있어 실시간 값이다. 미국 현물 지수는 이 시간에 닫혀 있어 넣지 않는다.

## 실패를 어떻게 가르나

- 요약(LLM)이 실패해도 **리포트는 나간다.** 표가 본체이고 요약은 덧붙임이다. 대신 실패했다는
  사실을 메시지에 남긴다. 조용히 빠지면 요약이 원래 없는 리포트와 구분되지 않는다.
- Slack이 거절하면(`SlackError`) 재시도해도 같은 결과라 태스크를 실패시킨다.
- Slack이 잠깐 죽었으면(`ConnectionError`) 올려서 Airflow가 재시도한다. 발송이 마지막
  단계라 재시도가 중복 발송을 만들지 않는다.

## 필요한 환경

- `SLACK_BOT_TOKEN`. 봇 토큰이고 `chat:write` 스코프가 필요하다. 공개 채널은
  `chat:write.public`이 있으면 초대 없이도 보내지지만, 비공개 채널은 봇을 초대해 두지
  않으면 Slack이 `not_in_channel`로 거절한다.
- `SLACK_CHANNEL_MARKET`. 채널 ID다. 워크스페이스마다 다른 배포 설정이라 코드에 두지 않는다.
- `XAI_API_KEY`. 요약 모델은 `modules/llm.py`의 `briefing_model()`이 코드로 정하고 키는 그
  LangChain 클래스가 자기 이름으로 읽는다.
- `CONNECTION_ID`가 가리키는 Airflow 연결.
"""

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import pendulum
from airflow.exceptions import AirflowFailException, AirflowSkipException
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import dag, task
from pydantic import SecretStr

from modules.briefing import market
from modules.briefing.comment import BriefingCommentator, CommentError
from modules.briefing.market import MarketScope
from modules.llm import LlmError, briefing_model
from modules.market_session import krx_open_day
from modules.slack import SlackError, post_message
from modules.utility import CONNECTION_ID, KST_TIMEZONE

logger = logging.getLogger(__name__)

# KST 평일 12:30·16:30·19:30 = UTC 월~금 03:30, 07:30, 10:30.
# 오전장 요약, 마감 직후(지수 중심), 확정 수급까지 실은 마감 확정 리포트 순서다.
# 종목 마감 확정 섹션은 KST 18:10 수집 뒤에야 값이 있어 19:30 발송에만 나타난다.
# 주기를 바꾸려면 이 한 줄만 고친다.
SCHEDULE = "30 12,16,19 * * 1-5"

REPORT_NAME = "한국장 브리핑"


def _connection() -> Any:
    # 반환 타입은 provider 버전에 따라 psycopg2/psycopg3 래퍼로 갈린다. 어느 쪽이든 PEP 249다.
    return PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()


def _slack_settings() -> tuple[SecretStr, str]:
    token = os.environ.get("SLACK_BOT_TOKEN")
    channel = os.environ.get("SLACK_CHANNEL_MARKET")
    if not token or not channel:
        # 설정 누락이라 재시도해도 같다. 값 자체는 메시지에 넣지 않는다.
        raise AirflowFailException("SLACK_BOT_TOKEN and SLACK_CHANNEL_MARKET are required")
    return SecretStr(token), channel


def _skip_when_closed(connection: Any, today_kst: pendulum.Date) -> None:
    """확정 휴장일이면 건너뛴다. **모르면 보낸다.**

    휴장일에는 모든 국내 섹션이 전일 값 재탕이라 하루 두 번 보내면 소음이다. 반대로 달력을
    아직 못 채웠다는 이유로 진짜 거래일 브리핑을 빠뜨리는 것이 더 나쁘다.
    """
    if krx_open_day(connection, today_kst) is False:
        raise AirflowSkipException(f"KRX is closed on {today_kst}")


@dag(
    dag_id="slack_kr_market_briefing",
    dag_display_name="💬 한국장 브리핑 (Slack)",
    description="국내 지수·선물, 장중 해외, 환율, 수급을 표로 묶어 Slack에 보낸다.",
    schedule=SCHEDULE,
    start_date=pendulum.datetime(2026, 8, 18, tz=KST_TIMEZONE),  # KST 2026-08-18 00:00 = UTC 2026-08-17 15:00
    catchup=False,
    max_active_runs=1,
    # 발송이 마지막 단계라 재시도가 중복 메시지를 만들지 않는다.
    default_args={"retries": 1, "retry_delay": timedelta(minutes=2)},
    doc_md=__doc__,
    tags=["slack", "briefing", "market", "korea"],
)
def slack_kr_market_briefing():
    @task(task_display_name="한국장 브리핑 발송")
    def send_briefing() -> str:
        token, channel = _slack_settings()
        now = datetime.now(UTC)

        connection = _connection()
        try:
            _skip_when_closed(connection, now.astimezone(KST_TIMEZONE).date())
            summary = market.collect_summary(connection, now)
        finally:
            connection.close()

        comment, comment_error = _comment(summary)
        blocks = market.render_blocks(summary, MarketScope.KOREA, comment, comment_error)
        text = market.render_text(summary, MarketScope.KOREA)

        try:
            return post_message(token, channel, text=text, blocks=blocks)
        except SlackError as error:
            # 토큰·채널·블록이 틀렸다. 다시 보내도 같은 결과다.
            raise AirflowFailException(str(error)) from error

    def _comment(summary: market.MarketSummary) -> tuple[str | None, str | None]:
        """요약을 만든다. **실패해도 리포트를 막지 않는다.**

        표가 본체라 요약이 없어도 보낼 값어치가 있다. 대신 실패 원인을 함께 돌려주어
        메시지에 남긴다. 로그만 남기면 아무도 보지 않는 경고가 된다.
        """
        try:
            return BriefingCommentator(briefing_model()).comment(
                REPORT_NAME, market.comment_input(summary, MarketScope.KOREA)
            ), None
        except (ConnectionError, LlmError, CommentError) as error:
            logger.warning("briefing comment failed; sending the tables without it: %s", error)
            return None, str(error)

    send_briefing()


slack_kr_market_briefing = slack_kr_market_briefing()
