"""그래프 저장소(Neo4j)에 붙는 공통 조각. **리소스 하나에만 쓰이는 것은 여기 두지 않는다.**

드라이버 프로토콜, 읽기 전용 가드, `neo4j.time.Date` 변환 셋이다. 지금 쓰는 쪽은
`kospi_graph.py` 하나지만 그 셋은 리소스가 아니라 **저장소의 성질**이라 여기 있다.

## 가드가 코드에 있는 이유

Neo4j community 판은 사용자가 `neo4j` 하나뿐이고 역할 기반 권한이 Enterprise 전용이라
**읽기 전용 계정을 줄 수 없다.** 그래서 문장 단위로 막는다.

쓰기 쪽은 `airflow/modules/kospi/graph.py`이고 두 트리는 서로를 import하지 않는다.
"""

import re
from datetime import date
from typing import Any, Protocol, Self

# 읽기 전용이 아닌 낱말. **거절 목록이 아니라 통과 검사의 보조다** — 문장이 `MATCH`로
# 시작해 `RETURN`을 갖는지를 먼저 보고, 그 안에 이 낱말이 있으면 거절한다.
#
# **낱말 경계로 본다.** 부분 문자열로 보면 속성 이름이 걸린다 — `m.created_on`이 `CREATE`로,
# `OFFSET`이 `SET`으로 읽혔다(2026-09-03에 메모 조회가 그렇게 막혔다).
FORBIDDEN = (
    "CREATE",
    "MERGE",
    "DELETE",
    "SET",
    "REMOVE",
    "DROP",
    "LOAD CSV",
    "CALL",
)

FORBIDDEN_PATTERN = re.compile(
    "|".join(rf"\b{re.escape(word)}\b" for word in FORBIDDEN), re.IGNORECASE
)


class UnsafeCypher(RuntimeError):
    """읽기 전용이 아닌 문장. **삼키지 않는다** — 빈 결과로 바꾸면 "그런 행이 없다"와
    "우리가 막았다"를 부르는 쪽이 구별하지 못한다."""


def ensure_read_only(cypher: str) -> str:
    """`MATCH`로 시작하고 `RETURN`이 있는 문장만 통과시킨다.

    **통과 검사가 먼저이고 거절 목록은 보조다.** 거절 목록만 두면 새 쓰기 문법이 생길 때
    조용히 통과한다 — 통과 조건을 좁게 잡는 편이 낫다.
    """
    text = " ".join(cypher.split())
    upper = text.upper()
    if not upper.startswith("MATCH ") or " RETURN " not in f" {upper} ":
        raise UnsafeCypher(f"read-only queries must MATCH ... RETURN: {text[:80]}")
    found = FORBIDDEN_PATTERN.search(upper)
    if found:
        raise UnsafeCypher(f"{found.group()} is not allowed in a read query: {text[:80]}")
    return text


class Session(Protocol):
    """Neo4j 세션 중 우리가 쓰는 것만. 드라이버를 가짜로 바꿔 끼우려고 좁게 잡는다."""

    def run(self, query: str, **parameters: Any) -> Any: ...

    def __enter__(self) -> Self: ...

    def __exit__(self, *args: object) -> None: ...


class Driver(Protocol):
    def session(self) -> Session: ...


def as_date(value: Any) -> date | None:
    """Neo4j `Date`를 파이썬 `date`로. **드라이버 타입을 응답까지 들이지 않는다.**

    `neo4j.time.Date`는 `date`의 하위형이 아니라서 Pydantic이 그대로는 못 받는다.
    """
    if value is None:
        return None
    if isinstance(value, date):
        return value
    native = getattr(value, "to_native", None)
    return native() if callable(native) else None
