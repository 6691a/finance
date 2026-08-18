"""add document direction check

Revision ID: b6de981cb250
Revises: fb3a83fe70f6
Create Date: 2026-08-18 12:41:22.099872

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b6de981cb250"
down_revision: str | Sequence[str] | None = "fb3a83fe70f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DIRECTION_CHECK = "ck_document_direction"


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
    # autogenerate는 CHECK 제약을 비교하지 않는다. 같은 테이블의 `document_type`,
    # `content_level`은 이미 걸려 있고 `direction`만 빠져 있었다.
    # `direction`은 평가 전이면 NULL이다. SQL에서 NULL은 CHECK를 통과한다.
    op.create_check_constraint(
        DIRECTION_CHECK,
        "document",
        "direction IN ('positive', 'negative', 'neutral')",
    )


def downgrade_default() -> None:
    op.drop_constraint(DIRECTION_CHECK, "document", type_="check")
