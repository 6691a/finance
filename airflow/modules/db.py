"""PEP 249 연결과 커서의 구조적 타입. 스무 모듈이 각자 베끼던 것을 한 벌로 모은 것이다.

**왜 Protocol인가.** Airflow가 주는 연결은 provider 버전에 따라 psycopg2/psycopg3 래퍼로
갈리고 테스트는 가짜 객체를 넣는다. 어느 쪽이든 PEP 249 모양이라 구조적 타입이면 충분하고,
구체 클래스를 import하면 수집기 테스트가 배포 환경 없이 돌지 않는다.

**왜 한 벌인가.** 전에는 모듈마다 자기가 쓰는 메서드만 적은 `Cursor`가 있었고 스무 개가
조금씩 달랐다(`__exit__` 반환형, `parameters` 타입, `executemany` 유무). 새 모듈은 가까운
파일에서 복사해 왔으므로 그 차이는 의도가 아니라 사고였다. 여기 하나만 두면 다음 복사가
없다.

`Cursor`는 **여섯 메서드를 다 요구한다.** 실제 psycopg 커서가 전부 갖고 있고, 테스트
가짜도 이 한 벌에 맞추면 어느 모듈에 넣어도 통과한다. 모듈마다 좁히면 다시 스무 개가 된다.

`Connection`은 `cursor()`만 요구한다. 커밋 경계는 대부분 DAG이 `modules/utility.atomic`으로
쥐고 있어 수집기·조회 코드가 볼 일이 없다. 스스로 커밋하는 코드는
`TransactionalConnection`을 쓴다.
"""

from collections.abc import Sequence
from typing import Any, Protocol, Self


class Cursor(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(self, *args: object) -> object: ...

    def execute(self, statement: str, parameters: Any = ()) -> object: ...

    def executemany(self, statement: str, parameters: Sequence[Sequence[Any]]) -> object: ...

    def fetchone(self) -> Any: ...

    def fetchall(self) -> Any: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...


class TransactionalConnection(Connection, Protocol):
    """스스로 커밋·롤백하는 코드가 받는 연결.

    `thesis.ThesisStore`와 `dedup.link_duplicates`가 그렇다 — 항목 하나가 트랜잭션 하나라
    앞의 성공을 뒤의 실패가 되돌리지 않게 중간에 커밋한다.
    """

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
