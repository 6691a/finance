"""확정 일봉에서 매매 신호를 검출해 `technical_signal`에 저장한다.

계산은 `modules/technical.py`가 하고 여기는 조회·정렬·저장만 한다. 그래서 계산기는 계속
DB를 모른다. 설계는 docs/market-technical-indicators.md 12.3절이다.

조회는 추론 툴·브리핑과 **같은 SQL**을 쓴다(`technical/select_history.sql`). 지표와 신호가
같은 봉을 봐야 Slack 표의 SMA와 신호의 SMA가 어긋나지 않는다.
"""

import logging
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from modules import technical
from modules.db import Connection
from modules.sql import read_sql
from modules.technical import TECHNICAL_LOOKBACK_BARS
from modules.upsert import execute_upserts

logger = logging.getLogger(__name__)

TECHNICAL_HISTORY = read_sql("postgres", "technical", "select_history.sql")
SIGNAL_UPSERT = read_sql("postgres", "technical_signal", "upsert.sql")

# 신호를 검출하는 지수. 종목은 `instrument.is_watched`가 정하므로 여기 적지 않는다.
SIGNAL_INDEXES: tuple[str, ...] = ("KOSPI", "KOSDAQ")

# 국내 종목의 하루 가격제한폭보다 큰 단절은 분할·병합이나 원천 이상을 의심한다(문서 5.1절).
DOMESTIC_MAX_DAILY_CHANGE_PCT = 35.0


class TechnicalSignalError(RuntimeError):
    """신호를 낼 수 있는 대상이 하나도 없었다. 조용한 성공으로 넘기지 않는다."""
class SignalRun(BaseModel):
    """한 번의 검출 결과. 저장한 사건 수와 표본이 모자라 건너뛴 대상 이름이다."""

    model_config = ConfigDict(frozen=True)

    stored: int
    subjects: tuple[str, ...]
    skipped: tuple[str, ...]


def detect_and_store(connection: Connection, *, as_of_at: datetime, scan_bars: int) -> SignalRun:
    """지수와 watched 종목의 최근 `scan_bars`봉에서 사건을 찾아 저장한다.

    **대상 전부를 건너뛰면 실패다.** 0건 저장은 "교차가 없었다"는 정상 상태이지만, 볼 대상이
    하나도 없는 것은 앞단 수집이 비었다는 뜻이다. 그것을 성공으로 표시하면 다음 실행도 같은
    자리에서 조용히 지나간다.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            TECHNICAL_HISTORY,
            {
                "symbols": list(SIGNAL_INDEXES),
                "include_watched": True,
                "as_of_at": as_of_at,
                "limit": TECHNICAL_LOOKBACK_BARS,
            },
        )
        rows = list(cursor.fetchall())

    grouped: dict[str, list[Sequence[Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row[1]), []).append(row)

    if not grouped:
        raise TechnicalSignalError(f"No daily bars to scan as of {as_of_at.isoformat()}")

    stored = 0
    subjects: list[str] = []
    skipped: list[str] = []
    for symbol, subject_rows in grouped.items():
        # 조회는 최신순이고 계산기는 오름차순을 받는다.
        ascending = list(reversed(subject_rows))
        events = technical.detect_signals(
            [
                technical.DailyBar(
                    business_date=row[5],
                    open=float(row[6]),
                    high=float(row[7]),
                    low=float(row[8]),
                    close=float(row[9]),
                    volume=None if row[10] is None else int(row[10]),
                )
                for row in ascending
            ],
            scan_bars=scan_bars,
            max_abs_daily_change_pct=DOMESTIC_MAX_DAILY_CHANGE_PCT,
        )
        if len(ascending) < technical.TECHNICAL_MIN_BARS:
            logger.info("%s has only %s bars; skipping", symbol, len(ascending))
            skipped.append(symbol)
            continue

        subjects.append(symbol)
        if not events:
            continue
        provider = str(ascending[0][0])
        with connection.cursor() as cursor:
            execute_upserts(
                cursor,
                SIGNAL_UPSERT,
                [
                    (
                        provider,
                        symbol,
                        event.signal_date,
                        event.kind.value,
                        event.direction,
                        Decimal(str(event.close)),
                        Decimal(str(event.sma20)),
                        Decimal(str(event.sma60)),
                        Decimal(str(event.rsi14)),
                        Decimal(str(event.macd)),
                        Decimal(str(event.macd_signal)),
                        None if event.volume_ratio20 is None else Decimal(str(event.volume_ratio20)),
                        event.rule_version,
                    )
                    for event in events
                ],
            )
        stored += len(events)
        logger.info("Stored %s signals for %s", len(events), symbol)

    if not subjects:
        raise TechnicalSignalError(f"Every subject was too short to scan: {', '.join(sorted(skipped))}")
    return SignalRun(stored=stored, subjects=tuple(subjects), skipped=tuple(skipped))
