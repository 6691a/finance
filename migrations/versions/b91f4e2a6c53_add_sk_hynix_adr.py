"""add sk hynix adr

Revision ID: b91f4e2a6c53
Revises: a7c3e91f5b24
Create Date: 2026-08-19 15:30:00.000000

SK하이닉스 ADR(`SKHY`) 수집을 시작한다. TSMC ADR(NYSE)과 달리 나스닥 상장이라
거래소 축에 `NASDAQ`을 추가해야 한다.

- `stock_bar`/`stock_daily`의 exchange CHECK와 컬럼 주석에 NASDAQ을 넣는다.
- `quote_bar`/`quote_daily` 호환 뷰의 stock 필터에 NASDAQ을 태운다. NXT 제외
  이유(같은 종목·같은 분에 두 줄)는 그대로다. NASDAQ은 겹치는 심볼이 없다.
- `quote_symbol` 마스터에 시드 한 줄을 넣는다. 대시보드가 이 마스터에서 심볼
  목록을 읽는다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b91f4e2a6c53"
down_revision: str | Sequence[str] | None = "a7c3e91f5b24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# e5b2d7a41c93이 만든 뷰와 같은 SELECT 순서·컬럼이어야 CREATE OR REPLACE가 된다.
MACRO_BAR_TABLES = (
    "index_bar",
    "index_future_bar",
    "fx_bar",
    "rate_bar",
    "bond_future_bar",
    "commodity_bar",
    "crypto_bar",
)
MACRO_DAILY_TABLES = (
    "index_daily",
    "index_future_daily",
    "fx_daily",
    "rate_daily",
    "bond_future_daily",
    "commodity_daily",
    "crypto_daily",
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


def _set_exchange_axis(exchanges: tuple[str, ...], view_exchanges: tuple[str, ...]) -> None:
    """거래소 축(CHECK·주석·호환 뷰)을 주어진 목록으로 다시 선언한다."""
    check = "exchange IN ({})".format(", ".join(f"'{value}'" for value in exchanges))
    comment = f"체결이 일어난 거래소({', '.join(exchanges)}). 통합(UN) 시세는 받지 않는다"
    for table in ("stock_bar", "stock_daily"):
        op.drop_constraint(f"ck_{table}_exchange", table, type_="check")
        op.create_check_constraint(f"ck_{table}_exchange", table, check)
        op.execute(f"COMMENT ON COLUMN {table}.exchange IS '{comment}'")

    stock_filter = "exchange IN ({})".format(", ".join(f"'{value}'" for value in view_exchanges))
    view_label = "·".join(view_exchanges)

    bar_selects = []
    for table in MACRO_BAR_TABLES:
        contract = "contract_code" if table == "index_future_bar" else "NULL::text AS contract_code"
        bar_selects.append(
            f"SELECT provider, symbol, bar_at, open, high, low, close, volume, previous_close, "
            f"{contract}, source_record_id, created_at, updated_at FROM {table}"
        )
    bar_selects.append(
        "SELECT provider, stock_code AS symbol, bar_at, open, high, low, close, volume, previous_close, "
        f"NULL::text AS contract_code, source_record_id, created_at, updated_at "
        f"FROM stock_bar WHERE {stock_filter}"
    )
    op.execute("CREATE OR REPLACE VIEW quote_bar AS\n" + "\nUNION ALL\n".join(bar_selects))
    op.execute(
        "COMMENT ON VIEW quote_bar IS "
        f"'kind별 1분봉 테이블을 합쳐 보여 주는 호환 뷰. 쓰기는 물리 테이블로 한다. 종목은 {view_label} 봉만 태운다'"
    )

    daily_selects = [
        f"SELECT provider, symbol, business_date, open, high, low, close, volume, "
        f"source_record_id, created_at, updated_at FROM {table}"
        for table in MACRO_DAILY_TABLES
    ]
    daily_selects.append(
        "SELECT provider, stock_code AS symbol, business_date, open, high, low, close, volume, "
        f"source_record_id, created_at, updated_at FROM stock_daily WHERE {stock_filter}"
    )
    op.execute("CREATE OR REPLACE VIEW quote_daily AS\n" + "\nUNION ALL\n".join(daily_selects))
    op.execute(
        "COMMENT ON VIEW quote_daily IS "
        f"'kind별 일봉 테이블을 합쳐 보여 주는 호환 뷰. 쓰기는 물리 테이블로 한다. 종목은 {view_label} 봉만 태운다'"
    )


def upgrade_default() -> None:
    _set_exchange_axis(("KRX", "NXT", "NYSE", "NASDAQ"), ("KRX", "NYSE", "NASDAQ"))
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
                "provider": "yahoo",
                "symbol": "SK_HYNIX_ADR",
                "kind": "equity",
                "country": "KR",
                "country_name": "한국",
                "label": "SK하이닉스 ADR",
            }
        ],
        multiinsert=False,
    )


def downgrade_default() -> None:
    op.execute("DELETE FROM quote_symbol WHERE provider = 'yahoo' AND symbol = 'SK_HYNIX_ADR'")
    _set_exchange_axis(("KRX", "NXT", "NYSE"), ("KRX", "NYSE"))
