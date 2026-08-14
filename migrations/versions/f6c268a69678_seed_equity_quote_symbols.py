"""seed equity quote symbols

Revision ID: f6c268a69678
Revises: 0efdb0dd4485
Create Date: 2026-08-15 00:12:25.362876

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f6c268a69678'
down_revision: str | Sequence[str] | None = '0efdb0dd4485'
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


# 분봉을 쌓는 종목. `modules.collectors.kis.DomesticStock`과 같아야 하고
# `tests/migrations/test_quote_symbol_catalog.py`가 둘을 대조한다.
#
# **심볼이 6자리 종목코드다.** 지수·선물은 `KOSPI`처럼 이름을 쓰지만 종목은 코드를 쓴다.
# 공시·수급·포지션 테이블이 전부 그 코드를 키로 쓰고 있어, 봉만 이름을 쓰면 화면에서 조인이
# 안 된다.
EQUITY_QUOTE_SYMBOL_SEED: tuple[tuple[str, str, str, str, str, str], ...] = (
    ("kis", "005930", "equity", "KR", "한국", "삼성전자"),
    ("kis", "000660", "equity", "KR", "한국", "SK하이닉스"),
)


def upgrade_default() -> None:
    quote_symbol = sa.table(
        "quote_symbol",
        sa.column("provider", sa.Text),
        sa.column("symbol", sa.Text),
        sa.column("kind", sa.Text),
        sa.column("country", sa.Text),
        sa.column("country_name", sa.Text),
        sa.column("label", sa.Text),
    )
    op.bulk_insert(
        quote_symbol,
        [
            dict(zip(("provider", "symbol", "kind", "country", "country_name", "label"), row))
            for row in EQUITY_QUOTE_SYMBOL_SEED
        ],
        # offline(`--sql`)에서는 executemany를 찍을 수 없다. 행마다 INSERT를 내게 한다.
        multiinsert=False,
    )


def downgrade_default() -> None:
    op.execute(
        "DELETE FROM quote_symbol WHERE provider = 'kis' AND symbol IN ('005930', '000660')"
    )


def upgrade_market_migration() -> None:
    pass


def downgrade_market_migration() -> None:
    pass

