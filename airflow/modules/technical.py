"""확정 일봉에서 기술적 보조지표와 매매 신호(사건)를 계산하는 순수 모듈.

설계는 docs/analysis/market-technical-indicators.md 5절(지표)·12.1절(신호)이다. DB·Airflow·LLM을
import하지 않는다 — 조회는 부르는 쪽(thesis, briefing, technical_signals)이 하고 여기는
계산만 한다.

**지표 snapshot과 신호 검출이 같은 시리즈 함수를 쓴다.** 두 벌이 되면 Slack 표의 SMA와
신호의 SMA가 어긋나는 날이 온다. 시리즈 함수는 창 앞부분의 미정의 구간을 `None`으로 채워
길이를 입력과 같게 한다 — 인덱스가 봉과 1:1이어야 날짜를 잘못 붙이지 않는다.

계산은 반올림하지 않는다. 표시 자릿수는 JSON·Slack 경계가 줄인다. 라이브러리·증권사마다
EMA 초기값과 RSI 평활이 달라 값이 조금씩 다르므로, 아래 공식과 테스트의 고정 벡터가 이
프로젝트의 계약이다.
"""

from collections.abc import Sequence
from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# 계산에 쓰는 조회 창과 최소 표본. 60봉은 SMA60의 최소 요건이고 120봉은 EMA 안정화 여유다.
TECHNICAL_LOOKBACK_BARS = 120
TECHNICAL_MIN_BARS = 60

SMA_SHORT_BARS = 20
SMA_LONG_BARS = 60
RSI_BARS = 14
MACD_FAST_BARS = 12
MACD_SLOW_BARS = 26
MACD_SIGNAL_BARS = 9
VOLUME_WINDOW_BARS = 20

# 신호 검출 규칙의 버전. 규칙을 바꾸면 올린다 — thesis의 PROMPT_VERSION과 같은 역할이다.
RULE_VERSION = "1"
RSI_OVERSOLD = 30.0
RSI_OVERBOUGHT = 70.0
# 한 번에 다시 볼 수 있는 봉 수의 상한. 조회 창과 같다.
SIGNAL_SCAN_BARS_MAX = TECHNICAL_LOOKBACK_BARS


class DailyBar(BaseModel):
    """정규화한 확정 일봉 1건. 지수·종목의 컬럼 차이는 SQL이 이미 지웠다."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    business_date: date
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: int | None = Field(default=None, ge=0)


class TechnicalSnapshot(BaseModel):
    """가장 최근 봉 기준의 지표 한 벌. 해석은 소비자 몫이다."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    subject_code: str
    label: str
    as_of_date: date
    close: float
    sma20: float
    sma60: float
    rsi14: float
    macd: float
    macd_signal: float
    macd_histogram: float
    volume_ratio20: float | None
    observations: int


class SignalKind(StrEnum):
    """매매 신호(사건)의 종류. 값이 `technical_signal.kind`에 그대로 저장된다."""

    SMA_CROSS = "sma_cross"
    MACD_CROSS = "macd_cross"
    RSI_REVERSAL = "rsi_reversal"


