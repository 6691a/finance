"""seed japan jgb series

Revision ID: d84f1b06c9a7
Revises: a2e57b3c8f41
Create Date: 2026-08-07 19:12:04.881207

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d84f1b06c9a7"
down_revision: str | Sequence[str] | None = "a2e57b3c8f41"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 일본 재무성이 고시하는 국채 금리 시계열. 재무성 CSV는 1~40년 열 열다섯 개를 주지만 실제로
# 입찰 발행되는 연한만 넣는다. 나머지는 발행 종목이 없는 곡선 위의 값이라 시장이 인용하지 않는다.
#
# 이 목록은 이 리비전이 만들어진 시점의 값으로 고정한다. 시계열을 늘리면 수집기 Enum
# (`modules.collectors.mof.JgbSeries`)과 함께 새 리비전에서 INSERT를 추가한다. 여기서 앱 코드를
# import해 오면 나중에 코드가 바뀔 때 과거 리비전의 결과가 따라 바뀐다.
#
# (provider, series_id, country, country_name, maturity_months, kind, label)
SEED: tuple[tuple[str, str, str, str, int, str, str], ...] = (
    ("mof", "JGB2Y", "JP", "일본", 24, "government_bond", "일본 2년물"),
    ("mof", "JGB5Y", "JP", "일본", 60, "government_bond", "일본 5년물"),
    ("mof", "JGB10Y", "JP", "일본", 120, "government_bond", "일본 10년물"),
    ("mof", "JGB20Y", "JP", "일본", 240, "government_bond", "일본 20년물"),
    ("mof", "JGB30Y", "JP", "일본", 360, "government_bond", "일본 30년물"),
    ("mof", "JGB40Y", "JP", "일본", 480, "government_bond", "일본 40년물"),
)

# 시드만 넣는 리비전이라 테이블을 다시 정의하지 않는다. `op.bulk_insert`에 넘길 컬럼 이름만 있으면 된다.
indicator_series = sa.table(
    "indicator_series",
    sa.column("provider", sa.Text),
    sa.column("series_id", sa.Text),
    sa.column("country", sa.Text),
    sa.column("country_name", sa.Text),
    sa.column("maturity_months", sa.Integer),
    sa.column("kind", sa.String),
    sa.column("label", sa.Text),
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
        indicator_series,
        [
            {
                "provider": provider,
                "series_id": series_id,
                "country": country,
                "country_name": country_name,
                "maturity_months": maturity_months,
                "kind": kind,
                "label": label,
            }
            for provider, series_id, country, country_name, maturity_months, kind, label in SEED
        ],
        # offline(`--sql`)에서는 executemany를 찍을 수 없다. 행마다 INSERT를 내게 한다.
        multiinsert=False,
    )


def downgrade_default() -> None:
    op.execute("DELETE FROM indicator_series WHERE provider = 'mof'")
