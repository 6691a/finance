"""국내 정규장 시장 브리핑을 Slack에 보낸다.

`docs/slack-report-design.md` 1부의 한국장 절반이다. 표는 SQL 집계가 만든다.
LLM 요약은 없다 — 2026-08-19까지 붙였지만 표가 이미 말하는 것 이상을 쓰지 못해 뺐다.

한국 주식의 실제 끝은 KRX 15:30이 아니라 NXT 애프터마켓 20:00이다. 그래서 발송이
20:15까지 이어지고, 종목(005930·000660) 행은 15:30까지 KRX, 이후 NXT 봉을 보인다
(라벨에 `(NXT)`가 붙는다). NXT 장중 봉은 realtime WebSocket 수집기가 채운다
(`KIS_ENABLE_NXT_WEBSOCKET`).

## 왜 미국장과 나뉘어 있나

미국 정규장은 KST로 밤이라 장중에 알릴 것이 없다. 그쪽은 `slack_us_market_briefing`이
다음날 아침에 한 번 보낸다. 휴장 판정도 서로 다른 달력을 본다(KRX vs 미국). 한 DAG에 묶으면
한쪽 달력이 다른 쪽 발송을 막는다.

여기서도 미국 **선물**은 그린다. 선물은 한국장 시간에도 거래되고 `yahoo_quote_intraday`가
5분마다 받고 있어 실시간 값이다. 미국 현물 지수는 이 시간에 닫혀 있어 넣지 않는다.

## 실패를 어떻게 가르나

- 당일 분봉 차트(matplotlib PNG → Slack 파일 업로드)가 실패해도 **리포트는 나간다.**
  표가 본체이고 차트는 덧붙임이다. 대신 실패했다는 사실을 메시지에 남긴다. 조용히 빠지면
  차트가 원래 없는 리포트와 구분되지 않는다. 개장 전처럼 그릴 봉이 없으면 오류가 아니라
  생략이다.
- Slack이 거절하면(`SlackError`) 재시도해도 같은 결과라 태스크를 실패시킨다.
- Slack이 잠깐 죽었으면(`ConnectionError`) 올려서 Airflow가 재시도한다. 발송이 마지막
  단계라 재시도가 중복 발송을 만들지 않는다.

## 필요한 환경

- `SLACK_BOT_TOKEN`. 봇 토큰이고 `chat:write` 스코프가 필요하다. 공개 채널은
  `chat:write.public`이 있으면 초대 없이도 보내지지만, 비공개 채널은 봇을 초대해 두지
  않으면 Slack이 `not_in_channel`로 거절한다. 차트 업로드에는 `files:write`도 필요하다.
- 차트는 운영 Airflow 이미지에 matplotlib과 한글 폰트(`fonts-nanum`)가 있어야 그려진다.
  없으면 차트만 빠지고 표는 나간다.
- `SLACK_CHANNEL_MARKET`. 채널 ID다. 워크스페이스마다 다른 배포 설정이라 코드에 두지 않는다.
- `CONNECTION_ID`가 가리키는 Airflow 연결.
"""

import logging
import os
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pendulum
from airflow.exceptions import AirflowFailException, AirflowSkipException
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import dag, task
from airflow.timetables.trigger import MultipleCronTriggerTimetable
from pydantic import SecretStr

from modules.briefing import chart, market
from modules.briefing.market import MarketScope
from modules.market_session import krx_open_day
from modules.slack import UPLOAD_PROCESSING_WAIT_SECONDS, SlackError, post_message, upload_file
from modules.utility import CONNECTION_ID, KST_TIMEZONE

logger = logging.getLogger(__name__)

# 매시 정각 10:00~19:00(정규장과 NXT 애프터마켓), 15:30 KRX 마감, 20:15 최종 마감이다.
# 확정 수급은 KST 18:10 수집이라 19:00부터 실린다. NXT 애프터마켓이 20:00에 끝나고
# REST 확정 배치(kis_stock_minute_bars_daily)가 20:05에 돌아 20:15 리포트가 하루 완결이다.
# 분이 제각각이라 cron 하나로 못 적는다. 시각을 바꾸려면 이 목록만 고친다.
SCHEDULE = MultipleCronTriggerTimetable(
    "0 10-19 * * 1-5",  # KST 평일 매시 정각 10:00~19:00 = UTC 01:00~10:00
    "30 15 * * 1-5",  # KST 평일 15:30 KRX 마감 = UTC 06:30
    "15 20 * * 1-5",  # KST 평일 20:15 NXT 마감 최종 = UTC 11:15
    timezone=KST_TIMEZONE,
)

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
            chart_series = market.collect_chart_series(connection, now)
        finally:
            connection.close()

        chart_files, chart_error = _chart(token, chart_series, now)
        blocks = market.render_blocks(summary, MarketScope.KOREA, chart_files=chart_files, chart_error=chart_error)
        text = market.render_text(summary, MarketScope.KOREA)

        try:
            return post_message(token, channel, text=text, blocks=blocks)
        except SlackError as error:
            # 토큰·채널·블록이 틀렸다. 다시 보내도 같은 결과다.
            raise AirflowFailException(str(error)) from error

    def _chart(
        token: SecretStr, series: tuple[market.ChartSeries, ...], now: datetime
    ) -> tuple[tuple[tuple[str, str], ...] | None, str | None]:
        """계열마다 차트 한 장을 그려 올린다. **실패해도 리포트를 막지 않는다.**

        표가 본체다. 개장 전처럼 그릴 봉이 없으면 실패가 아니라 생략이라 오류도 남기지
        않는다. 한 장이라도 실패하면 전부 버리고 원인을 메시지에 남긴다 — 일부만 실린
        차트는 빠진 심볼이 안 보이기 때문이다.
        """
        if not series:
            return None, None
        local = now.astimezone(KST_TIMEZONE)
        try:
            uploads = tuple(
                (
                    upload_file(
                        token,
                        filename=f"kr-{one.symbol}-{local:%Y%m%d-%H%M}.png",
                        title=f"{one.label} 당일 흐름 {local:%m/%d %H:%M} KST",
                        content=chart.render_series_png(one),
                    ),
                    one.label,
                )
                for one in series
            )
        except ImportError as error:
            # matplotlib이 운영 이미지에 없다. 재시도해도 같으므로 표만 보낸다.
            logger.warning("chart backend unavailable; sending the tables without it: %s", error)
            return None, "차트 백엔드 없음(matplotlib)"
        except (ConnectionError, SlackError, chart.ChartError) as error:
            logger.warning("chart failed; sending the tables without it: %s", error)
            return None, str(error)
        # 업로드 직후에는 image 블록이 invalid_blocks로 거절된다. slack 모듈 주석 참고.
        time.sleep(UPLOAD_PROCESSING_WAIT_SECONDS)
        return uploads, None

    send_briefing()


slack_kr_market_briefing = slack_kr_market_briefing()
