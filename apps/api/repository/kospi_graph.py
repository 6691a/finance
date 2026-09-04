"""요인 관계와 메모를 **Neo4j에서** 읽는다.

쓰기 쪽은 `airflow/modules/kospi/graph.py`이고 두 트리는 서로를 import하지 않는다.
그래서 **Cypher가 여기에 한 벌 더 있다.** 노드·엣지 이름이 어긋나면 화면이 조용히 0건이
되므로 `tests/api/test_kospi_graph.py`가 두 문장의 라벨과 속성 이름을 대조한다.

## 가드는 그대로다

community 판은 사용자가 `neo4j` 하나뿐이고 역할 기반 권한이 Enterprise 전용이라 읽기 전용
계정을 줄 수 없다. 그래서 `ensure_read_only()`가 문장 단위로 막는다 — 옛 인과 그래프
리포지토리가 쓰던 것을 그대로 쓴다.

## 여기서 접지 않는다

관측을 그대로 올리고 **가중치는 서비스가 접는다.** 감쇠 식을 Cypher에 넣으면 DB 없이
테스트할 수 없고, 같은 판단을 Airflow 쪽 `domain.relation_weight`가 이미 하고 있어 두
구현이 같은 값을 내는지 대조할 수 있어야 한다.
"""

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict

from apps.api.repository.graph import Driver, as_date, ensure_read_only

# 관계를 훑는 범위(달력일). 반감기 5일이면 90일 전 관측의 무게가 2^-18이라 값에 영향이
# 없고, 상한을 두는 이유는 그래프가 자란 뒤 한 번의 조회가 전 이력을 훑지 않게 하려는
# 것이다. **`airflow/modules/kospi/domain.RELATION_LOOKBACK_DAYS`와 같은 값이어야 한다.**
RELATION_LOOKBACK_DAYS = 90

# 지수 노드의 코드. 대상이 코스피 하나다.
INDEX_CODE = "KOSPI"

# 관측 전부. **접지 않고 그대로 올린다.**
OBSERVATIONS = """
MATCH (f:Factor)-[o:OBSERVED]->(:Index {code: $index})
WHERE o.date >= $window_start AND o.date <= $as_of_date
RETURN f.code AS code, o.date AS date, o.sign AS sign, o.strength AS strength,
       coalesce(o.note, '') AS note, o.llm_run_id AS llm_run_id
ORDER BY date DESC, code
"""

# 메모 전부. `state` 필터는 파이썬이 건다 — Cypher 분기를 늘리면 문장이 둘이 된다.
MEMORIES = """
MATCH (m:Memory)
OPTIONAL MATCH (m)-[:ABOUT]->(f:Factor)
RETURN m.id AS id, m.created_on AS created_on, m.text AS text,
       coalesce(m.verify_count, 0) AS verify_count,
       coalesce(m.unreviewed_count, 0) AS unreviewed_count,
       m.last_verified_on AS last_verified_on,
       m.retired_on AS retired_on, m.retire_reason AS retire_reason,
       m.llm_run_id AS llm_run_id, f.code AS factor
ORDER BY m.created_on DESC, m.id DESC
"""


class ObservationRow(BaseModel):
    """관측 엣지 하나를 읽은 것."""

    model_config = ConfigDict(frozen=True)

    factor: str
    observed_on: date
    sign: str
    strength: int
    note: str = ""
    llm_run_id: int | None = None


class MemoryRow(BaseModel):
    """메모 노드 하나."""

    model_config = ConfigDict(frozen=True)

    id: int
    created_on: date
    text: str
    factor: str | None = None
    verify_count: int = 0
    unreviewed_count: int = 0
    last_verified_on: date | None = None
    retired_on: date | None = None
    retire_reason: str | None = None
    llm_run_id: int | None = None


def as_int(value: Any) -> int | None:
    """Neo4j 정수를 파이썬 정수로. **없으면 None이다** — 0으로 채우면 "안 남겼다"가 사라진다."""
    return None if value is None else int(value)


class KospiGraphReadRepository:
    """관계와 메모를 읽는다. 쓰기 경로는 없다."""

    def __init__(self, driver: Driver | None) -> None:
        # **드라이버가 없을 수 있다.** `NEO4J_URI`를 안 준 실행에서는 관계 화면만 꺼지고
        # 나머지는 그대로 돈다. 그 판단은 서비스가 한다.
        self._driver = driver

    @property
    def enabled(self) -> bool:
        return self._driver is not None

    def _read(self, cypher: str, **parameters: Any) -> list[dict[str, Any]]:
        if self._driver is None:
            raise RuntimeError("neo4j driver is not configured")
        with self._driver.session() as session:
            return [dict(record) for record in session.run(ensure_read_only(cypher), **parameters)]

    def observations(
        self, *, as_of_date: date, lookback_days: int = RELATION_LOOKBACK_DAYS
    ) -> tuple[ObservationRow, ...]:
        """창 안의 관측 전부. **`date`를 그대로 넘긴다** — 투영이 Neo4j `Date`로 저장해서
        ISO 문자열로 비교하면 아무 것도 안 걸린다(2026-08-30에 그렇게 0건이 나왔다)."""
        window_start = date.fromordinal(as_of_date.toordinal() - lookback_days)
        rows = self._read(
            OBSERVATIONS, index=INDEX_CODE, as_of_date=as_of_date, window_start=window_start
        )
        found: list[ObservationRow] = []
        for row in rows:
            observed_on = as_date(row.get("date"))
            if observed_on is None:
                # 날짜 없는 엣지는 무게를 못 준다. 조용히 0으로 치지 않고 뺀다.
                continue
            found.append(
                ObservationRow(
                    factor=str(row.get("code", "")),
                    observed_on=observed_on,
                    sign=str(row.get("sign", "")),
                    strength=int(row.get("strength") or 0),
                    note=str(row.get("note") or ""),
                    llm_run_id=as_int(row.get("llm_run_id")),
                )
            )
        return tuple(found)

    def memories(self) -> tuple[MemoryRow, ...]:
        """메모 전부(활성과 내린 것). 가르는 것은 서비스가 한다."""
        return tuple(
            MemoryRow(
                id=int(row["id"]),
                created_on=as_date(row.get("created_on")) or date.min,
                text=str(row.get("text") or ""),
                factor=None if row.get("factor") is None else str(row["factor"]),
                verify_count=int(row.get("verify_count") or 0),
                unreviewed_count=int(row.get("unreviewed_count") or 0),
                last_verified_on=as_date(row.get("last_verified_on")),
                retired_on=as_date(row.get("retired_on")),
                retire_reason=None if row.get("retire_reason") is None else str(row["retire_reason"]),
                llm_run_id=as_int(row.get("llm_run_id")),
            )
            for row in self._read(MEMORIES)
            if row.get("id") is not None
        )

