"""세 DAG가 공유하는 것 — 연결, 슬롯 판정, 관측 상태 조립, Slack 발송.

**슬롯을 모른다.** 슬롯은 값으로 흘러갈 뿐 여기서 분기하지 않는다. 슬롯마다 다른 것(기준가를
어디서 읽나, 준비 검사가 무엇을 기다리나)은 슬롯별 모듈(`forecast.py`·`intraday.py`)이 갖는다.

여기가 Airflow를 import하는 유일한 자리 중 하나다. `domain`·`state`·`generation`은 안 본다 —
그래야 노트북과 테스트가 그것들을 가볍게 읽는다.
"""

import logging
import os
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from airflow.exceptions import AirflowFailException
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Param
from pendulum import instance as pendulum_instance
from pydantic import SecretStr

from modules.kospi.domain import (
    BARS_WINDOW,
    CLOSE_TIME,
    INDEX_CODE,
    OPEN_TIME,
    RELATION_LOOKBACK_DAYS,
    REVIEW_TIME,
    SLOT_TIMES,
    Direction,
    Factor,
    KospiNotReady,
    RunSlot,
    kst_label,
)
from modules.kospi.graph import driver as graph_driver
from modules.kospi.graph import read_memories, read_relations
from modules.kospi.state import EarlierReason, EarlierSlot, MemoryRow, ObservedState, RelationRow
from modules.kospi.store import KospiStore
from modules.utility import CONNECTION_ID, KST_TIMEZONE

logger = logging.getLogger(__name__)

RUN_DATE_PARAM = "run_date"
NOTIFY_PARAM = "notify"
CALENDAR_DAY_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")

# 세 DAG가 같은 재시도 정책을 쓴다. 재시도는 준비 검사가 선행 DAG의 지연을 기다리는 수단이다.
DEFAULT_ARGS: dict[str, Any] = {"retries": 2, "retry_delay": timedelta(minutes=5)}

# 전망 한 번의 상한. 요청 하나의 타임아웃은 모델 호출 한 번만 막고, 한 실행은 조사 왕복과
# 답변·교정까지 모델을 여러 번 부른다. 장전이 09:00 개장 전에 닿아야 해서 이 값이다.
BUILD_TIMEOUT = timedelta(minutes=15)


def run_date_param() -> dict[str, Param]:
    """세 DAG가 같은 Param 하나를 쓴다."""
    return {
        RUN_DATE_PARAM: Param(
            None,
            type=["null", "string"],
            format="date",
            title="대상 세션 날짜",
            description="YYYY-MM-DD. 비우면 스케줄된 시각의 KST 날짜. 지난 날을 다시 만들 때만 준다.",
        ),
    }


def notify_param() -> dict[str, Param]:
    """발송을 끄는 Param. **기본은 켜짐이다** — 정시 실행이 조용해지면 안 된다.

    끄는 자리는 하나다: 관찰 20영업일 백필. 날마다 수동 트리거하는 일이라 그대로 두면
    운영 시장 채널에 스무 번이 나간다. 저장소 규칙("테스트 발송을 운영 채널로 보내지
    않는다")과 같은 판단이다.
    """
    return {
        NOTIFY_PARAM: Param(
            True,
            type="boolean",
            title="Slack 발송",
            description="끄면 저장까지만 하고 발송을 건너뛴다. 과거 날짜를 백필할 때 쓴다.",
        ),
    }


def notify_enabled(context: Any) -> bool:
    """이 실행이 Slack을 보내나. Param이 없으면 보낸다 — 조용한 기본값을 만들지 않는다."""
    given = (context.get("params") or {}).get(NOTIFY_PARAM)
    return True if given is None else bool(given)


def connection() -> Any:
    """반환 타입은 provider 버전에 따라 갈린다. 어느 쪽이든 PEP 249 연결이다."""
    return PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()


def graph() -> Any:
    """Neo4j 드라이버. **설정이 없으면 죽인다.**

    관계와 메모가 이 기능의 절반이라 그래프 없이 도는 것은 다른 기능이다. 옛 인과 그래프는
    `NEO4J_URI`가 없으면 skip이었는데 거기는 투영이라 없어도 원본이 남았다 — 여기는 원본이
    그래프다.
    """
    uri = os.environ.get("NEO4J_URI")
    user = os.environ.get("NEO4J_USER")
    password = os.environ.get("NEO4J_PASSWORD")
    if not uri or not user or not password:
        raise AirflowFailException("NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD are required")
    return graph_driver(uri, (user, password))


def slack_settings() -> tuple[SecretStr, str]:
    token = os.environ.get("SLACK_BOT_TOKEN")
    channel = os.environ.get("SLACK_CHANNEL_MARKET")
    if not token or not channel:
        # 설정 누락이라 재시도해도 같다. 값 자체는 메시지에 넣지 않는다.
        raise AirflowFailException("SLACK_BOT_TOKEN and SLACK_CHANNEL_MARKET are required")
    return SecretStr(token), channel


