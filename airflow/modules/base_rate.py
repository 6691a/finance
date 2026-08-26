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
클래스가 아니라 함수다(`technical_signals.py`·`market_session.py`와 같은 형태).
"""

import logging
from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from modules.db import Connection
from modules.sql import read_sql
from modules.technical import RULE_VERSION
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

    unconditional: dict[str, dict[int, list[Decimal]]] = defaultdict(lambda: defaultdict(list))
    with connection.cursor() as cursor:
        cursor.execute(
            UNCONDITIONAL_RETURNS,
            {"as_of_date": as_of_date, "horizons": horizon_list, "symbols": symbol_list},
        )
        for symbol, horizon_days, return_pct in cursor.fetchall():
            unconditional[str(symbol)][int(horizon_days)].append(return_pct)

    baseline = {
        symbol: tuple(_summarize(horizon, buckets.get(horizon, [])) for horizon in horizon_list)
        for symbol, buckets in unconditional.items()
    }

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
