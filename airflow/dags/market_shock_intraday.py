"""코스피 급변 포착 — 30분 창에서 ±2% 움직이면 사실만 알린다.

설계는 `docs/analysis/market-shock-capture.md`다. **LLM이 없다** — 읽고, 나누고, 보낸다.

## 왜

2026-09-03 14:00~14:30 KST에 코스피가 30분 만에 −3.33% 빠졌다 되돌렸다(6,661.04 →
6,439.49). 그날 종가는 **+0.26%**였고 아시아 일봉도 −0.17%~−0.67%로 조용했다. **일봉에는
그 사건이 아예 없다.** 장중 수집 문서 96건 중 이유를 말한 것도 0건이었다.

그래서 분봉으로 잡아서 기록만 남긴다. "왜"는 이 DAG가 묻지 않는다 — 그 시각에 우리가 가진
문서로는 답이 안 나오고, 물으면 모델이 관계없는 같은 날 기사를 붙여 지어낸다.

## 무엇을 재나

봉마다 그 봉의 값을 **직전 봉들**의 극값과 견준다.

    하락폭 = 봉의 저가 / 창 시작~직전 봉의 최고가 - 1
    상승폭 = 봉의 고가 / 창 시작~직전 봉의 최저가 - 1

극값에서 그 봉 자신을 빼는 것이 중요하다. 넣으면 봉 하나의 고가-저가 폭이 신호가 되어
큰 음봉이 하락과 급등에 동시에 걸린다.

**종가 등락률이 아니다.** 2026-09-02는 종가가 −3.99%인데 30분 창에서 −1.5%를 한 번도 안
넘었다 — 하루 종일 눌린 추세 하락이고 아시아 일봉만 봐도 동반 하락이 보인다. 이 장치가
필요 없는 날이라 안 고르는 것이 맞다.

## 창의 끝이 지금이 아니다

**니케이가 KIS에서도 15~16분 지연이고 아시아 수집이 5분 폴링이다.** 창을 "지금"까지 잡으면
아시아 칸이 비어 포착의 핵심("한국만의 재료가 아닐 수 있다")이 빠진다. 그래서 창의 끝을
`lag_minutes`(기본 25분)만큼 앞에 둔다. 2026-09-03 사건이면 14:16에 임계에 닿고 포착은
14:45 전후에 나간다 — 마감 45분 전이다.

## 중복

한 급락이 30분 이어지면 5분 폴링에 여섯 번 걸린다. **자연키로는 못 막는다** — 낙폭이
깊어지면서 매번 다른 봉이 임계에 닿는다. 직전 포착이 `cooldown_minutes` 안이면 새로 만들지
않는다.

## 태스크 하나

    capture

저장과 발송을 한 태스크에 두되 트랜잭션은 가른다. 발송이 실패하면 `notified_at`이 NULL로
남아 "저장은 됐는데 아무도 못 봤다"를 말한다.

## 실패와 재시도

**단일 요청 형태다** — 한 번의 판정이 결과 전부라 예외를 그대로 올린다.

- KRX 휴장일 → `AirflowSkipException`
- 코스피 봉이 창의 절반(15개)에 못 미침 → `AirflowSkipException`. 개장 직후와 수집 지연이
  그렇고 **다음 run이 5분 뒤 같은 창을 다시 본다**
- **아시아 봉이 없는 것은 실패가 아니다.** `available=false`로 저장하고 계속한다. 포착의
  값어치가 코스피 쪽에 있고 동시성은 있으면 더 좋은 것이다
- Slack 실패 → 그대로 올려 재시도. 행은 이미 저장돼 있고 자연키가 중복 저장을 막는다

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `threshold_pct` | `2.0` | 창 안 극값 대비 이만큼이면 급변. 양방향이다 |
| `window_minutes` | `30` | 판정하는 창의 길이 |
| `lag_minutes` | `25` | 창의 끝을 지금보다 이만큼 앞에 둔다 |
| `cooldown_minutes` | `60` | 직전 포착이 이 안이면 같은 사건으로 본다 |
| `notify` | `true` | Slack 발송. 끄면 저장까지만 한다 |

## 필요한 환경

- `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_MARKET` — 시장 브리핑과 같은 채널을 재사용한다.
- `CONNECTION_ID`가 가리키는 Airflow 연결.
"""

import logging
import os
from contextlib import closing
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

import pendulum
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Param, dag, get_current_context, task
from airflow.sdk.exceptions import AirflowFailException, AirflowSkipException
from pydantic import SecretStr

