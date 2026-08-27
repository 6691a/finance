"""조건부 기저율 — 같은 신호가 과거에 어떻게 끝났나.

프롬프트가 세 확률을 "그 일이 실제로 일어날 빈도"로 요구하면서 빈도 재료를 주지 않고
있었다. 그 자리에는 `recent_signals`가 사건으로만 실려 있고, 시스템 프롬프트가 "같은 사건이
과거에 얼마나 맞았는지는 너도 시스템도 아직 모른다"라고 적어 두었다. 이 모듈이 그것을 센다.
설계는 `docs/analysis/market-thesis/10-base-rate.md`다.

## 무조건 기저를 함께 준다

조건부만 주면 거짓말이 된다. 신호 뒤 상승 60퍼센트라도 그 심볼의 평소 상승이 55퍼센트면
그 신호가 더하는 것은 5퍼센트포인트다.

## 분류는 채점과 같은 함수로 한다

`thesis_domain.classify_outcome`을 그대로 쓴다. 임계(`FLAT_THRESHOLD_PCT`)는 TUNING 문서가
당기라고 적어 둔 손잡이라, 기저율과 채점이 다른 임계를 쓰면 두 숫자가 다른 세계를 말한다.

버킷팅이 SQL이 아니라 여기 있는 이유도 채점 수식과 같다 — 경계값을 DB 없이 테스트한다.

## 저장하지 않는다

테이블도 DAG도 두지 않는다. 프롬프트 조립 때마다 센다. 사전 계산 테이블을 두면 소급 조정
재백필마다 그것도 무효화해야 하고, 그 무효화를 빠뜨리면 옛 기준의 기저율이 조용히 나간다.

**연결과 기준 날짜를 받아 한 번 계산하고 끝난다.** 여러 호출에 걸쳐 들고 돌 상태가 없어
클래스가 아니라 함수다(`technical/signals.py`·`market_session.py`와 같은 형태).
"""

import logging
from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from modules.db import Connection
from modules.sql import read_sql
from modules.technical.indicators import RULE_VERSION
from modules.thesis_domain import ThesisDirection, classify_outcome
from modules.thesis_state import HorizonBaseRate, SignalBaseRate

logger = logging.getLogger(__name__)

FORWARD_RETURNS = read_sql("postgres", "technical_signal", "select_forward_returns.sql")
UNCONDITIONAL_RETURNS = read_sql("postgres", "technical_signal", "select_unconditional_returns.sql")

# 기저율을 재는 지평(KRX 영업일). 채점 지평(`THESIS_HORIZON_DAYS`)에서 0을 뺀 것이다 —
# 신호는 그날 종가로 검출되므로 T+0 등락률은 정의상 0이라 셀 것이 없다.
BASE_RATE_HORIZON_DAYS: tuple[int, ...] = (1, 3, 5)

# 비율을 낼 최소 표본. 이보다 적으면 비율을 전부 `None`으로 두고 `sample_size`만 준다.
# 0으로 채우거나 "n이 작으니 알아서 무시하라"고 프롬프트에 적지 않는다 — 모델은 숫자가
# 보이면 쓴다.
MIN_BASE_RATE_SAMPLE = 20

# 프롬프트에 싣는 `flat` 기준선을 재는 창(거래일). **전 이력이 아니다.**
#
# 이 값이 신호 기저율의 창과 다른 이유는 재는 대상이 다르기 때문이다. 신호는 "그 사건이
# 평소보다 나았나"라 사건과 같은 기간의 기저와 견줘야 하고(그래서 전 이력), `flat` 기준선은
# "앞으로 며칠 안에 실제로 얼마나 자주 일어나나"라 **지금 체제**를 재야 한다.
#
# 2026-08-26 실측이 그 차이를 보여 준다. 코스피의 `flat` 비율이 연도별로 2016년 45퍼센트에서
# 2026년 6퍼센트까지 **단조로 줄었다.** 창을 넓힐수록 값이 커진다(132봉 6.1, 250봉 10.8,
# 500봉 19.4, 1000봉 22.1, 전체 27.3). 진동이 아니라 추세라, 전 이력 평균은 지금을 4배
# 넘게 벗어난다.
#
# 250봉을 고른 것은 둘 사이다. 직전 상수는 132봉으로 쟀는데 그 창의 코스피 `flat`이 8건뿐
# 이라 얇고, 1년이면 조용했던 구간과 요동친 구간을 함께 담는다. **상수가 아니라 실행마다
# 다시 재므로 체제가 바뀌면 값이 따라간다** — 전에는 사람이 다시 재기 전까지 낡았다.
FLAT_BASE_RATE_BARS = 250


def _summarize(horizon_days: int, returns: Sequence[Decimal]) -> HorizonBaseRate:
    """등락률 목록 하나를 분포로. 분류는 채점과 같은 임계를 쓴다."""
    sample_size = len(returns)
    if sample_size < MIN_BASE_RATE_SAMPLE:
        return HorizonBaseRate(horizon_days=horizon_days, sample_size=sample_size)

    counts = {direction: 0 for direction in ThesisDirection}
    for value in returns:
        counts[classify_outcome(value, horizon_days)] += 1

    ordered = sorted(returns)
    middle = sample_size // 2
    median = ordered[middle] if sample_size % 2 else (ordered[middle - 1] + ordered[middle]) / 2

    return HorizonBaseRate(
        horizon_days=horizon_days,
        sample_size=sample_size,
        up=round(counts[ThesisDirection.UP] / sample_size, 4),
        flat=round(counts[ThesisDirection.FLAT] / sample_size, 4),
        down=round(counts[ThesisDirection.DOWN] / sample_size, 4),
        median_return_pct=round(float(median), 4),
    )


