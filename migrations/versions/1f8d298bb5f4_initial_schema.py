"""initial schema

Revision ID: 1f8d298bb5f4
Revises:
Create Date: 2026-08-10 10:41:13.911131

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "1f8d298bb5f4"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 시드는 마이그레이션이 넣는다. 리비전 파일에서 앱 코드를 import하지 않는다. import하면
# 나중에 수집기 Enum이 바뀔 때 과거 리비전의 결과가 따라 바뀐다.
# 수집기 Enum과 이 시드의 대조는 tests/migrations/ 가 한다.

# (provider, series_id, country, country_name, maturity_months, kind, label)
INDICATOR_SERIES_SEED: tuple[tuple[str, str, str, str, int, str, str], ...] = (
    ("fred", "DGS3MO", "US", "미국", 3, "government_bond", "미국 3개월물"),
    ("fred", "DGS2", "US", "미국", 24, "government_bond", "미국 2년물"),
    ("fred", "DGS10", "US", "미국", 120, "government_bond", "미국 10년물"),
    ("fred", "DGS30", "US", "미국", 360, "government_bond", "미국 30년물"),
    ("ecos", "KTB2Y", "KR", "한국", 24, "government_bond", "한국 2년물"),
    ("ecos", "KTB3Y", "KR", "한국", 36, "government_bond", "한국 3년물"),
    ("ecos", "KTB10Y", "KR", "한국", 120, "government_bond", "한국 10년물"),
    ("ecos", "KTB30Y", "KR", "한국", 360, "government_bond", "한국 30년물"),
    ("ecos", "CD91D", "KR", "한국", 3, "money_market", "한국 CD 91일"),
    ("mof", "JGB2Y", "JP", "일본", 24, "government_bond", "일본 2년물"),
    ("mof", "JGB5Y", "JP", "일본", 60, "government_bond", "일본 5년물"),
    ("mof", "JGB10Y", "JP", "일본", 120, "government_bond", "일본 10년물"),
    ("mof", "JGB20Y", "JP", "일본", 240, "government_bond", "일본 20년물"),
    ("mof", "JGB30Y", "JP", "일본", 360, "government_bond", "일본 30년물"),
    ("mof", "JGB40Y", "JP", "일본", 480, "government_bond", "일본 40년물"),
    ("boe", "GILT5Y", "GB", "영국", 60, "government_bond", "영국 5년물"),
    ("boe", "GILT10Y", "GB", "영국", 120, "government_bond", "영국 10년물"),
    ("boe", "GILT20Y", "GB", "영국", 240, "government_bond", "영국 20년물"),
    ("ecb", "EA3M", "XM", "유로 지역", 3, "government_bond", "유로 지역 3개월물"),
    ("ecb", "EA6M", "XM", "유로 지역", 6, "government_bond", "유로 지역 6개월물"),
    ("ecb", "EA1Y", "XM", "유로 지역", 12, "government_bond", "유로 지역 1년물"),
    ("ecb", "EA2Y", "XM", "유로 지역", 24, "government_bond", "유로 지역 2년물"),
    ("ecb", "EA3Y", "XM", "유로 지역", 36, "government_bond", "유로 지역 3년물"),
    ("ecb", "EA5Y", "XM", "유로 지역", 60, "government_bond", "유로 지역 5년물"),
    ("ecb", "EA7Y", "XM", "유로 지역", 84, "government_bond", "유로 지역 7년물"),
    ("ecb", "EA10Y", "XM", "유로 지역", 120, "government_bond", "유로 지역 10년물"),
    ("ecb", "EA15Y", "XM", "유로 지역", 180, "government_bond", "유로 지역 15년물"),
    ("ecb", "EA20Y", "XM", "유로 지역", 240, "government_bond", "유로 지역 20년물"),
    ("ecb", "EA30Y", "XM", "유로 지역", 360, "government_bond", "유로 지역 30년물"),
    ("bbk", "DE1Y", "DE", "독일", 12, "government_bond", "독일 1년물"),
    ("bbk", "DE2Y", "DE", "독일", 24, "government_bond", "독일 2년물"),
    ("bbk", "DE3Y", "DE", "독일", 36, "government_bond", "독일 3년물"),
    ("bbk", "DE5Y", "DE", "독일", 60, "government_bond", "독일 5년물"),
    ("bbk", "DE7Y", "DE", "독일", 84, "government_bond", "독일 7년물"),
    ("bbk", "DE10Y", "DE", "독일", 120, "government_bond", "독일 10년물"),
    ("bbk", "DE15Y", "DE", "독일", 180, "government_bond", "독일 15년물"),
    ("bbk", "DE20Y", "DE", "독일", 240, "government_bond", "독일 20년물"),
    ("bbk", "DE30Y", "DE", "독일", 360, "government_bond", "독일 30년물"),
    ("ecb", "FR10YM", "FR", "프랑스", 120, "government_bond", "프랑스 10년물(월평균)"),
    ("ecb", "IT10YM", "IT", "이탈리아", 120, "government_bond", "이탈리아 10년물(월평균)"),
    ("ecb", "ES10YM", "ES", "스페인", 120, "government_bond", "스페인 10년물(월평균)"),
)

# (provider, symbol, kind, country, country_name, label)
QUOTE_SYMBOL_SEED: tuple[tuple[str, str, str, str, str, str], ...] = (
    ("yahoo", "SP500_FUT", "index_future", "US", "미국", "S&P500 선물"),
    ("yahoo", "NASDAQ100_FUT", "index_future", "US", "미국", "나스닥100 선물"),
    ("yahoo", "VIX", "index", "US", "미국", "VIX 변동성 지수"),
    ("yahoo", "SOX", "index", "US", "미국", "필라델피아 반도체 지수"),
    ("kis", "KOSPI", "index", "KR", "한국", "코스피"),
    ("kis", "KOSPI200_FUT", "index_future", "KR", "한국", "코스피200 선물"),
    ("kis", "KOSPI200", "index", "KR", "한국", "코스피200"),
    ("yahoo", "NIKKEI225", "index", "JP", "일본", "닛케이225"),
    ("yahoo", "TAIEX", "index", "TW", "대만", "대만 가권지수"),
    ("yahoo", "US10Y", "rate", "US", "미국", "미국 10년물 금리"),
    ("yahoo", "USDKRW", "fx", "KR", "한국", "원/달러"),
    ("yahoo", "USDJPY", "fx", "JP", "일본", "엔/달러"),
    ("yahoo", "DXY", "fx", "US", "미국", "달러인덱스"),
    ("yahoo", "HSI", "index", "HK", "홍콩", "항셍"),
    ("yahoo", "SSE_COMP", "index", "CN", "중국", "상하이종합"),
    ("yahoo", "RUSSELL2000", "index", "US", "미국", "러셀2000"),
    ("yahoo", "USDCNH", "fx", "CN", "중국", "위안/달러(역외)"),
    ("yahoo", "JPYKRW", "fx", "KR", "한국", "원/엔"),
    ("yahoo", "US10Y_FUT", "bond_future", "US", "미국", "미 10년 국채선물"),
    ("yahoo", "GOLD", "commodity", "US", "미국", "금"),
    ("yahoo", "SILVER", "commodity", "US", "미국", "은"),
    ("yahoo", "COPPER", "commodity", "US", "미국", "구리"),
    ("yahoo", "WTI", "commodity", "US", "미국", "WTI 원유"),
    ("yahoo", "TSMC_ADR", "equity", "TW", "대만", "TSMC ADR"),
    ("kis", "KOSDAQ", "index", "KR", "한국", "코스닥"),
    ("kis", "KOSDAQ150_FUT", "index_future", "KR", "한국", "코스닥150 선물"),
    ("yahoo", "RUSSELL2000_FUT", "index_future", "US", "미국", "러셀2000 선물"),
    ("yahoo", "DOW_FUT", "index_future", "US", "미국", "다우 선물"),
    ("yahoo", "BTC", "crypto", "XX", "글로벌", "비트코인"),
    ("yahoo", "ETH", "crypto", "XX", "글로벌", "이더리움"),
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
    # ### commands auto generated by Alembic - please adjust! ###
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
        info={"database": "default", "managed": True},
    )
    op.create_index("idx_exchange_rate_currency_date", "exchange_rate", ["currency", "date"], unique=False)
    op.create_index("idx_exchange_rate_date", "exchange_rate", ["date"], unique=False)
    indicator_series = op.create_table(
        "indicator_series",
        sa.Column(
            "provider",
            sa.Text(),
            nullable=False,
            comment="데이터 제공처 식별자(예: fred 또는 ecos). indicator_observation.provider와 같은 값이다",
        ),
        sa.Column(
            "series_id",
            sa.Text(),
            nullable=False,
            comment="제공처 안에서 시계열을 가리키는 식별자. indicator_observation.series_id와 같은 값이다",
        ),
        sa.Column(
            "country",
            sa.Text(),
            nullable=False,
            comment="발행 국가(ISO 3166-1 alpha-2, 예: US 또는 KR). 유로존처럼 국가가 아닌 통화권은 XM을 쓴다",
        ),
        sa.Column(
            "country_name",
            sa.Text(),
            nullable=False,
            comment="국가 표시 이름. 국가에 붙는 속성이 더 늘면 country 마스터 테이블로 분리한다",
        ),
        sa.Column(
            "maturity_months",
            sa.Integer(),
            nullable=False,
            comment="만기 개월 수. 만기별 비교와 정렬에 쓴다(3개월=3, 10년=120). 91일물은 3으로 둔다",
        ),
        sa.Column(
            "kind",
            sa.Enum("government_bond", "money_market", name="serieskind", native_enum=False, length=20),
            nullable=False,
            comment="금리의 종류(government_bond 또는 money_market). 국채 곡선에서 단기 자금시장 금리를 가른다",
        ),
        sa.Column("label", sa.Text(), nullable=False, comment="차트와 표에 쓰는 표시 이름(예: 미국 10년물)"),
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
        sa.CheckConstraint("kind IN ('government_bond', 'money_market')", name="ck_indicator_series_kind"),
        sa.CheckConstraint("maturity_months > 0", name="ck_indicator_series_maturity_months"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "series_id", name="uq_indicator_series_natural_key"),
        comment="지표 시계열이 어느 나라 무슨 금리인지 설명하는 마스터",
        info={"database": "default", "managed": True},
    )
    op.bulk_insert(
        indicator_series,
        [
            dict(zip(("provider", "series_id", "country", "country_name", "maturity_months", "kind", "label"), row))
            for row in INDICATOR_SERIES_SEED
        ],
        # offline(`--sql`)에서는 executemany를 찍을 수 없다. 행마다 INSERT를 내게 한다.
        multiinsert=False,
    )
    op.create_table(
        "instrument",
        sa.Column("ticker", sa.Text(), nullable=False, comment="거래 시장에서 사용하는 종목 코드"),
        sa.Column(
            "market",
            sa.Enum("kospi", "kosdaq", "nyse", "nasdaq", name="market", native_enum=False, length=20),
            nullable=False,
            comment="종목이 상장된 거래 시장(kospi, kosdaq, nyse 또는 nasdaq)",
        ),
        sa.Column("name", sa.Text(), nullable=False, comment="종목 표시 이름"),
        sa.Column(
            "kind",
            sa.Enum("equity", "etf", "index", name="instrumentkind", native_enum=False, length=20),
            nullable=False,
            comment="가격 수집 소스를 가르는 유형(equity, etf 또는 index)",
        ),
        sa.Column("currency", sa.Text(), nullable=False, comment="종목 가격의 표시 통화(ISO 4217, 예: KRW 또는 USD)"),
        sa.Column(
            "source_symbol",
            sa.Text(),
            nullable=True,
            comment="수집 소스에서 쓰는 심볼. 티커와 다를 때만 채운다(예: KOSPI → ^KS11)",
        ),
        sa.Column(
            "is_watched",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
            comment="신규 데이터 수집과 분석을 수행할 추적 대상 여부",
        ),
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
        sa.CheckConstraint("kind IN ('equity', 'etf', 'index')", name="ck_instrument_kind"),
        sa.CheckConstraint("market IN ('kospi', 'kosdaq', 'nyse', 'nasdaq')", name="ck_instrument_market"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ticker", "market", name="uq_instrument_ticker_market"),
        comment="시세·뉴스·시그널이 참조하는 추적 종목 마스터",
        info={"database": "default", "managed": True},
    )
    quote_symbol = op.create_table(
        "quote_symbol",
        sa.Column(
            "provider",
            sa.Text(),
            nullable=False,
            comment="데이터 제공처 식별자(예: yahoo). quote_bar.provider와 같은 값이다",
        ),
        sa.Column(
            "symbol",
            sa.Text(),
            nullable=False,
            comment="제공처 안에서 심볼을 가리키는 식별자. quote_bar.symbol과 같은 값이다",
        ),
        sa.Column(
            "kind",
            sa.Enum(
                "index",
                "index_future",
                "fx",
                "rate",
                "bond_future",
                "commodity",
                "equity",
                "crypto",
                name="quotesymbolkind",
                native_enum=False,
                length=20,
            ),
            nullable=False,
            comment="값의 종류(index, index_future, fx, rate, bond_future, commodity, equity, crypto). 화면을 가르는 기준이다. 거래 시간대가 다르고 정상 변동폭의 자릿수도 달라 한 축에 겹치면 읽을 수 없다",
        ),
        sa.Column("country", sa.Text(), nullable=False, comment="기초 시장의 국가(ISO 3166-1 alpha-2, 예: US 또는 KR)"),
        sa.Column(
            "country_name",
            sa.Text(),
            nullable=False,
            comment="국가 표시 이름. 국가에 붙는 속성이 더 늘면 country 마스터 테이블로 분리한다",
        ),
        sa.Column("label", sa.Text(), nullable=False, comment="차트와 표에 쓰는 표시 이름(예: 나스닥100 선물)"),
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
        sa.CheckConstraint(
            "kind IN ('index', 'index_future', 'fx', 'rate', 'bond_future', 'commodity', 'equity', 'crypto')",
            name="ck_quote_symbol_kind",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "symbol", name="uq_quote_symbol_natural_key"),
        comment="quote_bar 심볼이 현물 지수인지 지수선물인지 설명하는 마스터",
        info={"database": "default", "managed": True},
    )
    op.bulk_insert(
        quote_symbol,
        [
            dict(zip(("provider", "symbol", "kind", "country", "country_name", "label"), row))
            for row in QUOTE_SYMBOL_SEED
        ],
        # offline(`--sql`)에서는 executemany를 찍을 수 없다. 행마다 INSERT를 내게 한다.
        multiinsert=False,
    )
    op.create_table(
        "source_record",
        sa.Column(
            "source_type",
            sa.Enum("api", "crawl", "websocket", name="sourcetype", native_enum=False, length=20),
            nullable=False,
            comment="수집 방식(api, crawl 또는 websocket)",
        ),
        sa.Column("source", sa.Text(), nullable=False, comment="데이터 제공처 식별자(예: fred 또는 kis)"),
        sa.Column(
            "source_key", sa.Text(), nullable=False, comment="공급자 내 원천 식별자(예: 시계열 ID, URL 또는 배치 ID)"
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, comment="수집 시작 시각(UTC)"),
        sa.Column(
            "completed_at", sa.DateTime(timezone=True), nullable=True, comment="수집 완료 시각(UTC); 진행 중이면 NULL"
        ),
        sa.Column(
            "status",
            sa.Enum("running", "succeeded", "failed", "quarantined", name="sourcestatus", native_enum=False, length=20),
            nullable=False,
            comment="수집 상태(예: running, succeeded, failed 또는 quarantined)",
        ),
        sa.Column(
            "record_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="이 수집 단위에서 생성한 정규화 레코드 수",
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="작은 JSON 원본; 저장하지 않으면 NULL",
        ),
        sa.Column("payload_uri", sa.Text(), nullable=True, comment="대용량 원본의 외부 저장 위치; 없으면 NULL"),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment="HTTP 상태나 웹소켓 세션 ID 등 공급자별 부가 정보",
        ),
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
        sa.CheckConstraint("source_type IN ('api', 'crawl', 'websocket')", name="ck_source_record_source_type"),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'quarantined')", name="ck_source_record_status"
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="API, 크롤링, 웹소켓 수집 단위의 출처와 상태를 보존하는 테이블",
        info={"database": "default", "managed": True},
    )
    op.create_index("ix_source_record_source_started_at", "source_record", ["source", "started_at"], unique=False)
    op.create_table(
        "indicator_observation",
        sa.Column(
            "provider",
            sa.Text(),
            nullable=False,
            comment="데이터 제공처 식별자(예: fred 또는 ecos). 같은 수집의 source_record.source와 같은 값이다",
        ),
        sa.Column(
            "series_id",
            sa.Text(),
            nullable=False,
            comment="제공처가 정의한 시계열 식별자(예: DGS10). 제공처 안에서만 고유하다",
        ),
        sa.Column("observation_date", sa.Date(), nullable=False, comment="지표 값의 기준일"),
        sa.Column("value", sa.Numeric(precision=18, scale=8), nullable=False, comment="정규화한 지표 값"),
        sa.Column("unit", sa.Text(), nullable=False, comment="지표 값의 단위(예: Percent)"),
        sa.Column("source_record_id", sa.BigInteger(), nullable=False, comment="근거가 되는 source_record 레코드 ID"),
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
        sa.ForeignKeyConstraint(["source_record_id"], ["source_record.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "series_id", "observation_date", name="uq_indicator_observation_natural_key"),
        comment="여러 제공처의 지표 관측값을 조회 가능한 형태로 정규화하고 원본과 연결하는 테이블",
        info={"database": "default", "managed": True},
    )
    op.create_index(
        "ix_indicator_observation_source_record_id", "indicator_observation", ["source_record_id"], unique=False
    )
    op.create_table(
        "quote_bar",
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
            comment="시세 대상 식별자(예: SP500_FUT, SOX). 제공처 안에서만 고유하다",
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
            comment="봉 구간의 거래량. 제공처가 주는 값을 그대로 저장한다. 현물 지수처럼 거래량 개념이 없는 심볼은 제공처가 0을 실어 보내므로 0이 들어간다. 즉 0은 '거래가 없었다'와 '제공처가 거래량을 주지 않는다'를 구분하지 않는다. 거래량으로 판단하는 조회가 생기면 그때 심볼 종류로 갈라 읽는다",
        ),
        sa.Column(
            "previous_close",
            sa.Numeric(precision=18, scale=8),
            nullable=False,
            comment="직전 정규장 종가. 알림이 쓰는 변동률의 분모다. 봉마다 같은 값이 반복되지만 세션 경계 계산을 피하려고 그대로 저장한다",
        ),
        sa.Column(
            "contract_code",
            sa.Text(),
            nullable=True,
            comment="선물의 실제 월물 코드(예: A01609). 현물 지수와 연속 심볼은 NULL이다. 월물이 바뀌면 가격에 갭이 생기는데, 이 값이 없으면 그 갭이 시장 급변인지 롤오버인지 구분할 수 없다",
        ),
        sa.Column("source_record_id", sa.BigInteger(), nullable=False, comment="근거가 되는 source_record 레코드 ID"),
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
        sa.ForeignKeyConstraint(["source_record_id"], ["source_record.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "symbol", "bar_at", name="uq_quote_bar_natural_key"),
        comment="지수·선물의 1분봉을 장중 알림 판단용으로 누적하는 테이블",
        info={"database": "default", "managed": True},
    )
    op.create_index("ix_quote_bar_source_record_id", "quote_bar", ["source_record_id"], unique=False)
    # ### end Alembic commands ###


def downgrade_default() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index("ix_quote_bar_source_record_id", table_name="quote_bar")
    op.drop_table("quote_bar")
    op.drop_index("ix_indicator_observation_source_record_id", table_name="indicator_observation")
    op.drop_table("indicator_observation")
    op.drop_index("ix_source_record_source_started_at", table_name="source_record")
    op.drop_table("source_record")
    op.drop_table("quote_symbol")
    op.drop_table("instrument")
    op.drop_table("indicator_series")
    op.drop_index("idx_exchange_rate_date", table_name="exchange_rate")
    op.drop_index("idx_exchange_rate_currency_date", table_name="exchange_rate")
    op.drop_table("exchange_rate")
    # ### end Alembic commands ###
