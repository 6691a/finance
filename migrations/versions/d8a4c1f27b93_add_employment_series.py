"""add employment series

Revision ID: d8a4c1f27b93
Revises: e5b2d7a41c93
Create Date: 2026-08-18 21:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8a4c1f27b93"
down_revision: str | Sequence[str] | None = "e5b2d7a41c93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 미국 고용지표 둘. 리비전에서 앱 코드를 import하지 않는다. import하면 나중에 수집기 Enum이
# 바뀔 때 과거 리비전의 결과가 따라 바뀐다. 대조는 tests/migrations가 한다.
#
# 실업률은 만기 개념이 없어 maturity_months가 NULL이다. FRED 좌표는 UNRATE·PAYEMS이고
# 단위(Percent, Thousands of Persons)는 2026-08-18에 `series` 엔드포인트로 확인했다.
# 단위는 마스터가 아니라 관측값 행과 수집기 Enum이 든다.
#
# (provider, series_id, country, country_name, maturity_months, kind, label)
EMPLOYMENT_SERIES_SEED: tuple[tuple[str, str, str, str, int | None, str, str], ...] = (
    ("fred", "UNEMPLOYMENT_M", "US", "미국", None, "activity", "미국 실업률"),
    ("fred", "NONFARM_PAYROLL_M", "US", "미국", None, "activity", "미국 비농업고용"),
)


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
    op.bulk_insert(
        sa.table(
            "indicator_series",
            sa.column("provider", sa.Text),
            sa.column("series_id", sa.Text),
            sa.column("country", sa.Text),
            sa.column("country_name", sa.Text),
            sa.column("maturity_months", sa.Integer),
            sa.column("kind", sa.String),
            sa.column("label", sa.Text),
        ),
        [
            dict(zip(("provider", "series_id", "country", "country_name", "maturity_months", "kind", "label"), row))
            for row in EMPLOYMENT_SERIES_SEED
        ],
        # offline(`--sql`)에서는 executemany를 찍을 수 없다. 행마다 INSERT를 내게 한다.
        multiinsert=False,
    )


def downgrade_default() -> None:
    tickers = ", ".join(f"'{row[1]}'" for row in EMPLOYMENT_SERIES_SEED)
    op.execute(f"DELETE FROM indicator_series WHERE provider = 'fred' AND series_id IN ({tickers})")


def upgrade_finance() -> None:
    pass


def downgrade_finance() -> None:
    pass
