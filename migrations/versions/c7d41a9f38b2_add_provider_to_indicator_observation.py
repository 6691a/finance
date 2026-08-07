"""add provider to indicator observation

Revision ID: c7d41a9f38b2
Revises: e9640ef65120
Create Date: 2026-08-07 10:12:44.318206

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7d41a9f38b2"
down_revision: str | Sequence[str] | None = "e9640ef65120"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROVIDER_COMMENT = "데이터 제공처 식별자(예: fred 또는 ecos). 같은 수집의 source_record.source와 같은 값이다"
SERIES_ID_COMMENT = "제공처가 정의한 시계열 식별자(예: DGS10). 제공처 안에서만 고유하다"
TABLE_COMMENT = "여러 제공처의 지표 관측값을 조회 가능한 형태로 정규화하고 원본과 연결하는 테이블"

OLD_SERIES_ID_COMMENT = "공급자가 정의한 시계열 식별자(예: DGS10)"
OLD_TABLE_COMMENT = "FRED 지표 관측값을 조회 가능한 형태로 정규화하고 원본과 연결하는 테이블"

NATURAL_KEY = "uq_indicator_observation_natural_key"


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


def upgrade_default() -> None:
    # 손으로 쓴 리비전이다. autogenerate는 컬럼을 NOT NULL로 바로 붙이는데, 기존 행에 채울 값이
    # 없어 실패한다. nullable로 붙이고 값을 채운 뒤 조인다.
    op.add_column(
        "indicator_observation",
        sa.Column("provider", sa.Text(), nullable=True, comment=PROVIDER_COMMENT),
    )
    # 이 컬럼이 생기기 전의 행은 전부 FRED 수집분이다.
    op.execute("UPDATE indicator_observation SET provider = 'fred' WHERE provider IS NULL")
    op.alter_column("indicator_observation", "provider", existing_type=sa.Text(), nullable=False)

    # series_id는 제공처 안에서만 고유하다. 제공처가 둘 이상이 되면 예전 키로는 서로 다른
    # 시계열이 같은 행을 덮어쓸 수 있다.
    op.drop_constraint(NATURAL_KEY, "indicator_observation", type_="unique")
    op.create_unique_constraint(
        NATURAL_KEY,
        "indicator_observation",
        ["provider", "series_id", "observation_date"],
    )

    op.alter_column(
        "indicator_observation",
        "series_id",
        existing_type=sa.TEXT(),
        comment=SERIES_ID_COMMENT,
        existing_comment=OLD_SERIES_ID_COMMENT,
        existing_nullable=False,
    )
    op.create_table_comment(
        "indicator_observation",
        TABLE_COMMENT,
        existing_comment=OLD_TABLE_COMMENT,
        schema=None,
    )


def downgrade_default() -> None:
    op.create_table_comment(
        "indicator_observation",
        OLD_TABLE_COMMENT,
        existing_comment=TABLE_COMMENT,
        schema=None,
    )
    op.alter_column(
        "indicator_observation",
        "series_id",
        existing_type=sa.TEXT(),
        comment=OLD_SERIES_ID_COMMENT,
        existing_comment=SERIES_ID_COMMENT,
        existing_nullable=False,
    )

    # 예전 키는 제공처를 구분하지 못한다. FRED가 아닌 제공처의 행을 남겨 두면 (series_id,
    # observation_date)가 중복돼 제약을 다시 걸 수 없으므로 여기서 지운다. 되돌리면 그
    # 데이터는 사라진다.
    op.execute("DELETE FROM indicator_observation WHERE provider <> 'fred'")

    op.drop_constraint(NATURAL_KEY, "indicator_observation", type_="unique")
    op.create_unique_constraint(NATURAL_KEY, "indicator_observation", ["series_id", "observation_date"])
    op.drop_column("indicator_observation", "provider")
