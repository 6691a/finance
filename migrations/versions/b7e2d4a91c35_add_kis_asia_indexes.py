"""add kis asia indexes

Revision ID: b7e2d4a91c35
Revises: d5b8c204e7f1
Create Date: 2026-09-04 12:00:00.000000

니케이225·상해종합·항셍·대만가권을 KIS 해외지수 API로 받기 시작한다 — 장중 1분봉
(`kis_asia_index_intraday`)과 확정 일봉(`kis_asia_index_daily`). 설계는
`docs/collection/kis-overseas-index-close.md` §13이다.

- `quote_symbol` 마스터에 `provider = 'kis'` 행 넷을 넣는다. 브리핑·대시보드가
  `(provider, symbol)`로 봉을 조인한다.
- 심볼·라벨·국가는 같은 지수의 Yahoo 행과 같다. 같은 지수는 같은 심볼이고 제공처만 다르다.
  Yahoo 행은 그대로 둔다 — 일봉 10년 이력이 거기 있다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e2d4a91c35"
down_revision: str | Sequence[str] | None = "d5b8c204e7f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (symbol, country, country_name, label). Yahoo 시드(2029012bafaa)와 글자 그대로 같다.
SYMBOLS = (
    ("NIKKEI225", "JP", "일본", "닛케이225"),
    ("SSE_COMP", "CN", "중국", "상하이종합"),
    ("HSI", "HK", "홍콩", "항셍"),
    ("TAIEX", "TW", "대만", "대만 가권지수"),
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
                "symbol": symbol,
                "kind": "index",
                "country": country,
                "country_name": country_name,
                "label": label,
            }
            for symbol, country, country_name, label in SYMBOLS
        ],
        multiinsert=False,
    )


def downgrade_default() -> None:
    symbols = ", ".join(f"'{symbol}'" for symbol, *_ in SYMBOLS)
    op.execute(f"DELETE FROM quote_symbol WHERE provider = 'kis' AND symbol IN ({symbols})")
