"""upsert 여러 건을 한 번에 보낸다.

`hana.py`가 같은 문제를 먼저 풀고 측정치를 남겼다. psycopg2의 `executemany`는 내부적으로
행마다 왕복해서 직접 반복문을 도는 것과 같다(측정: 1475행에 0.64s vs 0.66s). 같은 드라이버의
`execute_batch`는 문장을 묶어 보내 0.18s다. **로컬에서는 차이가 초 단위지만 DB가 원격이면
왕복 지연이 행 수만큼 곱해진다.**

장중 수집은 폴링마다 수백 행을 쓰고 백필은 한 번에 수만 행을 쓴다. `hana.py`가 자기 안에
두고 쓰던 것을 여기로 꺼내 `yahoo.py`와 `kis.py`가 함께 쓴다.
"""

from collections.abc import Sequence
from typing import Any, Protocol

try:
    # psycopg2 전용 고속 경로. 이 모듈의 필수 의존성은 아니라서 없으면 `None`으로 두고
    # PEP 249 표준 `executemany`로 물러선다.
    from psycopg2.extensions import cursor as _Psycopg2Cursor
    from psycopg2.extras import execute_batch as _execute_batch
except ImportError:  # pragma: no cover - Airflow 이미지에는 psycopg2가 항상 있다
    _Psycopg2Cursor = None
    _execute_batch = None

# 한 번에 묶어 보낼 문장 수. `hana.py`와 같은 값이다.
UPSERT_PAGE_SIZE = 500


class BatchCursor(Protocol):
    def execute(self, statement: str, parameters: Sequence[Any]) -> object: ...

    def executemany(self, statement: str, parameters: Sequence[Sequence[Any]]) -> object: ...


def execute_upserts(cursor: BatchCursor, statement: str, parameters: Sequence[Sequence[Any]]) -> None:
    """같은 문장을 여러 파라미터로 실행한다. 빈 목록이면 아무 것도 하지 않는다.

    `execute_batch`가 없으면 PEP 249 표준 `executemany`로 물러선다. psycopg3의
    `executemany`는 자체적으로 파이프라이닝을 하므로 그쪽에서는 물러서도 느리지 않다.

    **판정 기준은 import 가능 여부가 아니라 커서의 드라이버다.** 한 이미지에 psycopg2와
    psycopg3이 함께 있고 provider가 psycopg3 연결을 주면, import는 성공하는데
    `execute_batch`가 psycopg3 커서를 받아 `mogrify`를 찾다 죽는다.
    """
    if not parameters:
        return
    if _execute_batch is None or not isinstance(cursor, _Psycopg2Cursor):
        cursor.executemany(statement, parameters)
        return
    _execute_batch(cursor, statement, parameters, page_size=UPSERT_PAGE_SIZE)
