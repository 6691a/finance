"""급변 포착의 DB 층. 연결과 제공처를 쥐므로 클래스다.

`detect.py`가 판정을 갖고 여기는 **행을 읽고 쓰는 일만** 한다. 그 경계 덕에 판정 테스트에
DB가 필요 없다.
"""

import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from modules.db import Connection
from modules.shock.domain import (
    BAR_PROVIDER,
    INDEX_BAR_TABLE,
    INDEX_FUTURE_BAR_TABLE,
    MAX_EVENTS_PER_RUN,
    MIN_DOCUMENT_SCORE,
    Bar,
    Direction,
    DocumentRow,
    PeerMove,
    PeerSpec,
    ShockEvent,
    _State,
)
from modules.sql import read_sql

logger = logging.getLogger(__name__)

SEARCH_PROVIDER = "tavily"

SELECT_BARS = read_sql("postgres", "market_shock_event", "select_bars.sql")
SELECT_PEER_BARS = read_sql("postgres", "market_shock_event", "select_peer_bars.sql")
SELECT_LAST_DETECTED = read_sql("postgres", "market_shock_event", "select_last_detected.sql")
INSERT_EVENT = read_sql("postgres", "market_shock_event", "insert.sql")
UPDATE_NOTIFIED = read_sql("postgres", "market_shock_event", "update_notified.sql")
SELECT_PENDING_CAUSES = read_sql("postgres", "market_shock_event", "select_pending_causes.sql")
CLOSE_EXPIRED_CAUSES = read_sql("postgres", "market_shock_event", "close_expired_causes.sql")
UPDATE_CAUSE_ATTEMPT = read_sql("postgres", "market_shock_event", "update_cause_attempt.sql")
UPDATE_CAUSE = read_sql("postgres", "market_shock_event", "update_cause.sql")
SELECT_DOCUMENTS_AFTER = read_sql("postgres", "shock_documents", "select_after_event.sql")
INSERT_SEARCH_HIT = read_sql("postgres", "market_shock_search_hit", "insert.sql")
UPDATE_SEARCH_CITED = read_sql("postgres", "market_shock_search_hit", "update_cited.sql")
SELECT_NTH_OPEN_DAY = read_sql("postgres", "market_session", "select_nth_open_day.sql")


class ShockStoreError(RuntimeError):
    """행이 있어야 할 자리에 없다. 다시 불러도 같은 결과다."""


class PendingCause(_State):
    """원인을 아직 못 찾은 급변 하나. `select_pending_causes.sql`의 한 행이다."""

    id: int
    symbol: str
    session_date: date
    direction: Direction
    detected_at: datetime
    window_start: datetime
    window_end: datetime
    extreme_at: datetime
    extreme_price: Decimal
    trigger_price: Decimal
    move_pct: Decimal
    window_change_pct: Decimal | None = None
    peers: tuple[PeerMove, ...] = ()
    deadline: date | None = None
    attempts: int = 0


class ExpiredCause(_State):
    """기한을 다 쓰고 닫힌 급변 하나. Slack에 실을 것만 갖는다."""

    id: int
    symbol: str
    session_date: date
    direction: Direction
    detected_at: datetime
    move_pct: Decimal
    attempts: int


def _bar(row: tuple) -> Bar:
    bar_at, open_, high, low, close = row
    return Bar(bar_at=bar_at, open=open_, high=high, low=low, close=close)


