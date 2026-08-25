"""밤사이 미국장 마감을 다음날 아침 Slack에 보낸다.

`docs/slack-report-design.md` 1부의 미국장 절반이다. 미국 정규장은 KST로 전날 22:30에
시작해 당일 05:00(서머타임)에 끝난다. **그 시간에는 알림을 보내지 않는다.** 대신 아침에 한
번, 밤사이 결과와 전일 한국장을 **같은 메시지에** 놓는다. 미국 값만 나열하는 것이 목적이
아니라 그 변화가 오늘 한국장에 어떤 맥락인지를 한 화면에 남기는 것이 목적이다.
LLM 요약은 없다 — 2026-08-19까지 붙였지만 표가 이미 말하는 것 이상을 쓰지 못해 뺐다.

## 왜 화~토인가

KST 월요일 아침에는 직전 미국 세션이 없다. 금요일 밤 세션은 KST 토요일 아침에 보고,
그것이 이 DAG의 마지막 실행이다.

## 왜 08:00인가

미국 마감(KST 05:00~06:00) 뒤이고, 아침 수집 DAG들(FRED 07:30, Yahoo 일봉 07:30)이 끝난
다음이다. 더 미루고 싶으면 `SCHEDULE` 한 줄을 고친다.

## 세션 날짜는 뉴욕 시계로 뽑는다

KST 날짜로 물으면 세션 하나가 두 날짜에 걸친다. `market.us_session_date`가
`America/New_York` 기준으로 날짜를 뽑고, 휴장 판정도 그 날짜로 `market_session`에 묻는다.

## 필요한 환경

`slack_kr_market_briefing`과 같다. 채널도 같은 `SLACK_CHANNEL_MARKET`이다. 같은 주제를
시간대만 나눠 보내는 것이라 채널을 쪼개지 않는다.
"""

import os
from datetime import UTC, datetime, timedelta
from typing import Any

import pendulum
from airflow.exceptions import AirflowFailException, AirflowSkipException
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import dag, task
from pydantic import SecretStr

from modules.briefing import market
from modules.briefing.market import MarketScope
from modules.market_session import us_equity_open_day
from modules.slack import SlackError, post_message
from modules.utility import CONNECTION_ID, KST_TIMEZONE

# KST 화~토 08:00 = UTC 월~금 23:00. 미국 마감과 아침 수집이 끝난 뒤다.
SCHEDULE = "0 8 * * 2-6"


def _connection() -> Any:
    return PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()


def _slack_settings() -> tuple[SecretStr, str]:
    token = os.environ.get("SLACK_BOT_TOKEN")
    channel = os.environ.get("SLACK_CHANNEL_MARKET")
    if not token or not channel:
        raise AirflowFailException("SLACK_BOT_TOKEN and SLACK_CHANNEL_MARKET are required")
    return SecretStr(token), channel


def _skip_when_closed(connection: Any, session_date) -> None:
    """미국 확정 휴장일이면 건너뛴다. 모르면 보낸다.

    날짜는 뉴욕 기준이다. KST 날짜로 물으면 세션의 절반이 엉뚱한 날을 본다.
    """
    if us_equity_open_day(connection, session_date) is False:
        raise AirflowSkipException(f"US equity market was closed on {session_date}")


@dag(
    dag_id="slack_us_market_briefing",
    dag_display_name="💬 미국장 마감 브리핑 (Slack)",
    description="밤사이 미국 지수·선물과 주요국 금리를 전일 한국장과 함께 묶어 아침에 보낸다.",
    schedule=SCHEDULE,
    start_date=pendulum.datetime(2026, 8, 18, tz=KST_TIMEZONE),  # KST 2026-08-18 00:00 = UTC 2026-08-17 15:00
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 1, "retry_delay": timedelta(minutes=2)},
    doc_md=__doc__,
    tags=["slack", "briefing", "market", "us"],
)
def slack_us_market_briefing():
    @task(task_display_name="미국장 마감 브리핑 발송")
    def send_briefing() -> str:
        token, channel = _slack_settings()
        now = datetime.now(UTC)

        connection = _connection()
        try:
            _skip_when_closed(connection, market.us_session_date(now))
            summary = market.MarketBriefingReader(connection, now).summary()
        finally:
            connection.close()

        blocks = market.render_blocks(summary, MarketScope.US)
        text = market.render_text(summary, MarketScope.US)

        try:
            return post_message(token, channel, text=text, blocks=blocks)
        except SlackError as error:
            raise AirflowFailException(str(error)) from error

    send_briefing()


slack_us_market_briefing = slack_us_market_briefing()
