"""후보 조립 — 프롬프트에 실을 것을 코드가 먼저 좁힌다.

주당 평가 문서가 1,000건을 넘어(2026-08-27 실측) 전부 실을 수 없다. 여기서 50건 안팎으로
좁히고, 모자라면 모델이 툴로 더 판다(설계 §5.1·§5.2).

**LangChain을 import하지 않는다.** 이 모듈이 아는 것은 연결과 SQL뿐이다.
"""

from collections.abc import Iterable, Sequence
from datetime import timedelta

from modules.causal.domain import (
    INDEX_TARGETS,
    INDICATOR_TARGETS,
    MACRO_TARGETS,
    CausalReturnUnit,
    CausalTarget,
    CausalTargetKind,
    CausalWindow,
    TargetReturns,
)
from modules.db import Connection
from modules.sql import read_sql

WATCHED_STOCKS = read_sql("postgres", "causal", "select_watched_stocks.sql")

# 대상 종류마다 원본 테이블이 다르다. 한 쿼리로 묶지 않는 이유는 각 SQL 머리에 있다.
RETURNS_SQL: dict[CausalTargetKind, str] = {
    CausalTargetKind.INDEX: read_sql("postgres", "causal", "select_index_returns.sql"),
    CausalTargetKind.INSTRUMENT: read_sql("postgres", "causal", "select_stock_returns.sql"),
    CausalTargetKind.QUOTE: read_sql("postgres", "causal", "select_quote_returns.sql"),
    CausalTargetKind.INDICATOR: read_sql("postgres", "causal", "select_indicator_returns.sql"),
}

# 실현 등락은 금리만 bp다. 나머지는 종가 대비 변화율이다.
RETURN_UNITS: dict[CausalTargetKind, CausalReturnUnit] = {
    CausalTargetKind.INDEX: CausalReturnUnit.PERCENT,
    CausalTargetKind.INSTRUMENT: CausalReturnUnit.PERCENT,
    CausalTargetKind.QUOTE: CausalReturnUnit.PERCENT,
    CausalTargetKind.INDICATOR: CausalReturnUnit.BASIS_POINT,
}

# 실현 등락 조회의 끝. 반응 주 금요일이 아니라 넉넉히 잡아야 휴장이 겹쳐도 T+5가 잡힌다.
RETURNS_SCAN_DAYS = 15


def resolve_targets(connection: Connection) -> tuple[CausalTarget, ...]:
    """이 실행이 다룰 대상. 지수 둘 → 관심종목 → 매크로 다섯 → 금리 둘 순서다.

    **종목만 마스터에서 읽는다.** 관심종목을 늘리면 대상이 따라 늘고, 그만큼 후보와 비용도
    는다. 지수·매크로는 늘어나는 목록이 아니라 상수다(설계 §0).
    """
    with connection.cursor() as cursor:
        cursor.execute(WATCHED_STOCKS)
        stocks = tuple(
            CausalTarget(kind=CausalTargetKind.INSTRUMENT, code=row[0])
            for row in cursor.fetchall()
        )
    return INDEX_TARGETS + stocks + MACRO_TARGETS + INDICATOR_TARGETS


def fetch_returns(
    connection: Connection,
    targets: Iterable[CausalTarget],
    window: CausalWindow,
) -> dict[str, TargetReturns]:
    """대상별 실현 등락. **값이 온전한 대상만 담는다.**

    셋 중 하나라도 없으면 그 대상을 빼는 이유는 설계 §6이다 — NULL로 저장하면 "안 쟀다"와
    "잴 수 없었다"가 나중에 구분되지 않는다. 반응 주가 아직 안 끝났거나 그 계열의 수집이
    늦게 시작된 주가 그렇고, 둘 다 정상 흐름이라 실패로 만들지 않는다.
    """
    by_kind: dict[CausalTargetKind, list[str]] = {}
    providers: dict[CausalTargetKind, set[str]] = {}
    for target in targets:
        by_kind.setdefault(target.kind, []).append(target.code)
        if target.provider is not None:
            providers.setdefault(target.kind, set()).add(target.provider)

    returns: dict[str, TargetReturns] = {}
    for kind, codes in by_kind.items():
        for provider in sorted(providers.get(kind, {""})):
            returns |= _fetch_kind(connection, kind, codes, provider, window)
    return returns


def _fetch_kind(
    connection: Connection,
    kind: CausalTargetKind,
    codes: Sequence[str],
    provider: str,
    window: CausalWindow,
) -> dict[str, TargetReturns]:
    parameters = {
        "codes": list(codes),
        "week_start": window.week_start,
        "week_end": window.week_end,
        "scan_end": window.week_end + timedelta(days=RETURNS_SCAN_DAYS),
    }
    if provider:
        parameters["provider"] = provider
    with connection.cursor() as cursor:
        cursor.execute(RETURNS_SQL[kind], parameters)
        rows = cursor.fetchall()
    unit = RETURN_UNITS[kind]
    return {
        row[0]: TargetReturns(week=row[1], t1=row[2], t5=row[3], unit=unit)
        for row in rows
        if row[1] is not None and row[2] is not None and row[3] is not None
    }
