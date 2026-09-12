"""상태 없는 공통 잎. 수집기·브리핑이 같은 답을 내야 하는 상수와 변환을 한 벌만 둔다.

`config`·`database`·Airflow를 import하지 않는다 — 어디서 불러도 배포 환경을 요구하지 않는다.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from os import environ
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from pendulum import timezone

# pendulum 시간대. DAG의 `start_date`처럼 pendulum 메서드를 쓰는 자리가 받는다.
KST_TIMEZONE = timezone("Asia/Seoul")
# 표준 라이브러리 시간대. `datetime.astimezone`·`tzinfo=`처럼 pendulum이 필요 없는 자리가 받는다.
KST = ZoneInfo("Asia/Seoul")

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


def normalize_to_utc(moment: datetime) -> datetime:
    """저장·비교용 시각을 UTC로 정규화한다. Pydantic 모델의 시각 validator가 부른다.

    naive datetime은 `AwareDatetime`이 이미 막으므로 여기서는 시간대만 바꾼다.
    """
    return moment.astimezone(UTC)


def require_finite(value: Decimal, subject: str) -> Decimal:
    """`Decimal`이 유한한 수인지 본다. Pydantic 모델의 값 validator가 부른다.

    Decimal은 "NaN"과 "Infinity"도 받아들인다. 그대로 저장하면 이후 집계가 전부 오염된다.
    `subject`는 오류 문장의 주어다(`observation value`·`quote value`).
    """
    if not value.is_finite():
        raise ValueError(f"{subject} must be a finite number")
    return value


class ObservationPeriod(Protocol):
    """조회 구간을 갖는 요청 모델의 모양. `require_ordered_period`가 받는다."""

    @property
    def observation_start(self) -> date: ...

    @property
    def observation_end(self) -> date: ...


def require_ordered_period[P: ObservationPeriod](request: P) -> P:
    """조회 구간의 시작이 끝보다 뒤가 아닌지 본다. 요청 모델의 `model_validator`가 부른다."""
    if request.observation_start > request.observation_end:
        raise ValueError("observation_start must not be after observation_end")
    return request