class ShockStore:
    """연결과 봉 제공처를 쥔다. 창은 호출마다 바뀌므로 메서드 인자다."""

    def __init__(self, connection: Connection, *, provider: str = BAR_PROVIDER) -> None:
        self._connection = connection
        self._provider = provider

    def bars(self, symbol: str, *, window_start: datetime, window_end: datetime) -> list[Bar]:
        """대상 하나의 창 봉. 0건일 수 있다."""
        with self._connection.cursor() as cursor:
            cursor.execute(
                SELECT_BARS,
                {
                    "provider": self._provider,
                    "symbol": symbol,
                    "window_start": window_start,
                    "window_end": window_end,
                },
            )
            return [_bar(row) for row in cursor.fetchall()]

    def peer_bars(
        self, specs: tuple[PeerSpec, ...], *, window_start: datetime, window_end: datetime
    ) -> dict[str, list[Bar]]:
        """여러 시장의 창 봉을 한 번에. **봉이 0건인 심볼은 빈 목록으로 채워 돌려준다.**

        빠진 키와 빈 값을 부르는 쪽이 다르게 다루면 "못 봤다"가 조용히 사라진다.

        표가 둘이다 — 아시아 지수는 `index_bar`, 미국 선물은 `index_future_bar`. 한국
        장중에 미국 현물장은 닫혀 있어 현물 봉이 없다.
        """
        by_table: dict[str, list[str]] = defaultdict(list)
        providers: dict[str, str] = {}
        for spec in specs:
            by_table[spec.table].append(spec.symbol)
            providers[spec.table] = spec.provider

        grouped: dict[str, list[Bar]] = defaultdict(list)
        with self._connection.cursor() as cursor:
            cursor.execute(
                SELECT_PEER_BARS,
                {
                    "index_provider": providers.get(INDEX_BAR_TABLE, self._provider),
                    "index_symbols": by_table.get(INDEX_BAR_TABLE, []),
                    "future_provider": providers.get(INDEX_FUTURE_BAR_TABLE, "yahoo"),
                    "future_symbols": by_table.get(INDEX_FUTURE_BAR_TABLE, []),
                    "window_start": window_start,
                    "window_end": window_end,
                },
            )
            for symbol, *rest in cursor.fetchall():
                grouped[symbol].append(_bar(tuple(rest)))
        return {spec.symbol: grouped.get(spec.symbol, []) for spec in specs}

    def last_detected_at(self, symbol: str) -> datetime | None:
        """그 대상의 가장 최근 포착 시각. 없으면 `None`."""
        with self._connection.cursor() as cursor:
            cursor.execute(SELECT_LAST_DETECTED, {"symbol": symbol})
            row = cursor.fetchone()
        return row[0] if row else None

    def nth_open_day(self, session_date: date, offset: int) -> date | None:
        """`session_date`부터 세어 `offset`번째 KRX 개장일. 달력이 안 채워졌으면 `None`.

        **날짜를 우리가 세지 않는다.** 휴장일에서 어긋난다 — 판정의 주인은
        `market_session.effective_open_day`이고 그것을 채우는 것은 `market_calendar_daily`다.
        """
        with self._connection.cursor() as cursor:
            cursor.execute(SELECT_NTH_OPEN_DAY, (session_date, offset))
            row = cursor.fetchone()
        return row[0] if row else None

    def save(
        self,
        event: ShockEvent,
        *,
        session_date: date,
        peers: list[PeerMove],
        cause_deadline: date | None,
    ) -> int | None:
        """포착 하나를 쓴다. 이미 있으면 `None` — 그때는 알림도 다시 보내지 않는다."""
        with self._connection.cursor() as cursor:
            cursor.execute(
                INSERT_EVENT,
                {
                    "symbol": event.symbol,
                    "session_date": session_date,
                    "direction": event.direction.value,
                    "detected_at": event.detected_at,
                    "window_start": event.window_start,
                    "window_end": event.window_end,
                    "extreme_at": event.extreme_at,
                    "extreme_price": event.extreme_price,
                    "trigger_price": event.trigger_price,
                    "move_pct": event.move_pct,
                    "window_change_pct": event.window_change_pct,
                    "bar_count": event.bar_count,
                    "peers": json.dumps(
                        [peer.model_dump(mode="json") for peer in peers],
                        ensure_ascii=False,
                    ),
                    "threshold_pct": event.threshold_pct,
                    "cause_deadline": cause_deadline,
                },
            )
            row = cursor.fetchone()
        return row[0] if row else None

    def mark_notified(self, event_id: int, notified_at: datetime) -> None:
        """알림을 보낸 시각을 찍는다. 저장과 다른 트랜잭션이다."""
        with self._connection.cursor() as cursor:
            cursor.execute(UPDATE_NOTIFIED, {"id": event_id, "notified_at": notified_at})

    # --- 원인 분석 -------------------------------------------------------------

    def pending_causes(self, *, today: date, limit: int = MAX_EVENTS_PER_RUN) -> list[PendingCause]:
        """원인을 아직 못 찾았고 기한이 남은 급변들. 대개 0~2건이다."""
        with self._connection.cursor() as cursor:
            cursor.execute(SELECT_PENDING_CAUSES, {"today": today, "limit": limit})
            rows = cursor.fetchall()
        return [
            PendingCause(
                id=row[0],
                symbol=row[1],
                session_date=row[2],
                direction=Direction(row[3]),
                detected_at=row[4],
                window_start=row[5],
                window_end=row[6],
                extreme_at=row[7],
                extreme_price=row[8],
                trigger_price=row[9],
                move_pct=row[10],
                window_change_pct=row[11],
                peers=tuple(PeerMove.model_validate(peer) for peer in (row[12] or [])),
                deadline=row[13],
                attempts=row[14],
            )
            for row in rows
        ]

    def close_expired_causes(self, *, today: date, resolved_at: datetime) -> list[ExpiredCause]:
        """기한이 지난 것을 `unknown`으로 닫고 **이번에 닫힌 것만** 돌려준다."""
        with self._connection.cursor() as cursor:
            cursor.execute(CLOSE_EXPIRED_CAUSES, {"today": today, "resolved_at": resolved_at})
            rows = cursor.fetchall()
        return [
            ExpiredCause(
                id=row[0],
                symbol=row[1],
                session_date=row[2],
                direction=Direction(row[3]),
                detected_at=row[4],
                move_pct=row[5],
                attempts=row[6],
            )
            for row in rows
        ]

    def start_attempt(self, event_id: int, *, deadline: date | None) -> tuple[int, date | None]:
        """시도 횟수를 올리고 기한이 비어 있으면 채운다. **모델을 부르기 전에 커밋한다.**

        부르고 나서 올리면 죽은 실행이 안 세어져 "안 돌았다"와 "돌다 죽었다"를 못 가른다.
        """
        with self._connection.cursor() as cursor:
            cursor.execute(UPDATE_CAUSE_ATTEMPT, {"id": event_id, "deadline": deadline})
            row = cursor.fetchone()
        if row is None:
            raise ShockStoreError(f"market_shock_event {event_id} disappeared while starting an attempt")
        return row[0], row[1]

    def documents_after(
        self,
        *,
        event_at: datetime,
        as_of_at: datetime,
        limit: int,
        min_score: int = MIN_DOCUMENT_SCORE,
    ) -> list[DocumentRow]:
        """급변 시각 **이후** 발행된 문서. 그 이전 것도, 점수 하한 아래도 안 준다."""
        with self._connection.cursor() as cursor:
            cursor.execute(
                SELECT_DOCUMENTS_AFTER,
                {"event_at": event_at, "as_of_at": as_of_at, "limit": limit, "min_score": min_score},
            )
            rows = cursor.fetchall()
        return [
            DocumentRow(
                id=row[0],
                published_at=row[1],
                source_slug=row[2],
                title=row[3],
                value_score=row[4],
                reason=row[5] or "",
                new_facts=tuple(row[6] or ()),
            )
            for row in rows
        ]

    def save_search_hits(self, event_id: int, hits: list, *, attempt: int, retrieved_at: datetime) -> int:
        """검색 결과를 영구 보관한다. **받은 것을 전부 남긴다.**

        같은 URL이 이미 있으면 건너뛴다 — 처음 본 질의·시도·순위가 남아야 "언제 처음 이
        기사를 봤나"가 사라지지 않는다.
        """
        stored = 0
        with self._connection.cursor() as cursor:
            for hit in hits:
                cursor.execute(
                    INSERT_SEARCH_HIT,
                    {
                        "shock_event_id": event_id,
                        "provider": SEARCH_PROVIDER,
                        "query": hit.query,
                        "attempt": attempt,
                        "rank": hit.rank,
                        "title": hit.title,
                        "url": hit.url,
                        "publisher": hit.publisher,
                        "published_at": hit.published_at,
                        "snippet": hit.snippet,
                        "relevance": hit.relevance,
                        "retrieved_at": retrieved_at,
                    },
                )
                if cursor.fetchone():
                    stored += 1
        return stored

    def mark_search_cited(self, event_id: int, urls: list[str]) -> None:
        """모델이 근거로 든 검색 결과에 표시한다."""
        if not urls:
            return
        with self._connection.cursor() as cursor:
            cursor.execute(UPDATE_SEARCH_CITED, {"shock_event_id": event_id, "urls": urls})

    def resolve_cause(
        self,
        event_id: int,
        *,
        cause_text: str,
        cause_kind: str,
        document_ids: list[int],
        search_used: bool,
        weak: bool,
        prompt_version: str,
        llm_model: str,
        resolved_at: datetime,
    ) -> bool:
        """찾은 원인을 쓰고 닫는다. 이미 닫혀 있으면 `False` — 첫 성공본은 불변이다."""
        with self._connection.cursor() as cursor:
            cursor.execute(
                UPDATE_CAUSE,
                {
                    "id": event_id,
                    "cause_text": cause_text,
                    "cause_kind": cause_kind,
                    "cause_document_ids": json.dumps(document_ids),
                    "cause_search_used": search_used,
                    "cause_weak": weak,
                    "prompt_version": prompt_version,
                    "llm_model": llm_model,
                    "resolved_at": resolved_at,
                },
            )
            return cursor.fetchone() is not None


def within_cooldown(detected_at: datetime, last_detected_at: datetime | None, minutes: int) -> bool:
    """직전 포착이 `minutes` 안이면 같은 사건으로 본다.

    순수 함수라 `ShockStore` 밖에 둔다. 경계값(정확히 60분)은 **밖**이다 — 쿨다운이
    끝난 순간부터 새 사건을 받는다.
    """
    if last_detected_at is None:
        return False
    return detected_at - last_detected_at < timedelta(minutes=minutes)
