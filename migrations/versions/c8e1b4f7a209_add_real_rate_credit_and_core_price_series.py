"""add real rate, credit spread and core price series

Revision ID: c8e1b4f7a209
Revises: b7f4c2a91d38
Create Date: 2026-08-28 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8e1b4f7a209"
down_revision: str | Sequence[str] | None = "b4e91c72a3d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 명목 금리와 헤드라인 물가만으로는 못 가르는 여섯 계열. 리비전에서 앱 코드를 import하지
# 않는다. import하면 나중에 수집기 Enum이 바뀔 때 과거 리비전의 결과가 따라 바뀐다.
# 대조는 tests/migrations가 한다.
#
# **`tips_rate` 둘은 만기가 있다.** 명목 10년물과 같은 120이고, 그래서 종류를 갈랐다.
# 같은 종류였으면 미국 10년물이 세 개로 보인다.
#
# **`credit_spread`와 근원 물가·주간 청구는 만기가 NULL이다.** 만기 개념이 없다. 0으로
# 채우면 만기별 비교 쿼리가 "0개월물"로 그린다.
#
# 계열 좌표·단위·주기는 2026-08-27에 FRED `series` 페이지로 확인했다.
#
# (provider, series_id, country, country_name, maturity_months, kind, label)
SIGNAL_SERIES_SEED: tuple[tuple[str, str, str, str, int | None, str, str], ...] = (
    ("fred", "REAL10Y", "US", "미국", 120, "tips_rate", "미국 10년 실질금리(TIPS)"),
    ("fred", "BREAKEVEN10Y", "US", "미국", 120, "tips_rate", "미국 10년 기대인플레(BEI)"),
    ("fred", "HY_OAS", "US", "미국", None, "credit_spread", "미국 하이일드 신용스프레드(OAS)"),
    ("fred", "CORE_CPI_M", "US", "미국", None, "price_index", "미국 근원 소비자물가지수"),
    ("fred", "CORE_PCE_M", "US", "미국", None, "price_index", "미국 근원 PCE 물가지수"),
    ("fred", "INITIAL_CLAIMS_W", "US", "미국", None, "activity", "미국 주간 신규 실업수당 청구"),
)

KIND_CHECK = "ck_indicator_series_kind"

# 앞 리비전(c9f1e4b70a25)이 대차대조표 둘을 더한 뒤의 목록에 `tips_rate`·`credit_spread`를 얹는다.
KINDS = (
    "government_bond",
    "money_market",
    "policy_rate",
    "tips_rate",
    "credit_spread",
    "price_index",
    "activity",
    "balance_sheet",
    "balance_sheet_item",
)
PREVIOUS_KINDS = (
    "government_bond",
    "money_market",
    "policy_rate",
    "price_index",
    "activity",
    "balance_sheet",
    "balance_sheet_item",
)

KIND_COMMENT = (
    "시계열의 종류(government_bond, money_market, policy_rate, tips_rate, credit_spread, "
    "price_index, activity, balance_sheet 또는 balance_sheet_item). 국채 곡선에서 단기 자금시장 "
    "금리·정책금리·실질금리·신용스프레드를 가르고, 단위가 다른 거시지표와 대차대조표 잔액을 "
    "그 곡선에서 뺀다"
)
PREVIOUS_KIND_COMMENT = (
    "시계열의 종류(government_bond, money_market, policy_rate, price_index, activity, "
    "balance_sheet 또는 balance_sheet_item). 국채 곡선에서 단기 자금시장 금리와 중앙은행 정책금리를 "
    "가르고, 단위가 다른 거시지표와 대차대조표 잔액을 그 곡선에서 뺀다"
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


def _kind_check(kinds: tuple[str, ...]) -> str:
    values = ", ".join(f"'{kind}'" for kind in kinds)
    return f"kind IN ({values})"


def upgrade_default() -> None:
    # autogenerate는 CHECK 제약의 변경을 만들지 않는다. 손으로 갈아 끼운다.
    op.drop_constraint(KIND_CHECK, "indicator_series", type_="check")
    op.create_check_constraint(KIND_CHECK, "indicator_series", _kind_check(KINDS))

    # 컬럼 주석에도 종류가 나열돼 있다. 모델과 어긋나면 autogenerate가 매번 COMMENT ON 차이를 낸다.
    op.alter_column(
        "indicator_series",
        "kind",
        existing_type=sa.VARCHAR(length=20),
        comment=KIND_COMMENT,
        existing_comment=PREVIOUS_KIND_COMMENT,
        existing_nullable=False,
    )

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
        [dict(zip(SEED_COLUMNS, row)) for row in SIGNAL_SERIES_SEED],
        # offline(`--sql`)에서는 executemany를 찍을 수 없다. 행마다 INSERT를 내게 한다.
        multiinsert=False,
    )


def downgrade_default() -> None:
    # 시드를 먼저 지운다. 새 kind를 쓰는 행이 남아 있으면 제약을 되돌릴 수 없다.
    series_ids = ", ".join(f"'{row[1]}'" for row in SIGNAL_SERIES_SEED)
    op.execute(f"DELETE FROM indicator_series WHERE provider = 'fred' AND series_id IN ({series_ids})")

    op.alter_column(
        "indicator_series",
        "kind",
        existing_type=sa.VARCHAR(length=20),
        comment=PREVIOUS_KIND_COMMENT,
        existing_comment=KIND_COMMENT,
        existing_nullable=False,
    )

    op.drop_constraint(KIND_CHECK, "indicator_series", type_="check")
    op.create_check_constraint(KIND_CHECK, "indicator_series", _kind_check(PREVIOUS_KINDS))


def upgrade_finance() -> None:
    pass


def downgrade_finance() -> None:
    pass