def resolve_run_date(context: Any) -> date:
    """이 실행이 대상으로 삼는 세션 날짜(KST).

    **모양을 먼저 본다.** `date.fromisoformat`은 `2026-W32`도 받아 그 주의 월요일이 된다 —
    운영자가 넣은 값과 다른 날을 조용히 추론하게 된다.
    """
    given = (context.get("params") or {}).get(RUN_DATE_PARAM)
    if given:
        text = str(given).strip()
        if not CALENDAR_DAY_PATTERN.fullmatch(text):
            raise AirflowFailException(f"{RUN_DATE_PARAM} must be YYYY-MM-DD, got {given!r}")
        return date.fromisoformat(text)
    logical = context.get("logical_date") or datetime.now(UTC)
    return logical.astimezone(KST_TIMEZONE).date()


def slot_at(day: date, slot: RunSlot) -> datetime:
    """그 슬롯의 기준 시각(UTC). **벽시계가 아니라 표가 정한다.**"""
    return _kst(day, SLOT_TIMES[slot])


def open_at(day: date) -> datetime:
    return _kst(day, OPEN_TIME)


def close_at(day: date) -> datetime:
    return _kst(day, CLOSE_TIME)


def review_at(day: date) -> datetime:
    """장후 관찰의 기준 시각(UTC). `kis_index_daily`(18:20)와 수급 확정(18:10) 뒤다."""
    return _kst(day, REVIEW_TIME)


def _kst(day: date, moment: Any) -> datetime:
    return pendulum_instance(datetime.combine(day, moment), tz=KST_TIMEZONE).astimezone(UTC)


def label(moment: datetime) -> str:
    """모델과 Slack이 읽는 시각 표기. UTC ISO를 그대로 싣지 않는다."""
    return kst_label(moment, KST_TIMEZONE)


def relation_rows(
    graph_driver_handle: Any, *, as_of_date: date, as_of_at: datetime
) -> tuple[RelationRow, ...]:
    """관계 표. 관측이 0인 요인도 행으로 남긴다 — 빈 칸이 "관계 없음"으로 읽히면 안 된다.

    **컷오프는 `as_of_at`이다.** 날짜로 자르면 그날 저녁의 관찰이 그날 아침 전망에 보인다.
    """
    return tuple(
        RelationRow(
            factor=item.factor,
            label=item.label,
            weight=item.weight,
            n_obs=item.n_obs,
            last_date=item.last_date,
            last_note=item.last_note,
            recent_signs=item.recent_signs,
        )
        for item in read_relations(graph_driver_handle, as_of_date=as_of_date, as_of_at=as_of_at)
    )


def memory_rows(
    graph_driver_handle: Any, *, as_of_date: date, as_of_at: datetime
) -> tuple[MemoryRow, ...]:
    """그 시점에 활성이던 메모 표. 컷오프는 `as_of_at`이다."""
    return tuple(
        MemoryRow(
            id=item.id,
            created_on=item.created_on,
            text=item.text,
            factor=item.factor,
            verify_count=item.verify_count,
        )
        for item in read_memories(graph_driver_handle, as_of_date=as_of_date, as_of_at=as_of_at)
    )


def earlier_slots(store: KospiStore, *, run_date: date, slot: RunSlot) -> tuple[EarlierSlot, ...]:
    """오늘 앞선 슬롯의 답. 장전은 언제나 빈 튜플이다.

    **정답이 아니라 그때의 판단이다.** 프롬프트가 그것을 밝히고, 이유가 이어받으면
    `slot_ref`로 인용한다.

    이유는 문장만이 아니라 **요인 코드까지** 넘긴다. 장중 슬롯이 그 요인을 다시 조회해
    "아직 작용하나"를 판단하는 손잡이다.
    """
    order = {value: index for index, value in enumerate(SLOT_TIMES)}
    return tuple(
        EarlierSlot(
            slot=row.slot,
            as_of_kst=label(row.as_of_at),
            direction=row.direction,
            expected_change_pct=row.expected_change_pct,
            band_pct=row.band_pct,
            base_price=row.base_price,
            reasons=tuple(_earlier_reason(item) for item in row.reasons),
        )
        for row in store.forecasts(run_date)
        if order.get(row.slot, 99) < order.get(slot, 99)
    )


def _earlier_reason(item: dict[str, Any]) -> EarlierReason:
    """`kospi_forecast.reasons` JSONB의 항목 하나. 저장 전 검증을 지난 값이라 그대로 읽는다."""
    return EarlierReason(
        direction=Direction(item["direction"]),
        statement=str(item.get("statement", "")),
        factor=Factor(item["factor"]) if item.get("factor") else None,
        memory_id=item.get("memory_id"),
    )


