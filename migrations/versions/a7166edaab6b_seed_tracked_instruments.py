"""seed tracked instruments

Revision ID: a7166edaab6b
Revises: b186ab47dbd8
Create Date: 2026-08-13 13:34:23.921265

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7166edaab6b"
down_revision: str | Sequence[str] | None = "b186ab47dbd8"
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


# 추적 종목 마스터 시드.
#
# **화면이 종목코드를 그대로 보여 주지 않게 하려는 것이다.** `005930`은 사람이 읽는 이름이
# 아니다. 이 마스터가 없으면 대시보드마다 코드→이름 대응을 SQL에 복사하게 되고, 종목이
# 늘 때 화면을 전부 고쳐야 한다. `indicator_series`와 `quote_symbol`이 지표·심볼에 하는
# 역할을 종목에서 한다.
#
# 리비전에서 앱 코드를 import하지 않는다. import하면 나중에 Enum이 바뀔 때 과거 리비전의
# 결과가 따라 바뀐다. 수집기 Enum과의 대조는
# `tests/migrations/test_instrument_catalog.py`가 한다.
INSTRUMENTS: tuple[dict[str, object], ...] = (
    {
        "ticker": "005930",
        "market": "kospi",
        "name": "삼성전자",
        "kind": "equity",
        "currency": "KRW",
        "is_watched": True,
    },
    {
        "ticker": "000660",
        "market": "kospi",
        "name": "SK하이닉스",
        "kind": "equity",
        "currency": "KRW",
        "is_watched": True,
    },
)


def upgrade_default() -> None:
    instrument = sa.table(
        "instrument",
        sa.column("ticker", sa.Text),
        sa.column("market", sa.String),
        sa.column("name", sa.Text),
        sa.column("kind", sa.String),
        sa.column("currency", sa.Text),
        sa.column("is_watched", sa.Boolean),
    )
    op.bulk_insert(instrument, [dict(row) for row in INSTRUMENTS])


def downgrade_default() -> None:
    tickers = ", ".join(f"'{row['ticker']}'" for row in INSTRUMENTS)
    op.execute(f"DELETE FROM instrument WHERE market = 'kospi' AND ticker IN ({tickers})")


def upgrade_market_migration() -> None:
    pass


def downgrade_market_migration() -> None:
    pass