class SignalEvent(BaseModel):
    """교차가 일어났다는 사건 하나. **판정이 아니다** — 사건이 유효했는지는 사후 수익률이 답한다.

    당시 지표값을 함께 담는 이유는 "거래량 동반 골든크로스만" 같은 사후 필터 분석이
    SQL로 되게 하기 위해서다.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    signal_date: date
    kind: SignalKind
    direction: Literal["up", "down"]
    close: float
    sma20: float
    sma60: float
    rsi14: float
    macd: float
    macd_signal: float
    volume_ratio20: float | None
    rule_version: str


def sma_series(values: Sequence[float], period: int) -> list[float | None]:
    """단순이동평균. 앞 `period - 1`칸은 `None`이다."""
    out: list[float | None] = [None] * len(values)
    running = 0.0
    for index, value in enumerate(values):
        running += value
        if index >= period:
            running -= values[index - period]
        if index >= period - 1:
            out[index] = running / period
    return out


def ema_series(values: Sequence[float | None], period: int) -> list[float | None]:
    """지수이동평균. 첫 값은 해당 기간 SMA로 시작한다(5.2절).

    입력의 앞쪽 `None` 구간(다른 시리즈의 미정의 창)은 건너뛰고, 정의된 값이 `period`개
    모인 지점부터 계산한다.
    """
    out: list[float | None] = [None] * len(values)
    start = next((i for i, value in enumerate(values) if value is not None), None)
    if start is None or len(values) - start < period:
        return out
    seed_end = start + period
    seed = sum(values[start:seed_end]) / period  # type: ignore[arg-type]
    out[seed_end - 1] = seed
    alpha = 2.0 / (period + 1)
    previous = seed
    for index in range(seed_end, len(values)):
        current = values[index]
        if current is None:  # pragma: no cover - 정의 구간은 연속이다
            return out
        previous = alpha * current + (1.0 - alpha) * previous
        out[index] = previous
    return out


def rsi_series(closes: Sequence[float], period: int = RSI_BARS) -> list[float | None]:
    """Wilder 평활 RSI. 첫 값은 처음 `period`개 변화의 평균으로 시작한다(5.2절)."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = 0.0
    losses = 0.0
    for index in range(1, period + 1):
        change = closes[index] - closes[index - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    average_gain = gains / period
    average_loss = losses / period
    out[period] = _rsi_value(average_gain, average_loss)
    for index in range(period + 1, len(closes)):
        change = closes[index] - closes[index - 1]
        average_gain = (average_gain * (period - 1) + max(change, 0.0)) / period
        average_loss = (average_loss * (period - 1) + max(-change, 0.0)) / period
        out[index] = _rsi_value(average_gain, average_loss)
    return out


def _rsi_value(average_gain: float, average_loss: float) -> float:
    if average_gain == 0.0 and average_loss == 0.0:
        return 50.0
    if average_loss == 0.0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + average_gain / average_loss)


def _volume_ratio(volumes: Sequence[int | None], index: int) -> float | None:
    """`index` 봉의 거래량 / 직전 20거래일 평균. 21개 중 하나라도 없거나 평균이 0이면 `None`."""
    if index < VOLUME_WINDOW_BARS:
        return None
    window = volumes[index - VOLUME_WINDOW_BARS : index + 1]
    if any(volume is None for volume in window):
        return None
    average = sum(window[:-1]) / VOLUME_WINDOW_BARS  # type: ignore[arg-type]
    if average == 0:
        return None
    return window[-1] / average  # type: ignore[operator]


def _validated(
    bars: Sequence[DailyBar],
    max_abs_daily_change_pct: float | None,
) -> bool:
    """계산해도 되는 입력인가. 조건은 5.1절 그대로다.

    거짓 지표를 내느니 안 내는 편이 낫다 — 60봉 미만, 날짜 역순·중복, guard를 넘는
    인접 종가 단절은 전부 계산 거부다.
    """
    if len(bars) < TECHNICAL_MIN_BARS:
        return False
    for index in range(1, len(bars)):
        if bars[index].business_date <= bars[index - 1].business_date:
            return False
        if max_abs_daily_change_pct is not None:
            change = abs(bars[index].close / bars[index - 1].close - 1.0) * 100.0
            if change > max_abs_daily_change_pct:
                return False
    return True


