"""drop exchange_rate

하나은행 환율 수집을 종료했다. 장중 환율은 `fx_bar`(yahoo 장외 시세)가 담당한다.
`daily_series` 뷰가 이 테이블을 UNION 하고 있어 DROP을 막으므로, 같은 컬럼을 내는
정의로 먼저 바꾼 뒤 테이블을 지운다.

데이터는 되살릴 수 없다. downgrade는 빈 테이블과 원래 뷰 정의만 복원한다.

Revision ID: a7c3e91f5b24
Revises: d41f7c9b3a12
Create Date: 2026-08-19 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c3e91f5b24"
down_revision: str | Sequence[str] | None = "d41f7c9b3a12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# e5b2d7a41c93의 정의에서 hana_daily 브랜치만 뺐다. 컬럼 이름·타입이 같아
# `CREATE OR REPLACE`가 성립하고, 뷰에 기대는 다른 객체를 건드리지 않는다.
DAILY_SERIES_VIEW = """SELECT quote_daily.provider,
    quote_daily.symbol AS series_id,
    quote_daily.business_date,
    quote_daily.close AS value,
    'price'::text AS kind
   FROM quote_daily
UNION ALL
 SELECT o.provider,
    o.series_id,
    o.observation_date AS business_date,
    o.value,
    COALESCE((s.kind)::text, 'rate'::text) AS kind
   FROM (indicator_observation o
     LEFT JOIN indicator_series s ON (((s.provider = o.provider) AND (s.series_id = o.series_id))))
UNION ALL
 SELECT stock_investor_trade_daily.provider,
    stock_investor_trade_daily.stock_code AS series_id,
    stock_investor_trade_daily.business_date,
    stock_investor_trade_daily.close_price AS value,
    'price'::text AS kind
   FROM stock_investor_trade_daily"""

# downgrade용. e5b2d7a41c93이 만든 정의 그대로다.
DAILY_SERIES_VIEW_WITH_HANA = (
    DAILY_SERIES_VIEW
    + """
UNION ALL
 SELECT hana_daily.provider,
    hana_daily.series_id,
    hana_daily.business_date,
    hana_daily.value,
    hana_daily.kind
   FROM ( SELECT DISTINCT ON (exchange_rate.currency, exchange_rate.date) 'hana'::text AS provider,
            exchange_rate.currency AS series_id,
            exchange_rate.date AS business_date,
            exchange_rate.exchange_standard_rate AS value,
            'fx'::text AS kind
           FROM exchange_rate
          WHERE (exchange_rate.exchange_standard_rate > (0)::numeric)
          ORDER BY exchange_rate.currency, exchange_rate.date, exchange_rate.round DESC) hana_daily"""
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
    op.execute("CREATE OR REPLACE VIEW daily_series AS\n" + DAILY_SERIES_VIEW)
    op.drop_table("exchange_rate")


def downgrade_default() -> None:
    # 2029012bafaa가 만든 DDL 그대로다. 데이터는 돌아오지 않는다.
    op.create_table(
        "exchange_rate",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False, comment="레코드 고유 식별자"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
            comment="레코드 생성 시각. 원본 DDL을 따라 시간대가 없으며 DB 서버 시각으로 채워진다",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
            comment="레코드 최종 수정 시각. 갱신은 upsert가 직접 넣으며 DB 트리거는 없다",
        ),
        sa.Column(
            "currency",
            sa.Enum(
                "USD",
                "JPY",
                "CNY",
                "EUR",
                "HKD",
                "TWD",
                "GBP",
                "AUD",
                "CAD",
                "RUB",
                name="currency",
                native_enum=False,
                length=10,
            ),
            nullable=False,
            comment="고시 통화 코드(ISO 4217). 허용 값은 Currency Enum이 정한다",
        ),
        sa.Column(
            "round",
            sa.Integer(),
            nullable=False,
            comment="같은 고시일자 안의 고시 회차. 1부터 증가하며 값이 클수록 나중 고시다",
        ),
        sa.Column(
            "date", sa.Date(), nullable=False, comment="고시일자. 하나은행 기준이라 KST이며 컬럼에 시간대 정보는 없다"
        ),
        sa.Column(
            "time", sa.Time(), nullable=False, comment="고시 시각. 고시일자와 같은 KST 기준이고 시간대 정보는 없다"
        ),
        sa.Column(
            "buy",
            sa.Numeric(precision=10, scale=2),
            nullable=False,
            comment="현찰 사실 때 환율(원). 고객이 외화를 현찰로 살 때 적용된다",
        ),
        sa.Column(
            "sell",
            sa.Numeric(precision=10, scale=2),
            nullable=False,
            comment="현찰 파실 때 환율(원). 고객이 외화를 현찰로 팔 때 적용된다",
        ),
        sa.Column("send", sa.Numeric(precision=10, scale=2), nullable=False, comment="송금 보낼 때 환율(원)"),
        sa.Column("receive", sa.Numeric(precision=10, scale=2), nullable=False, comment="송금 받을 때 환율(원)"),
        sa.Column(
            "exchange_standard_rate",
            sa.Numeric(precision=10, scale=2),
            nullable=False,
            comment="매매기준율(원). 일부 통화는 0으로 고시되므로 조회 쪽에서 대체값을 쓴다",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("currency", "date", "time", "round", name="unique_currency_date_time_round"),
        comment="하나은행이 고시한 통화별·회차별 환율",
    )
    op.create_index("idx_exchange_rate_currency_date", "exchange_rate", ["currency", "date"], unique=False)
    op.create_index("idx_exchange_rate_date", "exchange_rate", ["date"], unique=False)
    op.execute("CREATE OR REPLACE VIEW daily_series AS\n" + DAILY_SERIES_VIEW_WITH_HANA)