def unconditional_rates(
    connection: Connection,
    *,
    as_of_date: date,
    symbols: Sequence[str],
    horizons: Sequence[int] = BASE_RATE_HORIZON_DAYS,
    max_bars: int | None = None,
) -> dict[str, tuple[HorizonBaseRate, ...]]:
    """심볼마다 **아무 날이나**의 지평별 실현 분포. 조건부 기저율의 비교 대상이다.

    `max_bars`를 주면 최근 그만큼의 거래일만 센다. SQL이 거래일 오름차순으로 주므로 뒤에서
    자른다 — 봉 수 상한을 SQL에 넣으면 부르는 쪽 둘이 서로 안 쓰는 파라미터를 넘겨야 한다.
    """
    horizon_list = list(horizons)
    symbol_list = list(symbols)
    if not symbol_list or not horizon_list:
        return {}

    buckets: dict[str, dict[int, list[Decimal]]] = defaultdict(lambda: defaultdict(list))
    with connection.cursor() as cursor:
        cursor.execute(
            UNCONDITIONAL_RETURNS,
            {"as_of_date": as_of_date, "horizons": horizon_list, "symbols": symbol_list},
        )
        for symbol, horizon_days, return_pct in cursor.fetchall():
            buckets[str(symbol)][int(horizon_days)].append(return_pct)

    def window(values: list[Decimal]) -> list[Decimal]:
        return values if max_bars is None else values[-max_bars:]

    return {
        symbol: tuple(_summarize(horizon, window(rows.get(horizon, []))) for horizon in horizon_list)
        for symbol, rows in buckets.items()
    }


def flat_base_rates(
    connection: Connection,
    *,
    as_of_date: date,
    symbols: Sequence[str],
) -> dict[str, HorizonBaseRate]:
    """프롬프트에 싣는 심볼별 `flat` 기준선. **최근 `FLAT_BASE_RATE_BARS`봉이다.**

    모델이 `prob_flat`을 "±임계 안에 들어올 빈도"가 아니라 "방향을 모르겠다"로 읽어 30퍼센트대를
    주던 것을 막는 값이다. 그 자리에 상수를 박아 두었더니 6개월 만에 체제가 바뀌어 낡았다
    (`FLAT_BASE_RATE_BARS` 주석의 실측). 실행마다 다시 잰다.

    지평 1(하루)만 준다. 세 확률의 채점 창이 예측일 세션 하나라 그것과 같은 축이다.
    """
    rates = unconditional_rates(
        connection,
        as_of_date=as_of_date,
        symbols=symbols,
        horizons=(1,),
        max_bars=FLAT_BASE_RATE_BARS,
    )
    return {symbol: horizons[0] for symbol, horizons in rates.items() if horizons}


def signal_base_rates(
    connection: Connection,
    *,
    as_of_date: date,
    symbols: Sequence[str],
    horizons: Sequence[int] = BASE_RATE_HORIZON_DAYS,
) -> dict[tuple[str, str, str], SignalBaseRate]:
    """`(심볼, 종류, 방향)`마다 지평별 기저율. 무조건 기저는 심볼이 같으면 같은 값이다.

    조회하는 쪽이 심볼 목록을 정한다. 무조건 기저를 쓸데없이 넓게 재지 않기 위해서다.

    **비어 있는 것과 표본이 모자란 것은 다르다.** 사건이 아예 없으면 그 키가 없고, 있는데
    표본이 모자라면 키는 있고 비율이 `None`이다.
    """
    horizon_list = list(horizons)
    symbol_list = list(symbols)
    if not symbol_list or not horizon_list:
        return {}

    conditional: dict[tuple[str, str, str], dict[int, list[Decimal]]] = defaultdict(lambda: defaultdict(list))
    with connection.cursor() as cursor:
        cursor.execute(
            FORWARD_RETURNS,
            {"as_of_date": as_of_date, "horizons": horizon_list, "rule_version": RULE_VERSION},
        )
        for symbol, kind, direction, _signal_date, horizon_days, return_pct in cursor.fetchall():
            if symbol not in symbol_list:
                continue
            conditional[(str(symbol), str(kind), str(direction))][int(horizon_days)].append(return_pct)

    baseline = unconditional_rates(
        connection, as_of_date=as_of_date, symbols=symbol_list, horizons=horizon_list
    )

    rates: dict[tuple[str, str, str], SignalBaseRate] = {}
    for key, buckets in conditional.items():
        rates[key] = SignalBaseRate(
            conditional=tuple(_summarize(horizon, buckets.get(horizon, [])) for horizon in horizon_list),
            unconditional=baseline.get(key[0], ()),
        )

    logger.info(
        "Computed base rates for %s signal groups across %s symbols as of %s",
        len(rates),
        len(baseline),
        as_of_date,
    )
    return rates
