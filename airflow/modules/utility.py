from collections.abc import Iterator
from contextlib import contextmanager
from os import environ
from typing import Any

from pendulum import timezone

KST_TIMEZONE = timezone("Asia/Seoul")

CONNECTION_ID = "finance"

AIRFLOW_HOME = environ.get("AIRFLOW_HOME")

# 설정 오류라 재시도해도 같은 결과인 HTTP 상태.
UNRECOVERABLE_STATUSES = frozenset({400, 401, 403, 404})

# KIS는 401이 토큰 만료일 수 있어 재발급 후 다시 시도하므로 즉시 실패 대상에서 뺀다.
KIS_UNRECOVERABLE_STATUSES = frozenset({400, 403, 404})


@contextmanager
def atomic(connection: Any) -> Iterator[Any]:
    """성공하면 commit, 예외면 rollback 후 그대로 다시 올린다.

    close는 하지 않는다. 연결 하나로 항목별 커밋을 도는 DAG가 있어 연결 수명은
    호출자가 `contextlib.closing`으로 관리한다. 연결 타입은 provider 버전에 따라
    psycopg2/psycopg3 래퍼로 갈리지만 어느 쪽이든 PEP 249 연결이라
    commit·rollback을 갖는다.
    """
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
