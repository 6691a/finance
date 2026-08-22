"""widen investor flow market codes

Revision ID: b7d21e4a9f38
Revises: c2d9e4f1a7b3
Create Date: 2026-08-22 15:00:00.000000

시장별 투자자 매매동향(`market_investor_flow_snapshot`)을 코스피·코스닥 둘에서 선물·콜옵션·
풋옵션·주식선물·ETF까지 일곱으로 넓힌다.

같은 조회(`FHPTJ04030000`)가 일곱 시장에 같은 12개 투자자 분류를 같은 필드 이름으로 준다.
그래서 컬럼은 하나도 바뀌지 않고 `market_code`가 받는 값 집합만 넓어진다. 조회 코드는 공식
postman 컬렉션의 파라미터 설명에 있고 근거는
`airflow/modules/collectors/market/kis_investor_flow.py`의 "시장 코드는 문서에 다 있다" 절에 있다.

컬럼 타입은 그대로다. `Enum(native_enum=False)`는 PostgreSQL native enum이 아니라
`VARCHAR(20)`이고, 가장 긴 값 `STOCK_FUTURES`가 13자라 길이도 그대로다. 바뀌는 것은 명시
CHECK 제약 하나뿐이다.

기존 행은 `KOSPI`·`KOSDAQ`뿐이라 넓히는 방향의 제약 교체는 검증에 걸리지 않는다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7d21e4a9f38"
down_revision: str | Sequence[str] | None = "c2d9e4f1a7b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_market_investor_flow_snapshot_market_code"
TABLE = "market_investor_flow_snapshot"

OLD_VALUES = "'KOSPI', 'KOSDAQ'"
NEW_VALUES = "'KOSPI', 'KOSDAQ', 'FUTURES', 'CALL_OPTION', 'PUT_OPTION', 'STOCK_FUTURES', 'ETF'"

NEW_COMMENT = (
    "시장 구분(KOSPI, KOSDAQ, FUTURES, CALL_OPTION, PUT_OPTION, STOCK_FUTURES, ETF). "
    "현물과 파생이 한 테이블에 섞여 있으므로 조회하는 쪽은 이 칸을 반드시 건다"
)
OLD_COMMENT = "시장 구분(KOSPI, KOSDAQ). 코스닥 조회 코드는 아직 확인하지 못해 KOSPI만 채워진다"


def upgrade(engine_name: str) -> None:
    _run(f"upgrade_{engine_name}")


def downgrade(engine_name: str) -> None:
    _run(f"downgrade_{engine_name}")


def _run(name: str) -> None:
    # A revision written before an alias existed has no section for it, and
    # there is nothing for that alias to do. Adding an alias must not force a
    # no-op edit to every past revision.
    operations = globals().get(name)
    if operations is not None:
        operations()


def _swap_check(values: str) -> None:
    op.drop_constraint(CONSTRAINT, TABLE, type_="check")
    op.create_check_constraint(CONSTRAINT, TABLE, f"market_code IN ({values})")


def upgrade_default() -> None:
    _swap_check(NEW_VALUES)
    op.alter_column(TABLE, "market_code", existing_type=sa.String(length=20), comment=NEW_COMMENT)


def downgrade_default() -> None:
    # 파생·ETF 행이 있으면 좁히는 제약이 검증에 걸려 실패한다. 그것이 옳다 — 조용히 지우는
    # 것보다 멈추는 편이 낫다. 되돌리려면 먼저 그 행들을 지운다.
    op.alter_column(TABLE, "market_code", existing_type=sa.String(length=20), comment=OLD_COMMENT)
    _swap_check(OLD_VALUES)
