"""adopt finance exchange_rate

Revision ID: 9c4be2f1a70d
Revises: 782a48c2247d
Create Date: 2026-08-05 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "9c4be2f1a70d"
down_revision: str | Sequence[str] | None = "782a48c2247d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "exchange_rate"


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


def _already_exists() -> bool:
    """Whether the target database already has the table.

    Offline (`--sql`) runs have no connection to ask, and their output is the
    full schema for a database built from scratch, so they always answer no.
    """
    if context.is_offline_mode():
        return False
    return sa.inspect(op.get_bind()).has_table(TABLE)


def upgrade_default() -> None:
    pass


def downgrade_default() -> None:
    pass


def upgrade_finance() -> None:
    # Django's `migrate --fake-initial`. `exchange_rate` predates this project,
    # holds live data, and its DDL must not change. When it is already there the
    # revision only moves `alembic_version_finance` forward and emits nothing.
    # The body below is what a finance database built from scratch would get and
    # is a byte-for-byte mirror of the existing table, so the next autogenerate
    # run sees no difference.
    if _already_exists():
        return

    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("currency", sa.String(length=10), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("time", sa.Time(), nullable=False),
        sa.Column("buy", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("sell", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("send", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("receive", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("exchange_standard_rate", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("currency", "date", "time", "round", name="unique_currency_date_time_round"),
    )
    op.create_index("idx_exchange_rate_date", TABLE, ["date"], unique=False)
    op.create_index("idx_exchange_rate_currency_date", TABLE, ["currency", "date"], unique=False)


def downgrade_finance() -> None:
    # Never dropped. The table and its data belong to the finance database, not
    # to this project, and downgrading past the revision that adopted it must not
    # destroy them. Only the revision pointer moves back.
    pass