from modules.market_session import krx_open_day
from modules.shock.detect import detect, peer_move
from modules.shock.domain import (
    COOLDOWN_MINUTES,
    LAG_MINUTES,
    PEERS,
    THRESHOLD_PCT,
    TRIGGER_SYMBOL,
    WINDOW_MINUTES,
)
from modules.shock.render import render_blocks, render_text
from modules.shock.store import ShockStore, within_cooldown
from modules.slack import SlackClient
from modules.utility import CONNECTION_ID, KST_TIMEZONE, atomic

logger = logging.getLogger(__name__)

THRESHOLD_PARAM = "threshold_pct"
WINDOW_PARAM = "window_minutes"
LAG_PARAM = "lag_minutes"
COOLDOWN_PARAM = "cooldown_minutes"
NOTIFY_PARAM = "notify"

# 원인을 찾는 기한. 포착일부터 이만큼 뒤의 KRX 개장일이다.
#
# 대개 그날 마감~다음날 아침에 원인이 나오지만 안 나올 수도 있어 사흘을 둔다. 날짜는
# 우리가 세지 않는다 — `market_session.effective_open_day`가 판정의 주인이다.
CAUSE_BUSINESS_DAYS = 3


def _slack_settings() -> tuple[SecretStr, str]:
    token = os.environ.get("SLACK_BOT_TOKEN")
    channel = os.environ.get("SLACK_CHANNEL_MARKET")
    if not token or not channel:
        # 설정 누락이라 재시도해도 같다. 값 자체는 메시지에 넣지 않는다.
        raise AirflowFailException("SLACK_BOT_TOKEN and SLACK_CHANNEL_MARKET are required")
    return SecretStr(token), channel


