"""모델이 공유하는 컬럼 형태.

`analysis` 안의 세 aggregate가 전부 쓰지만 어느 하나에 두면 나머지가 그것을 import하게 돼
없는 의존이 생긴다. 그래서 따로 둔다.
"""

from enum import StrEnum

from sqlalchemy import Enum as SqlEnum


def _enum_column(enum: type[StrEnum]) -> SqlEnum:
    """`StrEnum`을 VARCHAR + CHECK로 내리는 공통 형태.

    PostgreSQL native enum은 값 추가·삭제 마이그레이션 비용이 커서 쓰지 않는다(프로젝트 규칙).
    """
    return SqlEnum(
        enum,
        native_enum=False,
        length=20,
        values_callable=lambda members: [member.value for member in members],
    )
