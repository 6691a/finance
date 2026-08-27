"""후보 조립 — 프롬프트에 실을 것을 코드가 먼저 좁힌다.

주당 평가 문서가 1,000건을 넘어(2026-08-27 실측) 전부 실을 수 없다. 여기서 50건 안팎으로
좁히고, 모자라면 모델이 툴로 더 판다(설계 §5.1·§5.2).

**LangChain을 import하지 않는다.** 이 모듈이 아는 것은 연결과 SQL뿐이다.
"""

from modules.causal.domain import (
    INDEX_TARGETS,
    INDICATOR_TARGETS,
    MACRO_TARGETS,
    CausalTarget,
    CausalTargetKind,
)
from modules.db import Connection
from modules.sql import read_sql

WATCHED_STOCKS = read_sql("postgres", "causal", "select_watched_stocks.sql")


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
