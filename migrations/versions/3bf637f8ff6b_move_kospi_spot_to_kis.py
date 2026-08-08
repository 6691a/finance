"""move kospi spot to kis

Revision ID: 3bf637f8ff6b
Revises: 2495c7a1c877
Create Date: 2026-08-08 17:50:38.360844

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3bf637f8ff6b"
down_revision: str | Sequence[str] | None = "2495c7a1c877"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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
    """코스피 현물의 제공처를 yahoo 에서 kis 로 옮긴다.

    Yahoo 의 `^KS11` 분봉은 일중 변동이 5~10%로 나오는 날이 있어 신뢰할 수 없었다. 재요청해도
    같은 값이 오므로 제공처가 주는 값 자체가 그렇다. KIS 는 국내 지수를 무료로 주고 공식
    API 다. **국내에서 받을 수 있는 것은 국내를 우선한다.**

    이미 쌓인 yahoo 코스피 봉은 지운다. 마스터가 kis 로 옮겨가면 그 행들은 참조하는 마스터가
    없어 대시보드에서 사라지고, 품질이 낮아 남겨 둘 값어치도 없다. 다른 심볼은 건드리지 않는다.
    """
    op.execute(
        sa.text(
            "UPDATE quote_symbol SET provider = 'kis', updated_at = now() WHERE provider = 'yahoo' AND symbol = 'KOSPI'"
        )
    )
    op.execute(sa.text("DELETE FROM quote_bar WHERE provider = 'yahoo' AND symbol = 'KOSPI'"))


def downgrade_default() -> None:
    # 지운 봉은 되돌리지 않는다. 다시 받으려면 Yahoo 를 다시 수집해야 한다.
    op.execute(
        sa.text(
            "UPDATE quote_symbol SET provider = 'yahoo', updated_at = now() WHERE provider = 'kis' AND symbol = 'KOSPI'"
        )
    )


def upgrade_market_migration() -> None:
    pass


def downgrade_market_migration() -> None:
    pass
