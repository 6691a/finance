"""add kis us spot indexes

Revision ID: a3f9c1d27e64
Revises: 6e09dafae6f8
Create Date: 2026-08-22 12:00:00.000000

S&P500(`SPX`)·나스닥 종합(`COMP`) 현물 지수를 KIS 해외지수 분봉 API로 받기 시작한다
(`kis_overseas_index_close`, `docs/collection/kis-overseas-index-close.md`). 지금까지 둘은 선물만
(Yahoo `ES=F`·`NQ=F`) 있었다.

- `quote_symbol` 마스터에 `provider = 'kis'` 행 둘을 넣는다. 브리핑·대시보드가
  `(provider, symbol)`로 봉을 조인한다.
- 다우 현물은 KIS 분봉이 0건이라 넣지 않는다. 러셀 현물은 이미 Yahoo `RUSSELL2000`이다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3f9c1d27e64"
down_revision: str | Sequence[str] | None = "6e09dafae6f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SYMBOLS = ("SP500", "NASDAQ")


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
            "quote_symbol",
            sa.column("provider", sa.Text),
            sa.column("symbol", sa.Text),
            sa.column("kind", sa.Text),
            sa.column("country", sa.Text),
            sa.column("country_name", sa.Text),
            sa.column("label", sa.Text),
        ),
        [
            {
                "provider": "kis",
                "symbol": "SP500",
                "kind": "index",
                "country": "US",
                "country_name": "미국",
                "label": "S&P500",
            },
            {
                "provider": "kis",
                "symbol": "NASDAQ",
                "kind": "index",
                "country": "US",
                "country_name": "미국",
                "label": "나스닥 종합",
            },
        ],
        multiinsert=False,
    )


def downgrade_default() -> None:
    symbols = ", ".join(f"'{symbol}'" for symbol in SYMBOLS)
    op.execute(f"DELETE FROM quote_symbol WHERE provider = 'kis' AND symbol IN ({symbols})")