class IndicatorSeries(BaseModel):
    """한 대상의 지표 시리즈 전체. snapshot과 신호 검출, 일봉 차트가 같은 것을 본다."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    sma20: list[float | None]
    sma60: list[float | None]
    rsi14: list[float | None]
    macd: list[float | None]
    macd_signal: list[float | None]


def compute_series(closes: Sequence[float]) -> IndicatorSeries:
    """종가 시리즈 하나에서 이동평균·RSI·MACD를 한 번에 낸다.

    값이 없는 앞부분은 `None`이다. 0으로 채우면 차트가 0에서 솟는 가짜 선을 그린다.
    """
    fast = ema_series(list(closes), MACD_FAST_BARS)
    slow = ema_series(list(closes), MACD_SLOW_BARS)
    macd = [
        None if fast_value is None or slow_value is None else fast_value - slow_value
        for fast_value, slow_value in zip(fast, slow, strict=True)
    ]
    return IndicatorSeries(
        sma20=sma_series(closes, SMA_SHORT_BARS),
        sma60=sma_series(closes, SMA_LONG_BARS),
        rsi14=rsi_series(closes),
        macd=macd,
        macd_signal=ema_series(macd, MACD_SIGNAL_BARS),
    )


def summarize(
    subject_code: str,
    label: str,
    bars: Sequence[DailyBar],
    *,
    max_abs_daily_change_pct: float | None = None,
) -> TechnicalSnapshot | None:
    """가장 최근 봉의 지표 한 벌. 계산할 수 없으면 `None`이다 — 0으로 꾸미지 않는다."""
    if not _validated(bars, max_abs_daily_change_pct):
        return None
    closes = [bar.close for bar in bars]
    series = compute_series(closes)
    last = len(bars) - 1
    sma20 = series.sma20[last]
    sma60 = series.sma60[last]
    rsi14 = series.rsi14[last]
    macd = series.macd[last]
    macd_signal = series.macd_signal[last]
    if sma20 is None or sma60 is None or rsi14 is None or macd is None or macd_signal is None:
        return None  # pragma: no cover - TECHNICAL_MIN_BARS가 이미 보장한다
    return TechnicalSnapshot(
        subject_code=subject_code,
        label=label,
        as_of_date=bars[last].business_date,
        close=closes[last],
        sma20=sma20,
        sma60=sma60,
        rsi14=rsi14,
        macd=macd,
        macd_signal=macd_signal,
        macd_histogram=macd - macd_signal,
        volume_ratio20=_volume_ratio([bar.volume for bar in bars], last),
        observations=len(bars),
    )


def _crossed(previous: float, current: float) -> Literal["up", "down"] | None:
    """부호 교차. 정확히 0은 어느 쪽도 아니다(12.1절)."""
    if previous < 0.0 and current > 0.0:
        return "up"
    if previous > 0.0 and current < 0.0:
        return "down"
    return None


def detect_signals(
    bars: Sequence[DailyBar],
    *,
    scan_bars: int,
    max_abs_daily_change_pct: float | None = None,
) -> list[SignalEvent]:
    """마지막 `scan_bars`개 봉에서 일어난 사건을 검출한다.

    사건 행에 당시 지표 전체를 담으므로 **모든 지표가 정의된 봉부터만** 검출한다 —
    사실상 SMA60이 생기는 60번째 봉부터다. 계산할 수 없는 입력이면 빈 리스트다.
    """
    if not _validated(bars, max_abs_daily_change_pct):
        return []
    scan = min(scan_bars, SIGNAL_SCAN_BARS_MAX)
    closes = [bar.close for bar in bars]
    volumes = [bar.volume for bar in bars]
    series = compute_series(closes)
    events: list[SignalEvent] = []
    for index in range(max(1, len(bars) - scan), len(bars)):
        values = (
            series.sma20[index],
            series.sma60[index],
            series.rsi14[index],
            series.macd[index],
            series.macd_signal[index],
        )
        previous_values = (
            series.sma20[index - 1],
            series.sma60[index - 1],
            series.rsi14[index - 1],
            series.macd[index - 1],
            series.macd_signal[index - 1],
        )
        if any(value is None for value in values + previous_values):
            continue
        sma20, sma60, rsi14, macd, macd_signal = values  # type: ignore[assignment]
        previous_sma20, previous_sma60, previous_rsi, previous_macd, previous_signal = previous_values  # type: ignore[assignment]

        found: list[tuple[SignalKind, Literal["up", "down"]]] = []
        sma_direction = _crossed(previous_sma20 - previous_sma60, sma20 - sma60)
        if sma_direction:
            found.append((SignalKind.SMA_CROSS, sma_direction))
        macd_direction = _crossed(previous_macd - previous_signal, macd - macd_signal)
        if macd_direction:
            found.append((SignalKind.MACD_CROSS, macd_direction))
        if previous_rsi < RSI_OVERSOLD and rsi14 > RSI_OVERSOLD:
            found.append((SignalKind.RSI_REVERSAL, "up"))
        elif previous_rsi > RSI_OVERBOUGHT and rsi14 < RSI_OVERBOUGHT:
            found.append((SignalKind.RSI_REVERSAL, "down"))

        events.extend(
            SignalEvent(
                signal_date=bars[index].business_date,
                kind=kind,
                direction=direction,
                close=closes[index],
                sma20=sma20,
                sma60=sma60,
                rsi14=rsi14,
                macd=macd,
                macd_signal=macd_signal,
                volume_ratio20=_volume_ratio(volumes, index),
                rule_version=RULE_VERSION,
            )
            for kind, direction in found
        )
    return events
