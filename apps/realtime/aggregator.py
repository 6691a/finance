"""틱을 1분봉으로 모으는 집계기와 세션 창.

I/O가 없는 순수 상태 기계다. 집계 규칙은 문서 7.2~7.3:
open=이벤트 시각이 가장 이른 체결, close=가장 늦은 체결(동시각은 수신 순서),
volume=합. 연결 시각이 담긴 분과 flush 뒤의 늦은 틱은 저장하지 않는다.
"""

from datetime import UTC, datetime, time
from decimal import Decimal

from pydantic import AwareDatetime, BaseModel, ConfigDict

from apps.models.market import StockExchange
from apps.realtime.frames import KST, Tick

# 세션 창(KST, 분 기준 양끝 포함). REST 일별 수집(`modules.collectors.kis`)과 같은
# 값이어야 WS에만 구멍이 생기지 않는다. 테스트가 둘을 대조한다.
SESSION_WINDOWS: dict[StockExchange, tuple[time, time]] = {
    StockExchange.KRX: (time(9, 0), time(15, 30)),
    StockExchange.NXT: (time(8, 0), time(20, 0)),
}


class AggregatedBar(BaseModel):
    """닫힌 1분봉 하나. `bar_at`은 분 시작(UTC)이다."""

    model_config = ConfigDict(frozen=True)

    exchange: StockExchange
    stock_code: str
    bar_at: AwareDatetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


class _OpenBar:
    """집계 중인 분 하나. 순수 가변 상태라 Pydantic을 쓰지 않는다."""

    __slots__ = ("close", "close_key", "high", "low", "open", "open_key", "volume")

    def __init__(self, tick: Tick, sequence: int) -> None:
        self.open = tick.price
        self.open_key = (tick.occurred_at, sequence)
        self.close = tick.price
        self.close_key = (tick.occurred_at, sequence)
        self.high = tick.price
        self.low = tick.price
        self.volume = tick.volume

    def absorb(self, tick: Tick, sequence: int) -> None:
        key = (tick.occurred_at, sequence)
        # 이벤트 시각이 기준이고 동시각은 수신 순서다(문서 7.2). sequence가 그 순서다.
        if key < self.open_key:
            self.open = tick.price
            self.open_key = key
        if key >= self.close_key:
            self.close = tick.price
            self.close_key = key
        self.high = max(self.high, tick.price)
        self.low = min(self.low, tick.price)
        self.volume += tick.volume


def minute_of(moment: datetime) -> datetime:
    return moment.replace(second=0, microsecond=0)


class MinuteAggregator:
    """틱을 (거래소, 종목, 분) 단위 봉으로 모은다. I/O가 없는 순수 상태 기계다."""

    def __init__(self) -> None:
        self._bars: dict[tuple[StockExchange, str, datetime], _OpenBar] = {}
        self._sequence = 0
        self._watermark: datetime | None = None
        self._flushed_until: datetime | None = None
        self.late_tick_count = 0
        self.skipped_partial_count = 0
        self.dropped_open_count = 0

    def mark_connected(self, now: datetime) -> None:
        """연결(재연결) 시각이 담긴 분을 저장 금지 수위선으로 기록한다.

        체결 프레임에 전역 체결 ID가 없어 분 중간에 붙은 연결의 첫 분은 앞부분이
        비었는지 알 수 없다. 그 분은 버리고 REST 확정에 맡긴다(문서 7.2).
        """
        watermark = minute_of(now.astimezone(UTC))
        if self._watermark is None or watermark > self._watermark:
            self._watermark = watermark

    def add(self, tick: Tick) -> None:
        bar_at = minute_of(tick.occurred_at)
        if self._flushed_until is not None and bar_at < self._flushed_until:
            # 이미 닫은 분의 늦은 틱. 병합하면 flush된 값과 어긋나므로 버리고 센다.
            self.late_tick_count += 1
            return
        self._sequence += 1
        key = (tick.exchange, tick.stock_code, bar_at)
        open_bar = self._bars.get(key)
        if open_bar is None:
            self._bars[key] = _OpenBar(tick, self._sequence)
        else:
            open_bar.absorb(tick, self._sequence)

    def flush_before(self, boundary: datetime) -> tuple[AggregatedBar, ...]:
        """`boundary`(분 시작, UTC) 이전의 분을 전부 닫아 돌려준다."""
        boundary = minute_of(boundary.astimezone(UTC))
        closed = []
        for key in sorted(key for key in self._bars if key[2] < boundary):
            exchange, stock_code, bar_at = key
            open_bar = self._bars.pop(key)
            if self._watermark is not None and bar_at <= self._watermark:
                # 연결 시각이 담긴 분은 불완전하다. 저장하지 않는다.
                self.skipped_partial_count += 1
                continue
            closed.append(
                AggregatedBar(
                    exchange=exchange,
                    stock_code=stock_code,
                    bar_at=bar_at,
                    open=open_bar.open,
                    high=open_bar.high,
                    low=open_bar.low,
                    close=open_bar.close,
                    volume=open_bar.volume,
                )
            )
        if self._flushed_until is None or boundary > self._flushed_until:
            self._flushed_until = boundary
        return tuple(closed)

    def drop_open_minutes(self) -> None:
        """끊김·종료 시 열린 분을 폐기한다. 불완전한 봉을 완전한 것처럼 저장하지 않는다."""
        self.dropped_open_count += len(self._bars)
        self._bars.clear()


def in_session(exchange: StockExchange, occurred_at: datetime) -> bool:
    """틱이 담길 분이 REST 일별 수집과 같은 창 안인지 본다.

    KRX 09:00~15:30, NXT 08:00~20:00(분 기준, 양끝 포함). NXT 세션 사이 공백은 체결이
    없어 자연히 봉이 비므로 창을 셋으로 나누지 않는다(kis.py 실측).
    """
    first_bar, last_bar = SESSION_WINDOWS[exchange]
    minute = minute_of(occurred_at.astimezone(KST)).time()
    return first_bar <= minute <= last_bar