@dag(
    dag_id="market_shock_intraday",
    dag_display_name="⚡ 코스피 급변 포착 (KIS 분봉)",
    description="국내 정규장 동안 5분마다 코스피 30분 낙폭·상승폭을 재고 ±2%면 아시아·미국 선물 동시성과 함께 알린다.",
    # KST 평일 09:20~15:55 = UTC 평일 00:20~06:55. 창의 끝이 25분 뒤라 개장 25분 뒤부터
    # 의미가 생기고, 마감(15:30) 뒤 25분까지 봐야 마지막 창이 판정된다.
    schedule="*/5 9-15 * * 1-5",
    start_date=pendulum.datetime(2026, 9, 5, tz=KST_TIMEZONE),  # KST 2026-09-05 00:00 = UTC 2026-09-04 15:00
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 1, "retry_delay": timedelta(minutes=2)},
    params={
        THRESHOLD_PARAM: Param(
            float(THRESHOLD_PCT),
            type="number",
            minimum=0.1,
            maximum=10,
            title="급변 임계(%)",
            description=(
                "창 안의 극값 대비 이만큼 움직이면 포착한다. 양방향이다. "
                "14거래일 실측에서 2.0%가 한 달 8.6회(주 2회)다."
            ),
        ),
        WINDOW_PARAM: Param(
            WINDOW_MINUTES,
            type="integer",
            minimum=5,
            maximum=120,
            title="판정 창(분)",
            description="이 길이의 창 안에서 극값 대비 움직임을 잰다. 봉이 창의 절반에 못 미치면 판정하지 않는다.",
        ),
        LAG_PARAM: Param(
            LAG_MINUTES,
            type="integer",
            minimum=0,
            maximum=60,
            title="창 끝의 지연(분)",
            description="창의 끝을 실행 시각보다 이만큼 앞에 둔다. 니케이가 15~16분 지연이라 그것을 흡수한다.",
        ),
        COOLDOWN_PARAM: Param(
            COOLDOWN_MINUTES,
            type="integer",
            minimum=0,
            maximum=240,
            title="쿨다운(분)",
            description="직전 포착이 이 안이면 같은 사건으로 보고 새로 만들지 않는다.",
        ),
        NOTIFY_PARAM: Param(
            True,
            type="boolean",
            title="Slack 발송",
            description="끄면 저장까지만 한다. 과거 구간을 손으로 확인할 때 끈다.",
        ),
    },
    doc_md=__doc__,
    tags=["market", "intraday", "shock", "korea", "slack"],
)
def market_shock_intraday():
    @task(task_display_name="급변 포착·알림")
    def capture() -> int:
        context = get_current_context()
        params = dict(context.get("params") or {})
        threshold = _decimal_param(params, THRESHOLD_PARAM, THRESHOLD_PCT)
        window_minutes = _int_param(params, WINDOW_PARAM, WINDOW_MINUTES)
        lag_minutes = _int_param(params, LAG_PARAM, LAG_MINUTES)
        cooldown_minutes = _int_param(params, COOLDOWN_PARAM, COOLDOWN_MINUTES)
        notify = bool(params.get(NOTIFY_PARAM, True))

        now = datetime.now(UTC)
        window_end = now - timedelta(minutes=lag_minutes)
        window_start = window_end - timedelta(minutes=window_minutes)
        session_date = window_end.astimezone(KST_TIMEZONE).date()
        min_bars = max(1, window_minutes // 2)

        with closing(PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()) as connection:
            # 답을 모르면 계속한다. 캘린더가 밀렸다고 진짜 거래일을 잃는 것이 휴장일에 빈
            # 조회를 한 번 더 하는 것보다 나쁘다(`modules/market_session.py`의 규칙).
            if krx_open_day(connection, session_date) is False:
                raise AirflowSkipException(f"KRX is closed on {session_date}")

            store = ShockStore(connection)
            bars = store.bars(TRIGGER_SYMBOL, window_start=window_start, window_end=window_end)
            if len(bars) < min_bars:
                raise AirflowSkipException(
                    f"{TRIGGER_SYMBOL} has {len(bars)} bar(s) in {window_start:%H:%M}~{window_end:%H:%M} UTC; "
                    f"{min_bars} required. The next run sees the same window."
                )

            event = detect(
                bars,
                symbol=TRIGGER_SYMBOL,
                threshold_pct=threshold,
                window_start=window_start,
                window_end=window_end,
                min_bars=min_bars,
            )
            if event is None:
                logger.info(
                    "No shock in %s~%s UTC across %s bar(s) at ±%s%%",
                    window_start.isoformat(),
                    window_end.isoformat(),
                    len(bars),
                    threshold,
                )
                return 0

            last_detected = store.last_detected_at(TRIGGER_SYMBOL)
            if within_cooldown(event.detected_at, last_detected, cooldown_minutes):
                logger.info(
                    "%s at %s is inside the %s-minute cooldown after %s",
                    event.direction.value,
                    event.detected_at.isoformat(),
                    cooldown_minutes,
                    last_detected.isoformat() if last_detected else "-",
                )
                return 0

            # 봉이 0건인 시장도 키가 남는다. 빠진 키와 빈 값을 다르게 다루면 "못 봤다"가
            # 조용히 사라진다.
            peer_bars = store.peer_bars(PEERS, window_start=window_start, window_end=window_end)
            peers = [peer_move(spec, peer_bars[spec.symbol]) for spec in PEERS]

            # 달력이 아직 그날까지 안 채워졌으면 None이고, 원인 DAG가 그때 다시 구한다.
            deadline = store.nth_open_day(session_date, CAUSE_BUSINESS_DAYS)

            with atomic(connection):
                event_id = store.save(
                    event,
                    session_date=session_date,
                    peers=peers,
                    cause_deadline=deadline,
                )

            if event_id is None:
                # 재시도가 같은 봉을 다시 집었다. 알림을 두 번 보내지 않는다.
                logger.info("%s at %s is already stored", event.direction.value, event.detected_at.isoformat())
                return 0

            logger.info(
                "Captured %s %s%% at %s (%s bars, %s peer market(s) with data)",
                event.direction.value,
                event.move_pct,
                event.detected_at.isoformat(),
                event.bar_count,
                sum(1 for peer in peers if peer.available),
            )

            if not notify:
                return event_id

            # 저장 트랜잭션 밖에서 보낸다. 발송 실패가 사건 기록까지 되돌리면 안 된다.
            token, channel = _slack_settings()
            SlackClient(token).post_message(
                channel,
                text=render_text(event),
                blocks=render_blocks(event, peers),
            )
            with atomic(connection):
                store.mark_notified(event_id, datetime.now(UTC))
            return event_id

    capture()


def _int_param(params: dict, name: str, fallback: int) -> int:
    value = params.get(name)
    if value is None:
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise AirflowFailException(f"{name} must be an integer, got {value!r}") from error


def _decimal_param(params: dict, name: str, fallback: Decimal) -> Decimal:
    value = params.get(name)
    if value is None:
        return fallback
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as error:
        raise AirflowFailException(f"{name} must be a number, got {value!r}") from error
    if parsed <= 0:
        raise AirflowFailException(f"{name} must be positive, got {parsed}")
    return parsed


market_shock_intraday = market_shock_intraday()
