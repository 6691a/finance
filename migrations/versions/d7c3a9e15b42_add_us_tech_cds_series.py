"""add US big tech CDS spread series

Revision ID: d7c3a9e15b42
Revises: a5d3f8c21b96
Create Date: 2026-10-03 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7c3a9e15b42"
down_revision: str | Sequence[str] | None = "a5d3f8c21b96"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# DTCC 공개 체결에서 고른 미국 빅테크·반도체 열 곳의 1·3·5년 CDS 프리미엄. 설계는
# docs/collection/us-tech-cds-spread.md다. 리비전에서 앱 코드를 import하지 않는다. 대조는
# tests/migrations/test_indicator_series_catalog.py가 수집기 `CdsCompany`·`CdsTenor`와 한다.
#
# **`credit_spread`지만 만기가 있다.** `HY_OAS`는 지수라 만기가 NULL이고, CDS는 만기마다 값이
# 달라 1·3·5년을 12·36·60개월로 둔다. `kind`는 이미 있는 값이라 CHECK를 바꾸지 않는다.
#
# (provider, series_id, country, country_name, maturity_months, kind, label)
CDS_SERIES_SEED: tuple[tuple[str, str, str, str, int, str, str], ...] = (
    ("dtcc", "CDS_ORCL_1Y", "US", "미국", 12, "credit_spread", "오라클 CDS 1년"),
    ("dtcc", "CDS_ORCL_3Y", "US", "미국", 36, "credit_spread", "오라클 CDS 3년"),
    ("dtcc", "CDS_ORCL_5Y", "US", "미국", 60, "credit_spread", "오라클 CDS 5년"),
    ("dtcc", "CDS_NVDA_1Y", "US", "미국", 12, "credit_spread", "엔비디아 CDS 1년"),
    ("dtcc", "CDS_NVDA_3Y", "US", "미국", 36, "credit_spread", "엔비디아 CDS 3년"),
    ("dtcc", "CDS_NVDA_5Y", "US", "미국", 60, "credit_spread", "엔비디아 CDS 5년"),
    ("dtcc", "CDS_GOOGL_1Y", "US", "미국", 12, "credit_spread", "알파벳(구글) CDS 1년"),
    ("dtcc", "CDS_GOOGL_3Y", "US", "미국", 36, "credit_spread", "알파벳(구글) CDS 3년"),
    ("dtcc", "CDS_GOOGL_5Y", "US", "미국", 60, "credit_spread", "알파벳(구글) CDS 5년"),
    ("dtcc", "CDS_AMZN_1Y", "US", "미국", 12, "credit_spread", "아마존 CDS 1년"),
    ("dtcc", "CDS_AMZN_3Y", "US", "미국", 36, "credit_spread", "아마존 CDS 3년"),
    ("dtcc", "CDS_AMZN_5Y", "US", "미국", 60, "credit_spread", "아마존 CDS 5년"),
    ("dtcc", "CDS_AVGO_1Y", "US", "미국", 12, "credit_spread", "브로드컴 CDS 1년"),
    ("dtcc", "CDS_AVGO_3Y", "US", "미국", 36, "credit_spread", "브로드컴 CDS 3년"),
    ("dtcc", "CDS_AVGO_5Y", "US", "미국", 60, "credit_spread", "브로드컴 CDS 5년"),
    ("dtcc", "CDS_AMD_1Y", "US", "미국", 12, "credit_spread", "AMD CDS 1년"),
    ("dtcc", "CDS_AMD_3Y", "US", "미국", 36, "credit_spread", "AMD CDS 3년"),
    ("dtcc", "CDS_AMD_5Y", "US", "미국", 60, "credit_spread", "AMD CDS 5년"),
    ("dtcc", "CDS_META_1Y", "US", "미국", 12, "credit_spread", "메타 CDS 1년"),
    ("dtcc", "CDS_META_3Y", "US", "미국", 36, "credit_spread", "메타 CDS 3년"),
    ("dtcc", "CDS_META_5Y", "US", "미국", 60, "credit_spread", "메타 CDS 5년"),
    ("dtcc", "CDS_MSFT_1Y", "US", "미국", 12, "credit_spread", "마이크로소프트 CDS 1년"),
    ("dtcc", "CDS_MSFT_3Y", "US", "미국", 36, "credit_spread", "마이크로소프트 CDS 3년"),
    ("dtcc", "CDS_MSFT_5Y", "US", "미국", 60, "credit_spread", "마이크로소프트 CDS 5년"),
    ("dtcc", "CDS_AAPL_1Y", "US", "미국", 12, "credit_spread", "애플 CDS 1년"),
    ("dtcc", "CDS_AAPL_3Y", "US", "미국", 36, "credit_spread", "애플 CDS 3년"),
    ("dtcc", "CDS_AAPL_5Y", "US", "미국", 60, "credit_spread", "애플 CDS 5년"),
    ("dtcc", "CDS_INTC_1Y", "US", "미국", 12, "credit_spread", "인텔 CDS 1년"),
    ("dtcc", "CDS_INTC_3Y", "US", "미국", 36, "credit_spread", "인텔 CDS 3년"),
    ("dtcc", "CDS_INTC_5Y", "US", "미국", 60, "credit_spread", "인텔 CDS 5년"),
)

SEED_COLUMNS = ("provider", "series_id", "country", "country_name", "maturity_months", "kind", "label")


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
        [dict(zip(SEED_COLUMNS, row)) for row in CDS_SERIES_SEED],
        # offline(`--sql`)에서는 executemany를 찍을 수 없다. 행마다 INSERT를 내게 한다.
        multiinsert=False,
    )


def downgrade_default() -> None:
    op.execute("DELETE FROM indicator_series WHERE provider = 'dtcc'")


def upgrade_finance() -> None:
    pass


def downgrade_finance() -> None:
    pass
