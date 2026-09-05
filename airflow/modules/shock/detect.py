"""급변 판정. **순수 함수다** — DB도 Airflow도 시계도 안 본다.

봉 목록과 임계만 받아서 답을 낸다. 그래서 경계값(1.99% vs 2.00%)을 손으로 만든 봉으로
잠글 수 있고, 테스트에 DB가 필요 없다.

## 무엇을 재나

**봉마다, 그 봉의 값을 직전 봉들의 극값과 견준다.**

    하락폭(t) = 봉 t의 저가 / 창 시작~(t-1)의 최고가 - 1
    상승폭(t) = 봉 t의 고가 / 창 시작~(t-1)의 최저가 - 1

**극값에서 그 봉 자신을 뺀다.** 넣으면 봉 하나의 고가-저가 폭이 그대로 신호가 된다 —
100에서 98로 떨어진 봉은 자기 저가가 최저점이라 `98 → 100`을 +2.04% 급등으로도 읽어,
큰 음봉이 하락과 급등에 동시에 걸린다. 1분 안의 왕복은 30분 창의 움직임이 아니다.

그리고 이렇게 두면 **극값이 언제나 트리거 봉보다 앞선다.** 순서 제약을 따로 걸 필요가 없다.

**종가 등락률을 쓰지 않는 이유가 여기 있다.** 2026-09-02는 종가가 -3.99%인데 30분 창에서
-1.5%를 한 번도 안 넘었다 — 하루 종일 눌린 추세 하락이고, 그날은 아시아 일봉만 봐도
동반 하락이 보인다. 이 장치가 필요 없는 날이라 안 고르는 것이 맞다.
"""

from datetime import datetime
from decimal import Decimal

from modules.shock.domain import (
    MIN_PEER_BARS,
    MIN_TRIGGER_BARS,
    PCT_EXPONENT,
    Bar,
    Direction,
    PeerMove,
    PeerSpec,
    ShockEvent,
)


def _pct(value: Decimal, base: Decimal) -> Decimal:
    """`base` 대비 `value`의 등락률(퍼센트). 저장 자릿수로 내린다."""
    return ((value / base - 1) * 100).quantize(PCT_EXPONENT)


def window_change_pct(bars: list[Bar]) -> Decimal | None:
    """창의 첫 시가 대비 마지막 종가 등락(퍼센트).

    **peers와 같은 눈금이다.** 트리거 값(`move_pct`)은 극값 기준이라 축이 다르고, 둘을
    한 줄에 섞어 보여 주면 읽는 사람이 코스피와 다른 시장을 비교할 수 없다.
    """
    if not bars or bars[0].open <= 0:
        return None
    return _pct(bars[-1].close, bars[0].open)


def detect(
    bars: list[Bar],
    *,
    symbol: str,
    threshold_pct: Decimal,
    window_start: datetime,
    window_end: datetime,
    min_bars: int = MIN_TRIGGER_BARS,
) -> ShockEvent | None:
    """창 안에서 처음 임계에 닿은 봉을 찾는다. 없으면 `None`.

    `threshold_pct`는 **양수**로 받는다. 하락이면 부호를 뒤집어 비교한다.

    봉이 `min_bars`에 못 미치면 판정하지 않는다 — 개장 직후나 수집이 밀린 구간이고,
    두 봉으로 "30분 창"이라고 부르면 그 값이 거짓이 된다. 다음 실행이 같은 창을 다시 본다.
    """
    if len(bars) < min_bars:
        return None

    ordered = sorted(bars, key=lambda bar: bar.bar_at)
    change = window_change_pct(ordered)

    # 극값은 **그 봉보다 앞선 봉들**에서만 잡는다.
    #
    # 현재 봉을 극값에 포함하면 봉 하나의 고가-저가 폭이 그대로 신호가 된다. 100에서
    # 98로 떨어진 봉은 자기 저가가 최저점이라 `98 → 100`을 +2.04% 급등으로도 읽어,
    # 모든 큰 음봉이 급등으로 동시에 잡혔다. 1분 안의 왕복은 30분 창의 움직임이 아니다.
    peak, peak_at = ordered[0].high, ordered[0].bar_at
    trough, trough_at = ordered[0].low, ordered[0].bar_at
    for bar in ordered[1:]:
        drop = _pct(bar.low, peak) if peak > 0 else Decimal(0)
        surge = _pct(bar.high, trough) if trough > 0 else Decimal(0)

        hit_drop = drop <= -threshold_pct
        hit_surge = surge >= threshold_pct
        if hit_drop or hit_surge:
            # 양쪽에 다 닿는 것은 창 안에서 왕복한 경우다. 큰 쪽을 그 봉의 사건으로 본다.
            if hit_drop and (not hit_surge or -drop >= surge):
                direction, move, extreme, extreme_at, trigger = Direction.DROP, drop, peak, peak_at, bar.low
            else:
                direction, move, extreme, extreme_at, trigger = Direction.SURGE, surge, trough, trough_at, bar.high
            return _event(
                symbol=symbol,
                direction=direction,
                bar=bar,
                window_start=window_start,
                window_end=window_end,
                extreme_at=extreme_at,
                extreme=extreme,
                trigger=trigger,
                move=move,
                change=change,
                bar_count=len(ordered),
                threshold_pct=threshold_pct,
            )

        if bar.high > peak:
            peak, peak_at = bar.high, bar.bar_at
        if bar.low < trough:
            trough, trough_at = bar.low, bar.bar_at
    return None


def _event(
    *,
    symbol: str,
    direction: Direction,
    bar: Bar,
    window_start: datetime,
    window_end: datetime,
    extreme_at: datetime,
    extreme: Decimal,
    trigger: Decimal,
    move: Decimal,
    change: Decimal | None,
    bar_count: int,
    threshold_pct: Decimal,
) -> ShockEvent:
    return ShockEvent(
        symbol=symbol,
        direction=direction,
        detected_at=bar.bar_at,
        window_start=window_start,
        window_end=window_end,
        extreme_at=extreme_at,
        extreme_price=extreme,
        trigger_price=trigger,
        move_pct=move,
        window_change_pct=change,
        bar_count=bar_count,
        threshold_pct=threshold_pct,
    )


def peer_move(spec: PeerSpec, bars: list[Bar], *, min_bars: int = MIN_PEER_BARS) -> PeerMove:
    """한 시장의 창 등락. 봉이 모자라면 **값 없이** 돌려준다.

    0%로 채우지 않는다. "안 움직였다"와 "못 봤다"를 섞으면 포착이 거짓말을 한다 —
    니케이가 15~16분 지연이라 실제로 자주 일어난다.
    """
    base = {"symbol": spec.symbol, "label": spec.label, "region": spec.region}
    if len(bars) < min_bars:
        return PeerMove(**base, bars=len(bars))
    ordered = sorted(bars, key=lambda bar: bar.bar_at)
    change = window_change_pct(ordered)
    if change is None:
        return PeerMove(**base, bars=len(ordered))
    return PeerMove(**base, change_pct=change, bars=len(ordered), available=True)
