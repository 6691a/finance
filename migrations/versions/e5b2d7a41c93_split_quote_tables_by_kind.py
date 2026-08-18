"""split quote tables by kind

Revision ID: e5b2d7a41c93
Revises: c7d41e8ab902
Create Date: 2026-08-18 17:40:00.000000

`quote_bar`/`quote_daily` 두 테이블이 8개 kind(지수·지수선물·환율·금리·채권선물·원자재·
종목·암호화폐)를 전부 담아 직접 조회할 때 자산군이 뒤섞여 읽기 어려웠다. kind별 물리
테이블로 가르고, 기존 이름은 UNION ALL 뷰로 남겨 브리핑 SQL과 Grafana 대시보드가
수정 없이 돌게 한다.

- 개별 종목(`stock_bar`/`stock_daily`)만 축이 다르다. 거래소(KRX/NXT/NYSE)가 자연키에
  들어가고 식별자 컬럼이 `stock_code`다. TSMC ADR은 NYSE로 이관한다.
- 이관 행 수를 원본과 대조하고 어긋나면 실패시켜 트랜잭션을 되돌린다.
  offline(`--sql`)은 연결이 없으므로 대조 없이 전체 DDL만 찍는다.
- 뷰에는 stock 쪽의 KRX·NYSE 봉만 태운다. NXT를 태우면 같은 종목·같은 분에 두 줄이
  생겨 `DISTINCT ON (provider, symbol)` 조회가 거래소를 뒤섞는다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5b2d7a41c93"
down_revision: str | Sequence[str] | None = "c7d41e8ab902"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# kind → (bar 테이블, daily 테이블). equity 는 모양이 달라 별도로 다룬다.
MACRO_TABLES: tuple[tuple[str, str, str, str], ...] = (
    ("index", "index_bar", "index_daily", "현물 지수"),
    ("index_future", "index_future_bar", "index_future_daily", "지수선물"),
    ("fx", "fx_bar", "fx_daily", "장외 시장 환율"),
    ("rate", "rate_bar", "rate_daily", "금리 수익률"),
    ("bond_future", "bond_future_bar", "bond_future_daily", "채권선물 가격"),
    ("commodity", "commodity_bar", "commodity_daily", "원자재 선물"),
    ("crypto", "crypto_bar", "crypto_daily", "암호화폐"),
)

BAR_COLUMNS = "provider, symbol, bar_at, open, high, low, close, volume, previous_close, source_record_id, created_at, updated_at"
DAILY_COLUMNS = "provider, symbol, business_date, open, high, low, close, volume, source_record_id, created_at, updated_at"


def _entity_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="레코드 고유 식별자"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="레코드 생성 시각(UTC)",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="레코드 최종 수정 시각(UTC)",
        ),
    ]


def _macro_bar_columns() -> list[sa.Column]:
    return [
        sa.Column(
            "provider",
            sa.Text(),
            nullable=False,
            comment="데이터 제공처 식별자(예: yahoo 또는 kis). 같은 수집의 source_record.source와 같은 값이다",
        ),
        sa.Column(
            "symbol",
            sa.Text(),
            nullable=False,
            comment="시세 대상 식별자(예: SP500_FUT, SOX). quote_symbol 마스터의 symbol과 같으며 제공처 안에서만 고유하다",
        ),
        sa.Column(
            "bar_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="1분봉이 시작하는 시각(UTC). 봉은 이 시각부터 1분간의 거래를 담는다",
        ),
        sa.Column("open", sa.Numeric(precision=18, scale=8), nullable=False, comment="봉 구간의 시가"),
        sa.Column("high", sa.Numeric(precision=18, scale=8), nullable=False, comment="봉 구간의 고가"),
        sa.Column("low", sa.Numeric(precision=18, scale=8), nullable=False, comment="봉 구간의 저가"),
        sa.Column("close", sa.Numeric(precision=18, scale=8), nullable=False, comment="봉 구간의 종가"),
        sa.Column(
            "volume",
            sa.BigInteger(),
            nullable=True,
            comment="봉 구간의 거래량. 제공처가 주는 값을 그대로 저장한다. 현물 지수처럼 거래량 개념이 없는 심볼은 제공처가 0을 실어 보내므로 0이 들어간다. 즉 0은 '거래가 없었다'와 '제공처가 거래량을 주지 않는다'를 구분하지 않는다",
        ),
        sa.Column(
            "previous_close",
            sa.Numeric(precision=18, scale=8),
            nullable=False,
            comment="직전 정규장 종가. 알림이 쓰는 변동률의 분모다. 봉마다 같은 값이 반복되지만 세션 경계 계산을 피하려고 그대로 저장한다",
        ),
        sa.Column("source_record_id", sa.BigInteger(), nullable=False, comment="근거가 되는 source_record 레코드 ID"),
    ]


def _macro_daily_columns() -> list[sa.Column]:
    return [
        sa.Column(
            "provider",
            sa.Text(),
            nullable=False,
            comment="데이터 제공처 식별자(yahoo). 같은 수집의 source_record.source와 같은 값이다",
        ),
        sa.Column(
            "symbol",
            sa.Text(),
            nullable=False,
            comment="시세 대상 식별자(예: USDKRW, SOX). quote_symbol 마스터의 symbol과 같으며 제공처 안에서만 고유하다",
        ),
        sa.Column(
            "business_date",
            sa.Date(),
            nullable=False,
            comment="봉이 담는 거래일. 제공처가 준 봉 시작 시각을 그 시장의 현지 날짜로 바꾼 값이다. 심볼마다 기준 시장이 달라 UTC 날짜와 어긋날 수 있다",
        ),
        sa.Column("open", sa.Numeric(precision=18, scale=8), nullable=False, comment="그 거래일의 시가"),
        sa.Column("high", sa.Numeric(precision=18, scale=8), nullable=False, comment="그 거래일의 고가"),
        sa.Column("low", sa.Numeric(precision=18, scale=8), nullable=False, comment="그 거래일의 저가"),
        sa.Column("close", sa.Numeric(precision=18, scale=8), nullable=False, comment="그 거래일의 종가"),
        sa.Column(
            "volume",
            sa.BigInteger(),
            nullable=True,
            comment="그 거래일의 거래량. 제공처가 주는 값을 그대로 저장한다. 현물 지수와 환율처럼 거래량 개념이 없는 심볼은 제공처가 0을 실어 보내므로 0이 들어간다",
        ),
        sa.Column("source_record_id", sa.BigInteger(), nullable=False, comment="근거가 되는 source_record 레코드 ID"),
    ]


CONTRACT_CODE_COMMENT = (
    "선물의 실제 월물 코드(예: A01609). Yahoo 연속 심볼(ES=F)은 NULL이다. "
    "월물이 바뀌면 가격에 갭이 생기는데, 이 값이 없으면 그 갭이 시장 급변인지 롤오버인지 구분할 수 없다"
)


def _create_bar_table(table: str, label: str, *, with_contract: bool) -> None:
    columns = _macro_bar_columns()
    if with_contract:
        columns.insert(0, sa.Column("contract_code", sa.Text(), nullable=True, comment=CONTRACT_CODE_COMMENT))
    op.create_table(
        table,
        *columns,
        *_entity_columns(),
        sa.ForeignKeyConstraint(["source_record_id"], ["source_record.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "symbol", "bar_at", name=f"uq_{table}_natural_key"),
        comment=f"{label}의 1분봉을 장중 알림 판단용으로 누적하는 테이블",
        info={"database": "default", "managed": True},
    )
    op.create_index(f"ix_{table}_source_record_id", table, ["source_record_id"], unique=False)


def _create_daily_table(table: str, label: str) -> None:
    op.create_table(
        table,
        *_macro_daily_columns(),
        *_entity_columns(),
        sa.ForeignKeyConstraint(["source_record_id"], ["source_record.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "symbol", "business_date", name=f"uq_{table}_natural_key"),
        comment=f"{label}의 일봉을 상관 분석용으로 누적하는 테이블",
        info={"database": "default", "managed": True},
    )
    op.create_index(f"ix_{table}_business_date", table, ["business_date"], unique=False)
    op.create_index(f"ix_{table}_source_record_id", table, ["source_record_id"], unique=False)


def _stock_shared_columns(price_comment_suffix: str) -> list[sa.Column]:
    return [
        sa.Column("open", sa.Numeric(precision=18, scale=4), nullable=False, comment=f"봉 구간의 시가{price_comment_suffix}"),
        sa.Column("high", sa.Numeric(precision=18, scale=4), nullable=False, comment=f"봉 구간의 고가{price_comment_suffix}"),
        sa.Column("low", sa.Numeric(precision=18, scale=4), nullable=False, comment=f"봉 구간의 저가{price_comment_suffix}"),
        sa.Column("close", sa.Numeric(precision=18, scale=4), nullable=False, comment=f"봉 구간의 종가{price_comment_suffix}"),
    ]


def _create_stock_tables() -> None:
    op.create_table(
        "stock_bar",
        sa.Column(
            "provider",
            sa.Text(),
            nullable=False,
            comment="데이터 제공처 식별자(kis 또는 yahoo). 같은 수집의 source_record.source와 같은 값이다",
        ),
        sa.Column(
            "stock_code",
            sa.Text(),
            nullable=False,
            comment="국내는 한국거래소 6자리 종목코드(005930), 해외 상장 종목은 저장 심볼(TSMC_ADR)이다. 국내 코드는 instrument.ticker, 수급·공시 테이블과 같은 체계라 한 화면에서 조인된다",
        ),
        sa.Column(
            "exchange",
            sa.String(length=20),
            nullable=False,
            comment="체결이 일어난 거래소(KRX, NXT, NYSE). 통합(UN) 시세는 받지 않는다",
        ),
        sa.Column(
            "bar_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="1분봉이 시작하는 시각(UTC). 봉은 이 시각부터 1분간의 거래를 담는다",
        ),
        *_stock_shared_columns(". 국내는 원 단위"),
        sa.Column(
            "volume",
            sa.BigInteger(),
            nullable=True,
            comment="봉 구간의 거래량(주). 제공처가 주는 값을 그대로 저장한다",
        ),
        sa.Column(
            "previous_close",
            sa.Numeric(precision=18, scale=4),
            nullable=False,
            comment="직전 거래일 확정 종가. 변동률의 분모다. NXT 봉도 KRX 확정 종가를 쓴다 — 전일 기준가가 거래소마다 따로 있지 않기 때문이다",
        ),
        sa.Column("source_record_id", sa.BigInteger(), nullable=False, comment="근거가 되는 source_record 레코드 ID"),
        *_entity_columns(),
        sa.CheckConstraint("exchange IN ('KRX', 'NXT', 'NYSE')", name="ck_stock_bar_exchange"),
        sa.ForeignKeyConstraint(["source_record_id"], ["source_record.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "stock_code", "exchange", "bar_at", name="uq_stock_bar_natural_key"),
        comment="개별 종목의 1분봉을 거래소 단위로 누적하는 테이블",
        info={"database": "default", "managed": True},
    )
    op.create_index("ix_stock_bar_source_record_id", "stock_bar", ["source_record_id"], unique=False)

    op.create_table(
        "stock_daily",
        sa.Column(
            "provider",
            sa.Text(),
            nullable=False,
            comment="데이터 제공처 식별자(yahoo). 같은 수집의 source_record.source와 같은 값이다",
        ),
        sa.Column(
            "stock_code",
            sa.Text(),
            nullable=False,
            comment="국내는 한국거래소 6자리 종목코드, 해외 상장 종목은 저장 심볼(TSMC_ADR)이다",
        ),
        sa.Column(
            "exchange",
            sa.String(length=20),
            nullable=False,
            comment="체결이 일어난 거래소(KRX, NXT, NYSE). 통합(UN) 시세는 받지 않는다",
        ),
        sa.Column(
            "business_date",
            sa.Date(),
            nullable=False,
            comment="봉이 담는 거래일. 그 시장의 현지 날짜라 UTC 날짜와 어긋날 수 있다",
        ),
        sa.Column("open", sa.Numeric(precision=18, scale=4), nullable=False, comment="그 거래일의 시가"),
        sa.Column("high", sa.Numeric(precision=18, scale=4), nullable=False, comment="그 거래일의 고가"),
        sa.Column("low", sa.Numeric(precision=18, scale=4), nullable=False, comment="그 거래일의 저가"),
        sa.Column("close", sa.Numeric(precision=18, scale=4), nullable=False, comment="그 거래일의 종가"),
        sa.Column(
            "volume",
            sa.BigInteger(),
            nullable=True,
            comment="그 거래일의 거래량(주). 제공처가 주는 값을 그대로 저장한다",
        ),
        sa.Column("source_record_id", sa.BigInteger(), nullable=False, comment="근거가 되는 source_record 레코드 ID"),
        *_entity_columns(),
        sa.CheckConstraint("exchange IN ('KRX', 'NXT', 'NYSE')", name="ck_stock_daily_exchange"),
        sa.ForeignKeyConstraint(["source_record_id"], ["source_record.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "stock_code", "exchange", "business_date", name="uq_stock_daily_natural_key"),
        comment="개별 종목의 일봉을 거래소 단위로 누적하는 테이블",
        info={"database": "default", "managed": True},
    )
    op.create_index("ix_stock_daily_business_date", "stock_daily", ["business_date"], unique=False)
    op.create_index("ix_stock_daily_source_record_id", "stock_daily", ["source_record_id"], unique=False)


# TSMC ADR 은 뉴욕 상장이라 NYSE, 나머지 equity(6자리 코드)는 KRX 다.
STOCK_EXCHANGE_CASE = "CASE WHEN quote.symbol = 'TSMC_ADR' THEN 'NYSE' ELSE 'KRX' END"


def _migrate_rows() -> None:
    for kind, bar_table, daily_table, _ in MACRO_TABLES:
        contract = "contract_code, " if bar_table == "index_future_bar" else ""
        op.execute(
            f"INSERT INTO {bar_table} ({contract}{BAR_COLUMNS}) "
            f"SELECT {'quote.contract_code, ' if contract else ''}quote.provider, quote.symbol, quote.bar_at, "
            "quote.open, quote.high, quote.low, quote.close, quote.volume, quote.previous_close, "
            "quote.source_record_id, quote.created_at, quote.updated_at "
            "FROM quote_bar AS quote JOIN quote_symbol AS symbol "
            "ON symbol.provider = quote.provider AND symbol.symbol = quote.symbol "
            f"WHERE symbol.kind = '{kind}'"
        )
        op.execute(
            f"INSERT INTO {daily_table} ({DAILY_COLUMNS}) "
            "SELECT quote.provider, quote.symbol, quote.business_date, "
            "quote.open, quote.high, quote.low, quote.close, quote.volume, "
            "quote.source_record_id, quote.created_at, quote.updated_at "
            "FROM quote_daily AS quote JOIN quote_symbol AS symbol "
            "ON symbol.provider = quote.provider AND symbol.symbol = quote.symbol "
            f"WHERE symbol.kind = '{kind}'"
        )

    op.execute(
        "INSERT INTO stock_bar (provider, stock_code, exchange, bar_at, open, high, low, close, "
        "volume, previous_close, source_record_id, created_at, updated_at) "
        f"SELECT quote.provider, quote.symbol, {STOCK_EXCHANGE_CASE}, quote.bar_at, "
        "quote.open, quote.high, quote.low, quote.close, quote.volume, quote.previous_close, "
        "quote.source_record_id, quote.created_at, quote.updated_at "
        "FROM quote_bar AS quote JOIN quote_symbol AS symbol "
        "ON symbol.provider = quote.provider AND symbol.symbol = quote.symbol "
        "WHERE symbol.kind = 'equity'"
    )
    op.execute(
        "INSERT INTO stock_daily (provider, stock_code, exchange, business_date, open, high, low, close, "
        "volume, source_record_id, created_at, updated_at) "
        f"SELECT quote.provider, quote.symbol, {STOCK_EXCHANGE_CASE}, quote.business_date, "
        "quote.open, quote.high, quote.low, quote.close, quote.volume, "
        "quote.source_record_id, quote.created_at, quote.updated_at "
        "FROM quote_daily AS quote JOIN quote_symbol AS symbol "
        "ON symbol.provider = quote.provider AND symbol.symbol = quote.symbol "
        "WHERE symbol.kind = 'equity'"
    )


def _verify_row_counts() -> None:
    """이관 행 수를 원본과 대조한다. 어긋나면 실패시켜 트랜잭션을 되돌린다.

    마스터에 없는 심볼은 JOIN 에서 빠져 조용히 사라질 수 있다. 그게 이 대조가 있는 이유다.
    """
    bind = op.get_bind()
    bar_tables = [table for _, table, _, _ in MACRO_TABLES] + ["stock_bar"]
    daily_tables = [table for _, _, table, _ in MACRO_TABLES] + ["stock_daily"]
    for source, targets in (("quote_bar", bar_tables), ("quote_daily", daily_tables)):
        expected = bind.execute(sa.text(f"SELECT count(*) FROM {source}")).scalar_one()
        moved = sum(
            bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one() for table in targets
        )
        if moved != expected:
            raise RuntimeError(
                f"{source} migration moved {moved} rows but the source holds {expected}; "
                "a symbol is probably missing from quote_symbol"
            )


BAR_VIEW_COLUMNS = "provider, symbol, bar_at, open, high, low, close, volume, previous_close, contract_code, source_record_id, created_at, updated_at"

# 운영 DB에 마이그레이션 밖에서 손으로 만들어 둔 뷰. quote_daily에 의존해서 DROP을 막는다
# (실측 2026-08-18: DependentObjectsStillExistError). 정의를 그대로 캡처해 두고, 원본
# quote_daily를 지우기 전에 내렸다가 같은 이름의 호환 뷰가 생긴 뒤 똑같이 되살린다.
# 새 quote_daily 뷰가 같은 컬럼(provider, symbol, business_date, close)을 내므로 정의가
# 글자 그대로 다시 성립한다.
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
   FROM stock_investor_trade_daily
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


def _create_views() -> None:
    bar_selects = []
    for _, table, _, _ in MACRO_TABLES:
        contract = "contract_code" if table == "index_future_bar" else "NULL::text AS contract_code"
        bar_selects.append(
            f"SELECT provider, symbol, bar_at, open, high, low, close, volume, previous_close, "
            f"{contract}, source_record_id, created_at, updated_at FROM {table}"
        )
    # NXT 를 태우면 같은 종목·같은 분에 두 줄이 생겨 DISTINCT ON 조회가 거래소를 섞는다.
    bar_selects.append(
        "SELECT provider, stock_code AS symbol, bar_at, open, high, low, close, volume, previous_close, "
        "NULL::text AS contract_code, source_record_id, created_at, updated_at "
        "FROM stock_bar WHERE exchange IN ('KRX', 'NYSE')"
    )
    op.execute("CREATE VIEW quote_bar AS\n" + "\nUNION ALL\n".join(bar_selects))
    op.execute(
        "COMMENT ON VIEW quote_bar IS "
        "'kind별 1분봉 테이블을 합쳐 보여 주는 호환 뷰. 쓰기는 물리 테이블로 한다. 종목은 KRX·NYSE 봉만 태운다'"
    )

    daily_selects = [
        f"SELECT provider, symbol, business_date, open, high, low, close, volume, "
        f"source_record_id, created_at, updated_at FROM {table}"
        for _, _, table, _ in MACRO_TABLES
    ]
    daily_selects.append(
        "SELECT provider, stock_code AS symbol, business_date, open, high, low, close, volume, "
        "source_record_id, created_at, updated_at FROM stock_daily WHERE exchange IN ('KRX', 'NYSE')"
    )
    op.execute("CREATE VIEW quote_daily AS\n" + "\nUNION ALL\n".join(daily_selects))
    op.execute(
        "COMMENT ON VIEW quote_daily IS "
        "'kind별 일봉 테이블을 합쳐 보여 주는 호환 뷰. 쓰기는 물리 테이블로 한다. 종목은 KRX·NYSE 봉만 태운다'"
    )


def upgrade(engine_name: str) -> None:
    _run(f"upgrade_{engine_name}")


def downgrade(engine_name: str) -> None:
    _run(f"downgrade_{engine_name}")


def _run(name: str) -> None:
    operations = globals().get(name)
    if operations is not None:
        operations()


def upgrade_default() -> None:
    for _, bar_table, daily_table, label in MACRO_TABLES:
        _create_bar_table(bar_table, label, with_contract=bar_table == "index_future_bar")
        _create_daily_table(daily_table, label)
    _create_stock_tables()

    _migrate_rows()
    if not op.get_context().as_sql:
        _verify_row_counts()

    op.execute("DROP VIEW IF EXISTS daily_series")
    op.drop_table("quote_bar")
    op.drop_table("quote_daily")
    _create_views()
    op.execute("CREATE VIEW daily_series AS\n" + DAILY_SERIES_VIEW)


def downgrade_default() -> None:
    op.execute("DROP VIEW IF EXISTS daily_series")
    op.execute("DROP VIEW quote_daily")
    op.execute("DROP VIEW quote_bar")

    # 원본 두 테이블을 squash 리비전의 DDL 그대로 되살린다.
    op.create_table(
        "quote_bar",
        *_macro_bar_columns()[:9],
        sa.Column("contract_code", sa.Text(), nullable=True, comment=CONTRACT_CODE_COMMENT),
        sa.Column("source_record_id", sa.BigInteger(), nullable=False, comment="근거가 되는 source_record 레코드 ID"),
        *_entity_columns(),
        sa.ForeignKeyConstraint(["source_record_id"], ["source_record.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "symbol", "bar_at", name="uq_quote_bar_natural_key"),
        comment="지수·선물의 1분봉을 장중 알림 판단용으로 누적하는 테이블",
        info={"database": "default", "managed": True},
    )
    op.create_index("ix_quote_bar_source_record_id", "quote_bar", ["source_record_id"], unique=False)
    op.create_table(
        "quote_daily",
        *_macro_daily_columns(),
        *_entity_columns(),
        sa.ForeignKeyConstraint(["source_record_id"], ["source_record.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "symbol", "business_date", name="uq_quote_daily_natural_key"),
        comment="지수·선물·환율의 일봉을 상관 분석용으로 누적하는 테이블",
        info={"database": "default", "managed": True},
    )
    op.create_index("ix_quote_daily_business_date", "quote_daily", ["business_date"], unique=False)
    op.create_index("ix_quote_daily_source_record_id", "quote_daily", ["source_record_id"], unique=False)

    for _, bar_table, daily_table, _ in MACRO_TABLES:
        contract_select = "contract_code" if bar_table == "index_future_bar" else "NULL"
        op.execute(
            f"INSERT INTO quote_bar ({BAR_COLUMNS}, contract_code) "
            f"SELECT {BAR_COLUMNS}, {contract_select} FROM {bar_table}"
        )
        op.execute(f"INSERT INTO quote_daily ({DAILY_COLUMNS}) SELECT {DAILY_COLUMNS} FROM {daily_table}")

    # NXT 봉은 원본 테이블에 자리(거래소 축)가 없어 되돌리면 사라진다.
    op.execute(
        f"INSERT INTO quote_bar ({BAR_COLUMNS}, contract_code) "
        "SELECT provider, stock_code, bar_at, open, high, low, close, volume, previous_close, "
        "source_record_id, created_at, updated_at, NULL "
        "FROM stock_bar WHERE exchange IN ('KRX', 'NYSE')"
    )
    op.execute(
        f"INSERT INTO quote_daily ({DAILY_COLUMNS}) "
        "SELECT provider, stock_code, business_date, open, high, low, close, volume, "
        "source_record_id, created_at, updated_at "
        "FROM stock_daily WHERE exchange IN ('KRX', 'NYSE')"
    )

    for _, bar_table, daily_table, _ in MACRO_TABLES:
        op.drop_table(bar_table)
        op.drop_table(daily_table)
    op.drop_table("stock_bar")
    op.drop_table("stock_daily")
    op.execute("CREATE VIEW daily_series AS\n" + DAILY_SERIES_VIEW)