def require_bars(store: KospiStore, *, as_of_at: datetime, before_date: date) -> tuple:
    """일봉 창을 읽고 모자라면 죽인다.

    **빈 봉을 채우지 않는다.** 창이 짧으면 모델이 최근 진폭을 잘못 읽고, 그것이 크기 답의
    출발점이다. 수집이 밀린 것이라면 재시도로 풀린다.
    """
    bars = store.bars(as_of_at=as_of_at, before_date=before_date, limit=BARS_WINDOW)
    if len(bars) < BARS_WINDOW:
        raise KospiNotReady(f"코스피 일봉이 {len(bars)}개뿐이다. {BARS_WINDOW}개가 필요하다")
    return bars


def build_observed_state(
    *,
    store: KospiStore,
    graph_handle: Any,
    run_date: date,
    slot: RunSlot,
    as_of_at: datetime,
    base_price: Decimal,
    base_at: datetime,
    base_note: str,
    intraday: Any = None,
) -> ObservedState:
    """관측 상태를 조립한다. **슬롯이 값으로만 흐른다.**

    슬롯마다 다른 것(기준가·장중 블록)은 이미 인자로 정해져 들어온다 — 이 함수는 그것을
    어디서 읽었는지 모른다.
    """
    logger.info(
        "관계 %s일 창으로 읽는다(as_of=%s, slot=%s)", RELATION_LOOKBACK_DAYS, as_of_at.isoformat(), slot.value
    )
    return ObservedState(
        run_date=run_date,
        slot=slot,
        as_of_kst=label(as_of_at),
        base_price=base_price,
        base_at_kst=label(base_at),
        base_note=base_note,
        bars=require_bars(store, as_of_at=as_of_at, before_date=run_date),
        moves=store.move_baseline(as_of_at=as_of_at, before_date=run_date),
        relations=relation_rows(graph_handle, as_of_date=run_date, as_of_at=as_of_at),
        memories=memory_rows(graph_handle, as_of_date=run_date, as_of_at=as_of_at),
        intraday=intraday,
        earlier_slots=earlier_slots(store, run_date=run_date, slot=slot),
    )


def conversation_id(run_date: date, slot: RunSlot | str) -> str:
    """xAI의 서버별 캐시를 맞추는 값. **결정적이어야 한다** — 난수면 재시도가 캐시를 버린다."""
    name = slot.value if isinstance(slot, RunSlot) else str(slot)
    return f"kospi-{run_date.isoformat()}-{name}"


def notify_forecast(built: dict[str, Any]) -> str:
    """이번 슬롯의 전망을 보낸다. **LLM을 다시 부르지 않는다.**

    `built`는 XCom을 지난 dict이고 날짜와 슬롯만 들어 있다 — 내용은 **DB에서 다시 읽는다.**
    발송이 재시도될 때 DB가 원본이어야 하고, 두 벌을 들고 다니면 어느 쪽이 맞는지 정해야 한다.
    """
    from airflow.sdk import get_current_context

    from modules.kospi.render import forecast_payload, render_blocks, render_text
    from modules.slack import SlackClient

    if not notify_enabled(get_current_context()):
        logger.info("notify=false — 발송을 건너뛴다")
        return "skipped"

    run_date = date.fromisoformat(str(built["run_date"]))
    slot = RunSlot(str(built["slot"]))
    with connection() as conn:
        rows = {row.slot: row for row in KospiStore(conn).forecasts(run_date)}
    row = rows.get(slot)
    if row is None:
        raise AirflowFailException(f"{run_date} {slot.value} 전망이 DB에 없다. 저장 태스크를 먼저 본다")

    payload = forecast_payload(row)
    payload["base_at_kst"] = label(row.base_at)
    token, channel = slack_settings()
    return SlackClient(token).post_message(channel, text=render_text(payload), blocks=render_blocks(payload))


def notify_review(built: dict[str, Any]) -> str:
    """장후 관찰을 보낸다. 채점 줄은 DB에서, 관찰과 메모 수는 XCom에서 온다.

    관찰 문장은 그래프에 있고 XCom에도 실린다 — 그래프를 다시 읽지 않는 이유는 그 사이에
    다음 실행이 쓰지 않기 때문이다(하루 한 번).
    """
    from airflow.sdk import get_current_context

    from modules.kospi.render import render_blocks, render_text
    from modules.slack import SlackClient

    if not notify_enabled(get_current_context()):
        logger.info("notify=false — 발송을 건너뛴다")
        return "skipped"

    run_date = date.fromisoformat(str(built["run_date"]))
    with connection() as conn:
        rows = KospiStore(conn).forecasts(run_date)

    payload = dict(built)
    payload["kind"] = "review"
    payload["grades"] = [
        {
            "slot": row.slot.value,
            "direction": row.direction.value,
            "expected_change_pct": str(row.expected_change_pct),
            "band_pct": str(row.band_pct),
            "actual_change_pct": None if row.actual_change_pct is None else str(row.actual_change_pct),
            "hit": row.hit,
            "within_band": row.within_band,
        }
        for row in rows
    ]
    token, channel = slack_settings()
    return SlackClient(token).post_message(channel, text=render_text(payload), blocks=render_blocks(payload))


def index_code() -> str:
    return INDEX_CODE
