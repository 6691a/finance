"""squash schema into one revision

Revision ID: 2029012bafaa
Revises:
Create Date: 2026-08-15 13:39:51.091951

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "2029012bafaa"
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
    ("kis", "005930", "equity", "KR", "한국", "삼성전자"),
    ("kis", "000660", "equity", "KR", "한국", "SK하이닉스"),
)

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

# 수집 출처 시드. 리비전에서 앱 코드를 import하지 않는다. import하면 나중에 수집기 Enum이
# 바뀔 때 과거 리비전의 결과가 따라 바뀐다. 대조는 tests/migrations가 한다.
#
# **여기 있는 피드는 전부 실제로 요청해 응답을 확인한 것이다**(2026-08-15). 설계 초안에 있던
# Reuters와 AP는 피드 도메인이 DNS에 없어 뺐다. 재정경제부·금융위원회·BIS·U.S. Treasury·KBS는
# 알려진 주소가 404 또는 503이라 주소를 다시 찾은 뒤 넣는다.
#
# `collection_mode`는 전부 `feed_content`로 시작한다. 원문 본문 추출을 아직 만들지 않았고,
# 이용조건 확인(`terms_checked_at`)도 하지 않아 `full_text`로 올릴 근거가 없다.
#
# (slug, name, source_kind, country, language, feed_url, collection_mode, enabled)
DOCUMENT_SOURCE_SEED: tuple[tuple[str, str, str, str | None, str, str, str, bool], ...] = (
    (
        "fed",
        "Federal Reserve",
        "official",
        "US",
        "en",
        "https://www.federalreserve.gov/feeds/press_all.xml",
        "feed_content",
        True,
    ),
    (
        "bls",
        "U.S. Bureau of Labor Statistics",
        "official",
        "US",
        "en",
        "https://www.bls.gov/feed/bls_latest.rss",
        "feed_content",
        True,
    ),
    (
        "bea",
        "U.S. Bureau of Economic Analysis",
        "official",
        "US",
        "en",
        "https://apps.bea.gov/rss/rss.xml",
        "feed_content",
        True,
    ),
    (
        "bok",
        "한국은행",
        "official",
        "KR",
        "ko",
        "https://www.bok.or.kr/portal/bbs/B0000338/news.rss?menuNo=200761",
        "feed_content",
        True,
    ),
    # SEC 최신 접수 목록은 전 종목 8-K가 시간당 수십 건씩 흐른다. 거시 문서 아카이브에
    # 넣으면 나머지 출처를 덮고 이후 LLM 태깅 비용도 여기서 대부분 나간다. 국내 종목 공시는
    # `dart_disclosure_intraday`가 이미 받고 있어 지금 켤 이유가 없다. 행은 남겨 둔다.
    (
        "sec",
        "SEC EDGAR 최신 접수",
        "official",
        "US",
        "en",
        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&output=atom",
        "feed_content",
        False,
    ),
    (
        "bbc_business",
        "BBC Business",
        "media",
        "GB",
        "en",
        "https://feeds.bbci.co.uk/news/business/rss.xml",
        "feed_content",
        True,
    ),
    (
        "cnbc",
        "CNBC",
        "media",
        "US",
        "en",
        "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "feed_content",
        True,
    ),
    (
        "npr_business",
        "NPR Business",
        "media",
        "US",
        "en",
        "https://feeds.npr.org/1006/rss.xml",
        "feed_content",
        True,
    ),
    (
        "yonhap",
        "연합뉴스 경제",
        "media",
        "KR",
        "ko",
        "https://www.yna.co.kr/rss/economy.xml",
        "feed_content",
        True,
    ),
)

# 흩어져 있는 일별 시계열을 한 이름 공간으로 모은다.
#
# **이 뷰가 있는 이유는 LLM에게 줄 툴을 하나로 만들기 위해서다.** 금리는
# `indicator_observation`, 환율은 `exchange_rate`, 지수·원자재는 `quote_daily`, 종목 종가는
# `stock_investor_trade_daily`에 있다. 툴을 네 개로 나누면 모델이 어느 것을 불러야 할지부터
# 틀리고, 상관 계산도 조합마다 다른 조인을 써야 한다.
#
# `(provider, series_id)`가 좌표다. `series_id`는 제공처 안에서만 고유하므로 provider가 함께
# 들어간다. `indicator_observation`이 쓰는 규칙과 같다.
#
# 환율은 하루에 회차가 1,300건 넘게 온다. 그중 마지막 회차 하나만 그날 값으로 쓴다.
# 매매기준율이 0으로 고시되는 통화가 있어 그건 뺀다.
#
# 뷰라서 마이그레이션이 데이터를 옮기지 않는다. 원본 테이블이 갱신되면 그대로 따라온다.
DAILY_SERIES_VIEW = """
CREATE OR REPLACE VIEW daily_series AS
SELECT provider, symbol AS series_id, business_date, close AS value, 'price' AS kind
FROM quote_daily
UNION ALL
SELECT provider, series_id, observation_date AS business_date, value, 'rate' AS kind
FROM indicator_observation
UNION ALL
SELECT provider, stock_code AS series_id, business_date, close_price AS value, 'price' AS kind
FROM stock_investor_trade_daily
UNION ALL
-- 하위 질의로 감싼다. UNION 안에서 ORDER BY를 쓰면 전체 결과에 붙어 원본 컬럼 이름을
-- 찾지 못한다(`column "currency" does not exist`).
SELECT * FROM (
    SELECT DISTINCT ON (currency, date)
        'hana' AS provider, currency AS series_id, date AS business_date,
        exchange_standard_rate AS value, 'fx' AS kind
    FROM exchange_rate
    WHERE exchange_standard_rate > 0
    ORDER BY currency, date, round DESC
) AS hana_daily
"""


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
    op.create_table(
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
    op.create_table(
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
        "disclosure_event",
        sa.Column(
            "provider",
            sa.Text(),
            nullable=False,
            comment="데이터 제공처 식별자(dart). 같은 수집의 source_record.source와 같은 값이다",
        ),
        sa.Column(
            "corp_code", sa.Text(), nullable=False, comment="DART 회사 고유번호(예: 00126380). 종목코드와 다른 체계다"
        ),
        sa.Column("stock_code", sa.Text(), nullable=False, comment="한국거래소 종목코드(예: 005930)"),
        sa.Column("company_name", sa.Text(), nullable=False, comment="DART가 준 회사명 원문"),
        sa.Column(
            "rcept_no",
            sa.Text(),
            nullable=False,
            comment="DART 접수번호. 공시·원문·재무제표를 잇는 키이며 제공처 안에서 고유하다",
        ),
        sa.Column(
            "report_name",
            sa.Text(),
            nullable=False,
            comment="보고서명 원문. 정정 접두사를 포함해 손대지 않고 그대로 저장한다",
        ),
        sa.Column("filer_name", sa.Text(), nullable=False, comment="제출인 이름 원문(flr_nm)"),
        sa.Column(
            "corp_class",
            sa.Text(),
            nullable=False,
            comment="DART 법인 구분(corp_cls). Y=유가증권, K=코스닥, N=코넥스, E=기타",
        ),
        sa.Column(
            "receipt_date",
            sa.Date(),
            nullable=False,
            comment="DART 접수일(rcept_dt). 날짜뿐이고 시·분이 없다. 기준 시간대는 한국이다",
        ),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="이 접수번호를 처음 수집한 시각(UTC). 재수집해도 갱신하지 않는다. 실제 공시 시각이 아니라 최초 감지 시각이므로 화면에도 그렇게 표시한다. 2분 폴링이라 공시 시각의 상한이며 오차는 폴링 주기와 DART 반영 지연의 합이다",
        ),
        sa.Column(
            "remarks",
            sa.Text(),
            nullable=True,
            comment="DART 비고 원문(rm). 정정·철회·유가증권신고서 관련 표시가 들어온다",
        ),
        sa.Column(
            "source_record_id",
            sa.BigInteger(),
            nullable=False,
            comment="이 행을 만든 회사별 공시 목록 조회의 source_record 레코드 ID",
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
        sa.ForeignKeyConstraint(["source_record_id"], ["source_record.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "rcept_no", name="uq_disclosure_event_natural_key"),
        comment="DART 공시 접수 이벤트를 접수번호 단위로 보존하는 테이블",
        info={"database": "default", "managed": True},
    )
    op.create_index("ix_disclosure_event_source_record_id", "disclosure_event", ["source_record_id"], unique=False)
    op.create_index(
        "ix_disclosure_event_stock_code_receipt_date", "disclosure_event", ["stock_code", "receipt_date"], unique=False
    )
    op.create_table(
        "earnings_fact",
        sa.Column(
            "provider",
            sa.Text(),
            nullable=False,
            comment="데이터 제공처 식별자(dart). 같은 수집의 source_record.source와 같은 값이다",
        ),
        sa.Column("stock_code", sa.Text(), nullable=False, comment="한국거래소 종목코드(예: 005930)"),
        sa.Column(
            "rcept_no",
            sa.Text(),
            nullable=False,
            comment="숫자의 출처가 된 공시의 DART 접수번호. disclosure_event와 같은 값이지만 외래키는 걸지 않는다",
        ),
        sa.Column(
            "release_type",
            sa.Enum("provisional", "periodic", name="earningsreleasetype", native_enum=False, length=20),
            nullable=False,
            comment="숫자의 출처 종류(provisional=잠정실적 공시 원문, periodic=정기보고서 재무제표 API)",
        ),
        sa.Column(
            "period_end",
            sa.Date(),
            nullable=False,
            comment="실적 대상 기간의 종료일(예: 2026-06-30). 기준 시간대는 한국이다",
        ),
        sa.Column(
            "statement_scope",
            sa.Enum("CFS", "OFS", name="statementscope", native_enum=False, length=20),
            nullable=False,
            comment="재무제표 범위(CFS=연결, OFS=별도). 연결을 우선하고 없을 때만 별도를 저장한다",
        ),
        sa.Column(
            "amount_basis",
            sa.Enum("period", "cumulative", name="amountbasis", native_enum=False, length=20),
            nullable=False,
            comment="금액의 기간 기준(period=해당 분기·반기, cumulative=사업연도 누계)",
        ),
        sa.Column(
            "metric",
            sa.Enum("revenue", "operating_profit", "net_income", name="earningsmetric", native_enum=False, length=20),
            nullable=False,
            comment="지표(revenue=매출액, operating_profit=영업이익, net_income=당기순이익)",
        ),
        sa.Column(
            "current_amount",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="당기 금액. 원문 단위를 원 단위로 정규화한 값이며 음수와 0은 정상값이다",
        ),
        sa.Column(
            "prior_year_amount",
            sa.Numeric(precision=24, scale=2),
            nullable=True,
            comment="비교 가능한 전년 동기 금액(원). 원문에 없으면 NULL이고 0으로 바꾸지 않는다",
        ),
        sa.Column("currency", sa.Text(), nullable=False, comment="원문이 밝힌 통화(예: KRW). 임의로 환산하지 않는다"),
        sa.Column(
            "source_account_id",
            sa.Text(),
            nullable=True,
            comment="OpenDART 원계정 ID(예: ifrs-full_Revenue). 원문 표에서 읽은 잠정실적은 NULL이다",
        ),
        sa.Column(
            "source_account_name",
            sa.Text(),
            nullable=False,
            comment="원문 항목명. 어느 줄에서 읽은 숫자인지 되짚을 근거다",
        ),
        sa.Column(
            "source_record_id",
            sa.BigInteger(),
            nullable=False,
            comment="숫자를 얻은 원문 또는 재무제표 API 조회의 source_record 레코드 ID",
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
        sa.CheckConstraint("amount_basis IN ('period', 'cumulative')", name="ck_earnings_fact_amount_basis"),
        sa.CheckConstraint("metric IN ('revenue', 'operating_profit', 'net_income')", name="ck_earnings_fact_metric"),
        sa.CheckConstraint("release_type IN ('provisional', 'periodic')", name="ck_earnings_fact_release_type"),
        sa.CheckConstraint("statement_scope IN ('CFS', 'OFS')", name="ck_earnings_fact_statement_scope"),
        sa.ForeignKeyConstraint(["source_record_id"], ["source_record.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "rcept_no", "statement_scope", "amount_basis", "metric", name="uq_earnings_fact_natural_key"
        ),
        comment="DART 공시에서 추출한 실적 지표를 지표당 한 행으로 저장하는 테이블",
        info={"database": "default", "managed": True},
    )
    op.create_index("ix_earnings_fact_source_record_id", "earnings_fact", ["source_record_id"], unique=False)
    op.create_index(
        "ix_earnings_fact_stock_code_period_end", "earnings_fact", ["stock_code", "period_end"], unique=False
    )
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
        "krx_credit_balance_ranking_daily",
        sa.Column("provider", sa.Text(), nullable=False, comment="데이터 제공처 식별자(kis)"),
        sa.Column(
            "standard_date", sa.Date(), nullable=False, comment="순위 기준일(응답 stnd_date2). 둘 중 최신 날짜다"
        ),
        sa.Column(
            "comparison_date",
            sa.Date(),
            nullable=False,
            comment="증가율 비교일(응답 stnd_date1). 기준일보다 period_days 영업일 앞이다",
        ),
        sa.Column("universe_code", sa.Text(), nullable=False, comment="조회 대상 코드(FID_INPUT_ISCD). 0000은 전체다"),
        sa.Column(
            "sort_code", sa.Text(), nullable=False, comment="정렬 코드(FID_RANK_SORT_CLS_CODE). 2는 융자잔고금액 상위다"
        ),
        sa.Column("period_days", sa.Integer(), nullable=False, comment="증가율 비교 기간(FID_OPTION). 영업일 수다"),
        sa.Column(
            "rank",
            sa.Integer(),
            nullable=False,
            comment="순위. 응답에 순번 필드가 없어 배열 순서로 1부터 매긴다(실측: 순번 필드 없음)",
        ),
        sa.Column("stock_code", sa.Text(), nullable=False, comment="한국거래소 종목코드 6자리(mksc_shrn_iscd)"),
        sa.Column("stock_name", sa.Text(), nullable=False, comment="종목명 원문(hts_kor_isnm)"),
        sa.Column("close_price", sa.Numeric(precision=18, scale=4), nullable=False, comment="현재가(stck_prpr). 원"),
        sa.Column("accumulated_volume", sa.BigInteger(), nullable=False, comment="누적 거래량(acml_vol). 주"),
        sa.Column(
            "loan_balance_quantity", sa.BigInteger(), nullable=False, comment="융자 잔고 수량(whol_loan_rmnd_stcn). 주"
        ),
        sa.Column(
            "loan_balance_amount",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="융자 잔고 금액(whol_loan_rmnd_amt). 정렬 기준 값이다",
        ),
        sa.Column(
            "loan_balance_rate",
            sa.Numeric(precision=12, scale=4),
            nullable=False,
            comment="융자 잔고 비율(whol_loan_rmnd_rate). KIS 표기 그대로의 퍼센트",
        ),
        sa.Column(
            "short_loan_balance_quantity",
            sa.BigInteger(),
            nullable=False,
            comment="신용대주 잔고 수량(whol_stln_rmnd_stcn). 주",
        ),
        sa.Column(
            "short_loan_balance_amount",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="신용대주 잔고 금액(whol_stln_rmnd_amt)",
        ),
        sa.Column(
            "short_loan_balance_rate",
            sa.Numeric(precision=12, scale=4),
            nullable=False,
            comment="신용대주 잔고 비율(whol_stln_rmnd_rate). KIS 표기 그대로의 퍼센트",
        ),
        sa.Column(
            "loan_balance_growth_rate",
            sa.Numeric(precision=12, scale=4),
            nullable=False,
            comment="비교일 대비 융자잔고 증가율(nday_vrss_loan_rmnd_inrt). 변화량이 아니라 증가율이다",
        ),
        sa.Column(
            "short_loan_balance_growth_rate",
            sa.Numeric(precision=12, scale=4),
            nullable=False,
            comment="비교일 대비 신용대주잔고 증가율(nday_vrss_stln_rmnd_inrt). 변화량이 아니라 증가율이다",
        ),
        sa.Column(
            "source_record_id",
            sa.BigInteger(),
            nullable=False,
            comment="이 행을 마지막으로 갱신한 수집의 source_record 레코드 ID",
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
        sa.CheckConstraint("rank >= 1", name="ck_krx_credit_balance_ranking_daily_rank"),
        sa.ForeignKeyConstraint(["source_record_id"], ["source_record.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "standard_date",
            "universe_code",
            "sort_code",
            "period_days",
            "rank",
            name="uq_krx_credit_balance_ranking_daily_natural_key",
        ),
        comment="융자잔고금액 상위 종목의 일별 순위 스냅샷을 저장하는 테이블",
        info={"database": "default", "managed": True},
    )
    op.create_index(
        "ix_krx_credit_balance_ranking_daily_source_record_id",
        "krx_credit_balance_ranking_daily",
        ["source_record_id"],
        unique=False,
    )
    op.create_index(
        "ix_krx_credit_balance_ranking_daily_stock_code",
        "krx_credit_balance_ranking_daily",
        ["stock_code", "standard_date"],
        unique=False,
    )
    op.create_table(
        "krx_market_funds_daily",
        sa.Column("provider", sa.Text(), nullable=False, comment="데이터 제공처 식별자(kis)"),
        sa.Column(
            "business_date",
            sa.Date(),
            nullable=False,
            comment="응답이 준 영업일(bsop_date). 요청일이나 수집일을 대신 넣지 않는다",
        ),
        sa.Column(
            "index_close", sa.Numeric(precision=18, scale=4), nullable=False, comment="그날 시장지수(bstp_nmix_prpr)"
        ),
        sa.Column(
            "index_change",
            sa.Numeric(precision=18, scale=4),
            nullable=False,
            comment="지수 전일대비(bstp_nmix_prdy_vrss)",
        ),
        sa.Column(
            "market_capitalization",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="시가총액(hts_avls). 포털 표기는 백만원이며 환산하지 않는다",
        ),
        sa.Column(
            "customer_deposit",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="고객예탁금(cust_dpmn_amt). 포털 표기는 억원이며 환산하지 않는다",
        ),
        sa.Column(
            "customer_deposit_change",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="고객예탁금 전일대비(cust_dpmn_amt_prdy_vrss). 음수는 정상값이다",
        ),
        sa.Column(
            "turnover_ratio",
            sa.Numeric(precision=12, scale=4),
            nullable=False,
            comment="금액 회전율(amt_tnrt). KIS 표기 그대로의 퍼센트",
        ),
        sa.Column(
            "unsettled_amount",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="미수금(uncl_amt). 포털 표기는 억원이며 환산하지 않는다",
        ),
        sa.Column(
            "credit_loan_balance",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="신용융자 잔고(crdt_loan_rmnd). 포털 표기는 억원이며 환산하지 않는다",
        ),
        sa.Column(
            "futures_margin_amount",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="선물 관련 자금(futs_tfam_amt)",
        ),
        sa.Column(
            "equity_fund_amount",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="주식형 펀드 설정액(sttp_amt)",
        ),
        sa.Column(
            "mixed_fund_amount",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="혼합형 펀드 설정액(mxtp_amt)",
        ),
        sa.Column(
            "bond_fund_amount",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="채권형 펀드 설정액(bntp_amt)",
        ),
        sa.Column("mmf_amount", sa.Numeric(precision=24, scale=2), nullable=False, comment="MMF 설정액(mmf_amt)"),
        sa.Column(
            "securities_lending_amount",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="대차 금액(secu_lend_amt)",
        ),
        sa.Column(
            "source_record_id",
            sa.BigInteger(),
            nullable=False,
            comment="이 행을 마지막으로 갱신한 수집의 source_record 레코드 ID",
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
        sa.ForeignKeyConstraint(["source_record_id"], ["source_record.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "business_date", name="uq_krx_market_funds_daily_natural_key"),
        comment="고객예탁금·신용융자·펀드 등 국내 증시자금 종합을 영업일 단위로 누적하는 테이블",
        info={"database": "default", "managed": True},
    )
    op.create_index(
        "ix_krx_market_funds_daily_source_record_id", "krx_market_funds_daily", ["source_record_id"], unique=False
    )
    op.create_table(
        "krx_market_securities_lending_daily",
        sa.Column("provider", sa.Text(), nullable=False, comment="데이터 제공처 식별자(kis)"),
        sa.Column(
            "market_code",
            sa.Enum("KOSPI", "KOSDAQ", name="krxmarket", native_enum=False, length=20),
            nullable=False,
            comment="시장 구분(KOSPI, KOSDAQ). market_movement_snapshot.symbol과 같은 값 집합이다",
        ),
        sa.Column("business_date", sa.Date(), nullable=False, comment="영업일(bsop_date). 기준 시간대는 한국이다"),
        sa.Column(
            "index_close",
            sa.Numeric(precision=18, scale=4),
            nullable=False,
            comment="그날 시장지수 종가(stck_prpr). 종목 조회에서는 주가지만 시장 조회에서는 지수다",
        ),
        sa.Column(
            "index_change",
            sa.Numeric(precision=18, scale=4),
            nullable=False,
            comment="지수 전일대비(prdy_vrss). 음수는 정상값이다",
        ),
        sa.Column("accumulated_volume", sa.BigInteger(), nullable=False, comment="그날 시장 전체 거래량(acml_vol). 주"),
        sa.Column("new_quantity", sa.BigInteger(), nullable=False, comment="대차 신규 체결 수량(new_stcn). 주"),
        sa.Column("repayment_quantity", sa.BigInteger(), nullable=False, comment="대차 상환 수량(rdmp_stcn). 주"),
        sa.Column(
            "balance_change_quantity",
            sa.BigInteger(),
            nullable=False,
            comment="전일대비 잔고 증감 수량(prdy_rmnd_vrss). 음수는 정상값이다",
        ),
        sa.Column(
            "balance_quantity", sa.BigInteger(), nullable=False, comment="시장 전체 대차 잔고 수량(rmnd_stcn). 주"
        ),
        sa.Column(
            "balance_amount",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="시장 전체 대차 잔고 금액(rmnd_amt). **백만원 단위다**(종목 대차와 같은 표기)",
        ),
        sa.Column(
            "source_record_id",
            sa.BigInteger(),
            nullable=False,
            comment="이 행을 마지막으로 갱신한 수집의 source_record 레코드 ID",
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
        sa.CheckConstraint(
            "market_code IN ('KOSPI', 'KOSDAQ')", name="ck_krx_market_securities_lending_daily_market_code"
        ),
        sa.ForeignKeyConstraint(["source_record_id"], ["source_record.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "market_code", "business_date", name="uq_krx_market_securities_lending_daily_natural_key"
        ),
        comment="코스피·코스닥 시장 전체의 대차거래 신규·상환·잔고를 영업일 단위로 누적하는 테이블",
        info={"database": "default", "managed": True},
    )
    op.create_index(
        "ix_krx_market_securities_lending_daily_source_record_id",
        "krx_market_securities_lending_daily",
        ["source_record_id"],
        unique=False,
    )
    op.create_table(
        "krx_stock_credit_balance_daily",
        sa.Column(
            "provider",
            sa.Text(),
            nullable=False,
            comment="데이터 제공처 식별자(kis). 같은 수집의 source_record.source와 같은 값이다",
        ),
        sa.Column(
            "stock_code",
            sa.Text(),
            nullable=False,
            comment="한국거래소 종목코드 6자리(예: 005930). disclosure_event.stock_code와 같은 체계다",
        ),
        sa.Column(
            "trade_date", sa.Date(), nullable=False, comment="값이 만들어진 거래일(deal_date). 기준 시간대는 한국이다"
        ),
        sa.Column(
            "settlement_date",
            sa.Date(),
            nullable=False,
            comment="결제일(stlm_date). 거래일보다 통상 2영업일 뒤다(실측)",
        ),
        sa.Column(
            "close_price", sa.Numeric(precision=18, scale=4), nullable=False, comment="그 거래일 종가(stck_prpr). 원"
        ),
        sa.Column("accumulated_volume", sa.BigInteger(), nullable=False, comment="그 거래일 누적 거래량(acml_vol). 주"),
        sa.Column(
            "loan_new_quantity", sa.BigInteger(), nullable=False, comment="융자 신규 수량(whol_loan_new_stcn). 주"
        ),
        sa.Column(
            "loan_repayment_quantity",
            sa.BigInteger(),
            nullable=False,
            comment="융자 상환 수량(whol_loan_rdmp_stcn). 주",
        ),
        sa.Column(
            "loan_balance_quantity", sa.BigInteger(), nullable=False, comment="융자 잔고 수량(whol_loan_rmnd_stcn). 주"
        ),
        sa.Column(
            "loan_new_amount",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="융자 신규 금액(whol_loan_new_amt). KIS 표기 그대로 저장한다",
        ),
        sa.Column(
            "loan_repayment_amount",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="융자 상환 금액(whol_loan_rdmp_amt). KIS 표기 그대로 저장한다",
        ),
        sa.Column(
            "loan_balance_amount",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="융자 잔고 금액(whol_loan_rmnd_amt). KIS 표기 그대로 저장한다",
        ),
        sa.Column(
            "loan_balance_rate",
            sa.Numeric(precision=12, scale=4),
            nullable=False,
            comment="융자 잔고 비율(whol_loan_rmnd_rate). KIS 표기 그대로의 퍼센트",
        ),
        sa.Column(
            "loan_supply_rate",
            sa.Numeric(precision=12, scale=4),
            nullable=False,
            comment="융자 공여율(whol_loan_gvrt). KIS 표기 그대로의 퍼센트",
        ),
        sa.Column(
            "short_loan_new_quantity",
            sa.BigInteger(),
            nullable=False,
            comment="신용대주 신규 수량(whol_stln_new_stcn). 주",
        ),
        sa.Column(
            "short_loan_repayment_quantity",
            sa.BigInteger(),
            nullable=False,
            comment="신용대주 상환 수량(whol_stln_rdmp_stcn). 주",
        ),
        sa.Column(
            "short_loan_balance_quantity",
            sa.BigInteger(),
            nullable=False,
            comment="신용대주 잔고 수량(whol_stln_rmnd_stcn). 주",
        ),
        sa.Column(
            "short_loan_new_amount",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="신용대주 신규 금액(whol_stln_new_amt). KIS 표기 그대로 저장한다",
        ),
        sa.Column(
            "short_loan_repayment_amount",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="신용대주 상환 금액(whol_stln_rdmp_amt). KIS 표기 그대로 저장한다",
        ),
        sa.Column(
            "short_loan_balance_amount",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="신용대주 잔고 금액(whol_stln_rmnd_amt). KIS 표기 그대로 저장한다",
        ),
        sa.Column(
            "short_loan_balance_rate",
            sa.Numeric(precision=12, scale=4),
            nullable=False,
            comment="신용대주 잔고 비율(whol_stln_rmnd_rate). KIS 표기 그대로의 퍼센트",
        ),
        sa.Column(
            "short_loan_supply_rate",
            sa.Numeric(precision=12, scale=4),
            nullable=False,
            comment="신용대주 공여율(whol_stln_gvrt). KIS 표기 그대로의 퍼센트",
        ),
        sa.Column(
            "source_record_id",
            sa.BigInteger(),
            nullable=False,
            comment="이 행을 마지막으로 갱신한 수집의 source_record 레코드 ID",
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
        sa.ForeignKeyConstraint(["source_record_id"], ["source_record.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "stock_code", "trade_date", name="uq_krx_stock_credit_balance_daily_natural_key"
        ),
        comment="종목별 신용잔고(융자·신용대주) 일별추이를 거래일 기준으로 누적하는 테이블",
        info={"database": "default", "managed": True},
    )
    op.create_index(
        "ix_krx_stock_credit_balance_daily_source_record_id",
        "krx_stock_credit_balance_daily",
        ["source_record_id"],
        unique=False,
    )
    op.create_table(
        "krx_stock_securities_lending_daily",
        sa.Column("provider", sa.Text(), nullable=False, comment="데이터 제공처 식별자(kis)"),
        sa.Column("stock_code", sa.Text(), nullable=False, comment="한국거래소 종목코드 6자리(예: 005930)"),
        sa.Column("business_date", sa.Date(), nullable=False, comment="영업일(bsop_date). 기준 시간대는 한국이다"),
        sa.Column("close_price", sa.Numeric(precision=18, scale=4), nullable=False, comment="그날 종가(stck_prpr). 원"),
        sa.Column(
            "price_change",
            sa.Numeric(precision=18, scale=4),
            nullable=False,
            comment="전일대비 가격(prdy_vrss). 음수는 정상값이다",
        ),
        sa.Column("accumulated_volume", sa.BigInteger(), nullable=False, comment="그날 누적 거래량(acml_vol). 주"),
        sa.Column("new_quantity", sa.BigInteger(), nullable=False, comment="대차 신규 체결 수량(new_stcn). 주"),
        sa.Column("repayment_quantity", sa.BigInteger(), nullable=False, comment="대차 상환 수량(rdmp_stcn). 주"),
        sa.Column(
            "balance_change_quantity",
            sa.BigInteger(),
            nullable=False,
            comment="전일대비 잔고 증감 수량(prdy_rmnd_vrss). 음수는 정상값이다",
        ),
        sa.Column("balance_quantity", sa.BigInteger(), nullable=False, comment="대차 잔고 수량(rmnd_stcn). 주"),
        sa.Column(
            "balance_amount",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="대차 잔고 금액(rmnd_amt). **백만원 단위다**(실측: 잔고수량×종가의 1/1,000,000)",
        ),
        sa.Column(
            "source_record_id",
            sa.BigInteger(),
            nullable=False,
            comment="이 행을 마지막으로 갱신한 수집의 source_record 레코드 ID",
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
        sa.ForeignKeyConstraint(["source_record_id"], ["source_record.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "stock_code", "business_date", name="uq_krx_stock_securities_lending_daily_natural_key"
        ),
        comment="종목별 대차거래 신규·상환·잔고를 영업일 단위로 누적하는 테이블",
        info={"database": "default", "managed": True},
    )
    op.create_index(
        "ix_krx_stock_securities_lending_daily_source_record_id",
        "krx_stock_securities_lending_daily",
        ["source_record_id"],
        unique=False,
    )
    op.create_table(
        "krx_stock_short_sale_daily",
        sa.Column("provider", sa.Text(), nullable=False, comment="데이터 제공처 식별자(kis)"),
        sa.Column("stock_code", sa.Text(), nullable=False, comment="한국거래소 종목코드 6자리(예: 005930)"),
        sa.Column("business_date", sa.Date(), nullable=False, comment="영업일(stck_bsop_date). 기준 시간대는 한국이다"),
        sa.Column("close_price", sa.Numeric(precision=18, scale=4), nullable=False, comment="그날 종가(stck_clpr). 원"),
        sa.Column("accumulated_volume", sa.BigInteger(), nullable=False, comment="그날 누적 거래량(acml_vol). 주"),
        sa.Column(
            "short_sale_quantity", sa.BigInteger(), nullable=False, comment="그날 공매도 체결수량(ssts_cntg_qty). 주"
        ),
        sa.Column(
            "short_sale_volume_ratio",
            sa.Numeric(precision=12, scale=4),
            nullable=False,
            comment="공매도 거래량 비중(ssts_vol_rlim). KIS 표기 그대로의 퍼센트",
        ),
        sa.Column(
            "accumulated_short_sale_quantity",
            sa.BigInteger(),
            nullable=False,
            comment="누적 공매도 수량(acml_ssts_cntg_qty). 주",
        ),
        sa.Column(
            "accumulated_short_sale_volume_ratio",
            sa.Numeric(precision=12, scale=4),
            nullable=False,
            comment="누적 공매도 거래량 비중(acml_ssts_cntg_qty_rlim). 퍼센트",
        ),
        sa.Column(
            "short_sale_amount",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="그날 공매도 거래대금(ssts_tr_pbmn). **원 단위다**(실측: 수량×종가와 거의 같다)",
        ),
        sa.Column(
            "short_sale_amount_ratio",
            sa.Numeric(precision=12, scale=4),
            nullable=False,
            comment="공매도 거래대금 비중(ssts_tr_pbmn_rlim). 퍼센트",
        ),
        sa.Column(
            "accumulated_short_sale_amount",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="누적 공매도 거래대금(acml_ssts_tr_pbmn). 원",
        ),
        sa.Column(
            "accumulated_short_sale_amount_ratio",
            sa.Numeric(precision=12, scale=4),
            nullable=False,
            comment="누적 공매도 거래대금 비중(acml_ssts_tr_pbmn_rlim). 퍼센트",
        ),
        sa.Column(
            "total_amount",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="그날 전체 거래대금(acml_tr_pbmn). 원",
        ),
        sa.Column(
            "short_sale_average_price",
            sa.Numeric(precision=18, scale=4),
            nullable=False,
            comment="공매도 평균가(avrg_prc). 원",
        ),
        sa.Column(
            "source_record_id",
            sa.BigInteger(),
            nullable=False,
            comment="이 행을 마지막으로 갱신한 수집의 source_record 레코드 ID",
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
        sa.ForeignKeyConstraint(["source_record_id"], ["source_record.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "stock_code", "business_date", name="uq_krx_stock_short_sale_daily_natural_key"
        ),
        comment="종목별 공매도 체결수량·거래대금과 그 비중을 영업일 단위로 누적하는 테이블",
        info={"database": "default", "managed": True},
    )
    op.create_index(
        "ix_krx_stock_short_sale_daily_source_record_id",
        "krx_stock_short_sale_daily",
        ["source_record_id"],
        unique=False,
    )
    op.create_table(
        "market_investor_flow_snapshot",
        sa.Column("provider", sa.Text(), nullable=False, comment="데이터 제공처 식별자(kis)"),
        sa.Column(
            "market_code",
            sa.Enum("KOSPI", "KOSDAQ", name="krxmarket", native_enum=False, length=20),
            nullable=False,
            comment="시장 구분(KOSPI, KOSDAQ). 코스닥 조회 코드는 아직 확인하지 못해 KOSPI만 채워진다",
        ),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="관측이 속한 1분의 시작 시각(UTC). 응답에 원천 시각이 없어 수집 시각을 절삭한 값이다",
        ),
        sa.Column(
            "foreign_sell_qty",
            sa.BigInteger(),
            nullable=False,
            comment="외국인 누적 매도 수량(frgn_seln_vol). 단위 미확정이라 환산하지 않는다",
        ),
        sa.Column("foreign_buy_qty", sa.BigInteger(), nullable=False, comment="외국인 누적 매수 수량(frgn_shnu_vol)"),
        sa.Column(
            "foreign_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="외국인 순매수 수량(frgn_ntby_qty). 매수-매도와 일치하는지 검증한다",
        ),
        sa.Column(
            "foreign_net_buy_amount",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="외국인 순매수 대금(frgn_ntby_tr_pbmn). 단위 미확정",
        ),
        sa.Column(
            "institution_sell_qty", sa.BigInteger(), nullable=False, comment="기관 누적 매도 수량(orgn_seln_vol)"
        ),
        sa.Column("institution_buy_qty", sa.BigInteger(), nullable=False, comment="기관 누적 매수 수량(orgn_shnu_vol)"),
        sa.Column(
            "institution_net_buy_qty", sa.BigInteger(), nullable=False, comment="기관 순매수 수량(orgn_ntby_qty)"
        ),
        sa.Column(
            "institution_net_buy_amount",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="기관 순매수 대금(orgn_ntby_tr_pbmn). 단위 미확정",
        ),
        sa.Column("individual_sell_qty", sa.BigInteger(), nullable=False, comment="개인 누적 매도 수량(prsn_seln_vol)"),
        sa.Column("individual_buy_qty", sa.BigInteger(), nullable=False, comment="개인 누적 매수 수량(prsn_shnu_vol)"),
        sa.Column("individual_net_buy_qty", sa.BigInteger(), nullable=False, comment="개인 순매수 수량(prsn_ntby_qty)"),
        sa.Column(
            "individual_net_buy_amount",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="개인 순매수 대금(prsn_ntby_tr_pbmn). 단위 미확정",
        ),
        sa.Column(
            "securities_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="금융투자 순매수 수량(scrt_ntby_qty). 기관계의 부분집합이다",
        ),
        sa.Column(
            "investment_trust_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="투자신탁 순매수 수량(ivtr_ntby_qty). 기관계의 부분집합이다",
        ),
        sa.Column(
            "private_equity_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="사모펀드 순매수 수량(pe_fund_ntby_vol). 이 분류만 접미사가 _vol이다",
        ),
        sa.Column(
            "bank_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="은행 순매수 수량(bank_ntby_qty). 기관계의 부분집합이다",
        ),
        sa.Column(
            "insurance_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="보험 순매수 수량(insu_ntby_qty). 기관계의 부분집합이다",
        ),
        sa.Column(
            "merchant_bank_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="종금 순매수 수량(mrbn_ntby_qty). 기관계의 부분집합이다",
        ),
        sa.Column(
            "pension_fund_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="기금 순매수 수량(fund_ntby_qty). 기관계의 부분집합이다",
        ),
        sa.Column(
            "other_corporation_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="기타법인 순매수 수량(etc_corp_ntby_vol). 기관계 밖이며 접미사가 _vol이다",
        ),
        sa.Column(
            "other_organization_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="기타단체 순매수 수량(etc_orgt_ntby_vol). 기관계 밖이며 접미사가 _vol이다",
        ),
        sa.Column(
            "source_record_id",
            sa.BigInteger(),
            nullable=False,
            comment="이 행을 마지막으로 갱신한 수집의 source_record 레코드 ID",
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
        sa.CheckConstraint("market_code IN ('KOSPI', 'KOSDAQ')", name="ck_market_investor_flow_snapshot_market_code"),
        sa.ForeignKeyConstraint(["source_record_id"], ["source_record.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "market_code", "observed_at", name="uq_market_investor_flow_snapshot_natural_key"
        ),
        comment="시장별 외국인·기관·개인의 장중 누적 매매동향을 분 단위로 누적하는 테이블",
        info={"database": "default", "managed": True},
    )
    op.create_index(
        "ix_market_investor_flow_snapshot_source_record_id",
        "market_investor_flow_snapshot",
        ["source_record_id"],
        unique=False,
    )
    op.create_table(
        "market_movement_snapshot",
        sa.Column(
            "provider",
            sa.Text(),
            nullable=False,
            comment="데이터 제공처 식별자(kis). 같은 수집의 source_record.source와 같은 값이다",
        ),
        sa.Column(
            "symbol",
            sa.Enum("KOSPI", "KOSDAQ", name="krxmarket", native_enum=False, length=20),
            nullable=False,
            comment="분포를 고시한 지수(KOSPI, KOSDAQ). quote_bar.symbol과 같은 값이다",
        ),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="관측이 속한 1분의 시작 시각(UTC). REST는 응답을 받은 시각을 분 단위로 절삭한 값이라 제공처가 준 원천 시각이 아니다. 과거 분포를 복구하는 백필 값으로 쓰지 않는다",
        ),
        sa.Column(
            "upper_limit_count",
            sa.Integer(),
            nullable=False,
            comment="상한가 종목 수. 상승 종목 수 안에 포함된 부분집합이다(실측). 강조 표시용으로 따로 보존한다",
        ),
        sa.Column(
            "rising_count",
            sa.Integer(),
            nullable=False,
            comment="상승 종목 수. 상한가를 포함한다. 보합·하락과 더하면 그날 거래 종목 수가 된다",
        ),
        sa.Column("unchanged_count", sa.Integer(), nullable=False, comment="보합 종목 수"),
        sa.Column("falling_count", sa.Integer(), nullable=False, comment="하락 종목 수"),
        sa.Column(
            "lower_limit_count",
            sa.Integer(),
            nullable=False,
            comment="하한가 종목 수. 하락 종목 수에 포함되는지는 아직 확인하지 못했다(관측 내내 0이었다)",
        ),
        sa.Column(
            "source_record_id",
            sa.BigInteger(),
            nullable=False,
            comment="이 행을 마지막으로 갱신한 수집의 source_record 레코드 ID",
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
        sa.CheckConstraint("symbol IN ('KOSPI', 'KOSDAQ')", name="ck_market_movement_snapshot_symbol"),
        sa.CheckConstraint(
            "upper_limit_count >= 0 AND rising_count >= 0 AND unchanged_count >= 0 AND falling_count >= 0 AND lower_limit_count >= 0",
            name="ck_market_movement_snapshot_counts_not_negative",
        ),
        sa.ForeignKeyConstraint(["source_record_id"], ["source_record.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "symbol", "observed_at", name="uq_market_movement_snapshot_natural_key"),
        comment="코스피·코스닥의 상승·보합·하락 종목 수를 분 단위로 누적하는 테이블",
        info={"database": "default", "managed": True},
    )
    op.create_index(
        "ix_market_movement_snapshot_source_record_id", "market_movement_snapshot", ["source_record_id"], unique=False
    )
    op.create_table(
        "market_session",
        sa.Column(
            "market_code",
            sa.Enum("KRX", "US_EQUITY", name="marketcode", native_enum=False, length=20),
            nullable=False,
            comment="휴장 캘린더를 공유하는 시장 묶음(KRX, US_EQUITY). 상장 거래소를 뜻하는 Market enum과는 다른 체계다",
        ),
        sa.Column("market_name", sa.Text(), nullable=False, comment="사람이 읽는 시장 이름(예: 한국거래소)"),
        sa.Column(
            "country_code", sa.Text(), nullable=False, comment="시장이 속한 국가(ISO 3166-1 alpha-2, 예: KR 또는 US)"
        ),
        sa.Column(
            "session_date", sa.Date(), nullable=False, comment="그 시장의 현지 거래일. 시간대는 시장 현지 기준이다"
        ),
        sa.Column(
            "kis_weekday_code",
            sa.Text(),
            nullable=True,
            comment="국내 KIS 요일구분코드(wday_dvsn_cd). 미국 행은 NULL이다",
        ),
        sa.Column(
            "kis_business_day", sa.Boolean(), nullable=True, comment="국내 KIS 영업일 여부(bzdy_yn). 미국 행은 NULL이다"
        ),
        sa.Column(
            "kis_trading_day",
            sa.Boolean(),
            nullable=True,
            comment="국내 KIS 거래일 여부(tr_day_yn). 미국 행은 NULL이다",
        ),
        sa.Column(
            "kis_open_day",
            sa.Boolean(),
            nullable=True,
            comment="국내 KIS 개장일 여부(opnd_yn). 주문 가능 여부의 원본이다. 미국 행은 NULL이다",
        ),
        sa.Column(
            "kis_settlement_day",
            sa.Boolean(),
            nullable=True,
            comment="국내 KIS 결제일 여부(sttl_day_yn). 미국 행은 NULL이다",
        ),
        sa.Column(
            "local_settlement_date",
            sa.Date(),
            nullable=True,
            comment="해외 KIS 현지결제일자(acpl_sttl_dt). 국내 행은 NULL이다",
        ),
        sa.Column(
            "domestic_settlement_date",
            sa.Date(),
            nullable=True,
            comment="해외 KIS 국내결제일자(dmst_sttl_dt). 국내 행은 NULL이다",
        ),
        sa.Column(
            "effective_open_day",
            sa.Boolean(),
            nullable=True,
            comment="소비자가 쓰는 최종 개장일 판정. 조기 폐장일은 true다. 아직 판정하지 못한 날짜는 NULL이고, 이때 수집기는 시세 요청을 멈추지 않는다",
        ),
        sa.Column(
            "verified_by",
            sa.Enum("kis", "nyse", name="sessionverifier", native_enum=False, length=20),
            nullable=True,
            comment="effective_open_day를 채운 제공처(kis, nyse)",
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True, comment="최종 판정을 확인한 시각(UTC)"),
        sa.Column(
            "source_record_id",
            sa.BigInteger(),
            nullable=False,
            comment="이 행을 만든 수집의 source_record 레코드 ID. 국내는 KIS, 미국은 NYSE 수집이다",
        ),
        sa.Column(
            "verification_source_record_id",
            sa.BigInteger(),
            nullable=True,
            comment="같은 행을 보강한 다른 출처의 source_record 레코드 ID. 미국 행의 결제일을 채운 KIS 해외 수집이다",
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
        sa.CheckConstraint("market_code IN ('KRX', 'US_EQUITY')", name="ck_market_session_market_code"),
        sa.CheckConstraint("verified_by IN ('kis', 'nyse')", name="ck_market_session_verified_by"),
        sa.ForeignKeyConstraint(["source_record_id"], ["source_record.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["verification_source_record_id"], ["source_record.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("market_code", "session_date", name="uq_market_session_natural_key"),
        comment="시장별·날짜별 개장 여부와 결제일을 저장하는 테이블",
        info={"database": "default", "managed": True},
    )
    op.create_index("ix_market_session_session_date", "market_session", ["session_date"], unique=False)
    op.create_index("ix_market_session_source_record_id", "market_session", ["source_record_id"], unique=False)
    op.create_index(
        "ix_market_session_verification_source_record_id",
        "market_session",
        ["verification_source_record_id"],
        unique=False,
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
    op.create_table(
        "stock_investor_estimate_snapshot",
        sa.Column("provider", sa.Text(), nullable=False, comment="데이터 제공처 식별자(kis)"),
        sa.Column(
            "stock_code",
            sa.Text(),
            nullable=False,
            comment="한국거래소 종목코드 6자리(예: 005930). disclosure_event.stock_code와 같은 체계다",
        ),
        sa.Column(
            "business_date",
            sa.Date(),
            nullable=False,
            comment="이 값이 속한 거래일(KST). 응답에 날짜가 없어 수집 시각의 KST 날짜를 쓴다",
        ),
        sa.Column(
            "source_time_code",
            sa.Text(),
            nullable=False,
            comment="응답의 갱신 슬롯 코드(bsop_hour_gb). 시각이 아니라 코드이며 환산하지 않는다",
        ),
        sa.Column(
            "foreign_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="외국인 추정 순매수 수량(frgn_fake_ntby_qty). 음수는 정상값이다",
        ),
        sa.Column(
            "institution_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="기관 추정 순매수 수량(orgn_fake_ntby_qty). 음수는 정상값이다",
        ),
        sa.Column(
            "total_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="합계 추정 순매수 수량(sum_fake_ntby_qty). 외국인+기관과 다르면 수집기가 실패시킨다",
        ),
        sa.Column(
            "collected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="이 슬롯 값을 받은 시각(UTC). 자연키가 아니라 값이며 재수집하면 갱신된다",
        ),
        sa.Column(
            "source_record_id",
            sa.BigInteger(),
            nullable=False,
            comment="이 행을 마지막으로 갱신한 수집의 source_record 레코드 ID",
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
        sa.ForeignKeyConstraint(["source_record_id"], ["source_record.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "stock_code",
            "business_date",
            "source_time_code",
            name="uq_stock_investor_estimate_snapshot_natural_key",
        ),
        comment="종목별 외국인·기관 추정 순매수를 갱신 슬롯 단위로 누적하는 테이블",
        info={"database": "default", "managed": True},
    )
    op.create_index(
        "ix_stock_investor_estimate_snapshot_source_record_id",
        "stock_investor_estimate_snapshot",
        ["source_record_id"],
        unique=False,
    )
    op.create_table(
        "stock_investor_trade_daily",
        sa.Column("provider", sa.Text(), nullable=False, comment="데이터 제공처 식별자(kis)"),
        sa.Column(
            "stock_code",
            sa.Text(),
            nullable=False,
            comment="6자리 종목코드(005930, 000660). 종목 이름은 instrument 마스터가 갖는다",
        ),
        sa.Column(
            "business_date",
            sa.Date(),
            nullable=False,
            comment="거래일(stck_bsop_date). KRX 영업일 기준이며 시각은 담지 않는다",
        ),
        sa.Column(
            "open_price", sa.Numeric(precision=18, scale=4), nullable=False, comment="시가(stck_oprc). 단위는 원"
        ),
        sa.Column(
            "high_price", sa.Numeric(precision=18, scale=4), nullable=False, comment="고가(stck_hgpr). 단위는 원"
        ),
        sa.Column("low_price", sa.Numeric(precision=18, scale=4), nullable=False, comment="저가(stck_lwpr). 단위는 원"),
        sa.Column(
            "close_price",
            sa.Numeric(precision=18, scale=4),
            nullable=False,
            comment="종가(stck_clpr). 단위는 원. 수급과 가격을 한 화면에서 겹치려고 저장한다",
        ),
        sa.Column("accumulated_volume", sa.BigInteger(), nullable=False, comment="누적 거래량(acml_vol). 단위는 주"),
        sa.Column(
            "accumulated_trade_amount",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="누적 거래대금(acml_tr_pbmn). **단위는 원이다.** 투자자별 대금만 백만원이라 섞어 쓰면 안 된다",
        ),
        sa.Column(
            "foreign_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="외국인 순매수 수량(frgn_ntby_qty). 단위는 주. 등록+미등록과 일치하는지 검증한다",
        ),
        sa.Column(
            "foreign_registered_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="외국인 등록분 순매수 수량(frgn_reg_ntby_qty). 단위는 주",
        ),
        sa.Column(
            "foreign_unregistered_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="외국인 미등록분 순매수 수량(frgn_nreg_ntby_qty). 단위는 주",
        ),
        sa.Column(
            "individual_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="개인 순매수 수량(prsn_ntby_qty). 단위는 주. 장중 추정 API에는 없는 값이다",
        ),
        sa.Column(
            "institution_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="기관계 순매수 수량(orgn_ntby_qty). 단위는 주. 세부 일곱의 합과 일치하는지 검증한다",
        ),
        sa.Column(
            "securities_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="금융투자 순매수 수량(scrt_ntby_qty). 기관계의 부분집합이다",
        ),
        sa.Column(
            "investment_trust_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="투자신탁 순매수 수량(ivtr_ntby_qty). 기관계의 부분집합이다",
        ),
        sa.Column(
            "private_equity_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="사모펀드 순매수 수량(pe_fund_ntby_vol). 이 분류만 접미사가 _vol이다",
        ),
        sa.Column(
            "bank_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="은행 순매수 수량(bank_ntby_qty). 기관계의 부분집합이다",
        ),
        sa.Column(
            "insurance_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="보험 순매수 수량(insu_ntby_qty). 기관계의 부분집합이다",
        ),
        sa.Column(
            "merchant_bank_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="종금 순매수 수량(mrbn_ntby_qty). 기관계의 부분집합이다",
        ),
        sa.Column(
            "pension_fund_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="기금 순매수 수량(fund_ntby_qty). 기관계의 부분집합이다",
        ),
        sa.Column(
            "other_corporation_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="기타법인 순매수 수량(etc_corp_ntby_vol). 기관계 밖이며 접미사가 _vol이다",
        ),
        sa.Column(
            "other_organization_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="기타단체 순매수 수량(etc_orgt_ntby_vol). 기관계 밖이며 접미사가 _vol이다",
        ),
        sa.Column(
            "foreign_net_buy_amount",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="외국인 순매수 대금(frgn_ntby_tr_pbmn). **단위는 백만원이다**",
        ),
        sa.Column(
            "institution_net_buy_amount",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="기관계 순매수 대금(orgn_ntby_tr_pbmn). 단위는 백만원",
        ),
        sa.Column(
            "individual_net_buy_amount",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="개인 순매수 대금(prsn_ntby_tr_pbmn). 단위는 백만원",
        ),
        sa.Column(
            "source_record_id",
            sa.BigInteger(),
            nullable=False,
            comment="이 행을 마지막으로 갱신한 수집의 source_record 레코드 ID",
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
        sa.ForeignKeyConstraint(["source_record_id"], ["source_record.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "stock_code", "business_date", name="uq_stock_investor_trade_daily_natural_key"
        ),
        comment="종목별 투자자 매매동향의 장 마감 뒤 확정 일별값을 누적하는 테이블",
        info={"database": "default", "managed": True},
    )
    op.create_index(
        "ix_stock_investor_trade_daily_business_date", "stock_investor_trade_daily", ["business_date"], unique=False
    )
    op.create_index(
        "ix_stock_investor_trade_daily_source_record_id",
        "stock_investor_trade_daily",
        ["source_record_id"],
        unique=False,
    )

    # 시드. 테이블이 모두 만들어진 뒤에 넣는다.
    op.bulk_insert(
        sa.table(
            "indicator_series",
            sa.column("provider", sa.Text),
            sa.column("series_id", sa.Text),
            sa.column("country", sa.Text),
            sa.column("country_name", sa.Text),
            sa.column("maturity_months", sa.Integer),
            sa.column("kind", sa.Text),
            sa.column("label", sa.Text),
        ),
        [
            dict(zip(("provider", "series_id", "country", "country_name", "maturity_months", "kind", "label"), row))
            for row in INDICATOR_SERIES_SEED
        ],
        # offline(`--sql`)에서는 executemany를 찍을 수 없다. 행마다 INSERT를 내게 한다.
        multiinsert=False,
    )
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
            dict(zip(("provider", "symbol", "kind", "country", "country_name", "label"), row))
            for row in QUOTE_SYMBOL_SEED
        ],
        multiinsert=False,
    )
    op.bulk_insert(
        sa.table(
            "instrument",
            sa.column("ticker", sa.Text),
            sa.column("market", sa.String),
            sa.column("name", sa.Text),
            sa.column("kind", sa.String),
            sa.column("currency", sa.Text),
            sa.column("is_watched", sa.Boolean),
        ),
        [dict(row) for row in INSTRUMENTS],
        multiinsert=False,
    )

    op.create_table(
        "quote_daily",
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
        sa.UniqueConstraint("provider", "symbol", "business_date", name="uq_quote_daily_natural_key"),
        comment="지수·선물·환율의 일봉을 상관 분석용으로 누적하는 테이블",
        info={"database": "default", "managed": True},
    )
    op.create_index("ix_quote_daily_business_date", "quote_daily", ["business_date"], unique=False)
    op.create_index("ix_quote_daily_source_record_id", "quote_daily", ["source_record_id"], unique=False)

    op.create_table(
        "document_source",
        sa.Column(
            "slug",
            sa.Text(),
            nullable=False,
            comment="출처 식별자(예: fed, yonhap). 수집기 Enum과 같은 값이며 document.source_slug가 이 값을 쓴다",
        ),
        sa.Column("name", sa.Text(), nullable=False, comment="출처 표시 이름"),
        sa.Column(
            "source_kind",
            sa.Enum("official", "media", name="sourcekind", native_enum=False, length=20),
            nullable=False,
            comment="출처 종류(official 또는 media). 공식기관 문서는 가치 점수와 무관하게 보관한다",
        ),
        sa.Column(
            "country", sa.Text(), nullable=True, comment="출처 국가(ISO 3166-1 alpha-2). BIS처럼 국제기구는 NULL이다"
        ),
        sa.Column(
            "language",
            sa.Text(),
            nullable=False,
            comment=(
                "이 출처가 쓰는 언어(ISO 639-1). 국가에서 추측하지 않고 출처마다 선언한다. "
                "한 출처가 여러 언어를 내보내면 그때 문서 단위 판별을 붙인다"
            ),
        ),
        sa.Column(
            "feed_url", sa.Text(), nullable=False, comment="발견 채널 URL(RSS 또는 Atom). 인증이 없어 그대로 저장한다"
        ),
        sa.Column(
            "collection_mode",
            sa.Enum("metadata_only", "feed_content", "full_text", name="collectionmode", native_enum=False, length=20),
            nullable=False,
            comment="어디까지 저장할지. metadata_only는 제목·URL만, feed_content는 피드가 준 요약까지, full_text는 원문 본문까지다. 이용조건에서 개인 자동수집이 확인된 출처만 full_text로 올린다",
        ),
        sa.Column("terms_url", sa.Text(), nullable=True, comment="이용조건 문서 URL"),
        sa.Column(
            "terms_checked_at",
            sa.Date(),
            nullable=True,
            comment="이용조건을 마지막으로 확인한 날짜(KST). collection_mode를 정한 근거 시점이다",
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            server_default="true",
            nullable=False,
            comment="수집 대상 여부. 끄더라도 행은 남겨 왜 뺐는지를 보존한다",
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
        sa.CheckConstraint(
            "collection_mode IN ('metadata_only', 'feed_content', 'full_text')",
            name="ck_document_source_collection_mode",
        ),
        sa.CheckConstraint("source_kind IN ('official', 'media')", name="ck_document_source_kind"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_document_source_slug"),
        comment="문서 수집 출처와 출처별 수집 정책을 보관하는 마스터",
        info={"database": "default", "managed": True},
    )
    op.create_table(
        "document",
        sa.Column(
            "source_slug",
            sa.Text(),
            nullable=False,
            comment="document_source.slug와 같은 값. 외래키를 걸지 않아 마스터가 없어도 수집이 멈추지 않는다",
        ),
        sa.Column(
            "external_id",
            sa.Text(),
            nullable=False,
            comment="출처 안에서 문서를 가리키는 식별자. 제공처 ID가 없으면 정규화한 URL을 쓴다",
        ),
        sa.Column("canonical_url", sa.Text(), nullable=False, comment="문서 원문 URL"),
        sa.Column(
            "document_type",
            sa.Enum("article", "report", "press_release", "speech", name="documenttype", native_enum=False, length=20),
            nullable=False,
            comment="문서 종류(article, report, press_release 또는 speech)",
        ),
        sa.Column("title", sa.Text(), nullable=False, comment="정규화한 제목"),
        sa.Column("summary", sa.Text(), nullable=True, comment="피드가 준 요약. 우리가 만든 요약이 아니다"),
        sa.Column(
            "body",
            sa.Text(),
            nullable=True,
            comment="정규화한 본문. metadata_only 출처는 NULL이며 CHECK 제약이 이를 강제한다",
        ),
        sa.Column(
            "language",
            sa.Text(),
            nullable=False,
            comment="본문 언어(ISO 639-1, 예: ko, en). 검색 토크나이저를 가르는 값이다",
        ),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="제공처가 알려 준 발행 시각(UTC). 피드가 주지 않으면 NULL이다",
        ),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="이 문서를 처음 본 시각(UTC). 발행 시각과 달리 항상 있다",
        ),
        sa.Column(
            "content_level",
            sa.Enum("metadata_only", "feed_content", "full_text", name="collectionmode", native_enum=False, length=20),
            nullable=False,
            comment="이 문서에 실제로 담긴 수준. 출처 정책과 같지만 본문 수집이 실패하면 낮아질 수 있다",
        ),
        sa.Column(
            "content_hash",
            sa.Text(),
            nullable=False,
            comment="정규화한 제목·요약·본문의 SHA-256. 재평가 여부와 완전 중복 판정의 기준이다. 정규화 규칙이 흔들리면 이 값이 매번 바뀌므로 규칙을 먼저 고정한다",
        ),
        sa.Column(
            "canonical_document_id",
            sa.BigInteger(),
            nullable=True,
            comment="중복일 때 대표 문서 ID. 대표 문서 자신은 NULL이다. 물리 삭제는 하지 않는다",
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
        sa.CheckConstraint(
            "content_level <> 'metadata_only' OR body IS NULL", name="ck_document_metadata_only_has_no_body"
        ),
        sa.CheckConstraint(
            "content_level IN ('metadata_only', 'feed_content', 'full_text')", name="ck_document_content_level"
        ),
        sa.CheckConstraint(
            "document_type IN ('article', 'report', 'press_release', 'speech')", name="ck_document_type"
        ),
        sa.ForeignKeyConstraint(["canonical_document_id"], ["document.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_record_id"], ["source_record.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_slug", "external_id", name="uq_document_natural_key"),
        comment="수집한 경제 문서 한 건의 정규화 결과와 평가를 보관하는 테이블",
        info={"database": "default", "managed": True},
    )
    op.create_index("ix_document_canonical_document_id", "document", ["canonical_document_id"], unique=False)
    op.create_index("ix_document_content_hash", "document", ["content_hash"], unique=False)
    op.create_index("ix_document_published_at", "document", ["published_at"], unique=False)
    op.create_index("ix_document_source_record_id", "document", ["source_record_id"], unique=False)

    op.bulk_insert(
        sa.table(
            "document_source",
            sa.column("slug", sa.Text),
            sa.column("name", sa.Text),
            sa.column("source_kind", sa.String),
            sa.column("country", sa.Text),
            sa.column("language", sa.Text),
            sa.column("feed_url", sa.Text),
            sa.column("collection_mode", sa.String),
            sa.column("enabled", sa.Boolean),
        ),
        [
            dict(
                zip(
                    ("slug", "name", "source_kind", "country", "language", "feed_url", "collection_mode", "enabled"),
                    row,
                )
            )
            for row in DOCUMENT_SOURCE_SEED
        ],
        # offline(`--sql`)에서는 executemany를 찍을 수 없다. 행마다 INSERT를 내게 한다.
        multiinsert=False,
    )

    op.create_table(
        "document_indicator",
        sa.Column(
            "document_id", sa.BigInteger(), nullable=False, comment="문서 ID. 문서가 지워지면 태그도 함께 지운다"
        ),
        sa.Column(
            "provider",
            sa.Text(),
            nullable=False,
            comment="지표 제공처(fred, ecos, yahoo 등). series_id는 제공처 안에서만 고유하다",
        ),
        sa.Column(
            "series_id",
            sa.Text(),
            nullable=False,
            comment="indicator_series.series_id 또는 quote_symbol.symbol과 같은 값(예: DGS10, USDKRW)",
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
        sa.ForeignKeyConstraint(["document_id"], ["document.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "provider", "series_id", name="uq_document_indicator_natural_key"),
        comment="문서와 지표 시계열을 잇는 태그 테이블",
        info={"database": "default", "managed": True},
    )
    op.create_index("ix_document_indicator_series", "document_indicator", ["provider", "series_id"], unique=False)
    op.create_table(
        "document_instrument",
        sa.Column(
            "document_id", sa.BigInteger(), nullable=False, comment="문서 ID. 문서가 지워지면 태그도 함께 지운다"
        ),
        sa.Column(
            "ticker",
            sa.Text(),
            nullable=False,
            comment="instrument.ticker와 같은 값(예: 005930). 외래키를 걸지 않아 마스터가 없어도 태깅이 멈추지 않는다",
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
        sa.ForeignKeyConstraint(["document_id"], ["document.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "ticker", name="uq_document_instrument_natural_key"),
        comment="문서와 추적 종목을 잇는 태그 테이블",
        info={"database": "default", "managed": True},
    )
    op.create_index("ix_document_instrument_ticker", "document_instrument", ["ticker"], unique=False)
    op.add_column(
        "document",
        sa.Column(
            "direction",
            sa.Enum("positive", "negative", "neutral", name="direction", native_enum=False, length=20),
            nullable=True,
            comment="LLM이 본 방향(positive, negative 또는 neutral). 평가 전이면 NULL이다",
        ),
    )
    op.add_column(
        "document",
        sa.Column(
            "value_score",
            sa.Integer(),
            nullable=True,
            comment="관련성·새로움·구체성·영향의 0~2점 합계(0~8). **이 값으로 문서를 버리지 않는다.** 리포트를 만들 때 프롬프트가 상위 몇 개를 고르는 데만 쓴다",
        ),
    )
    op.add_column(
        "document",
        sa.Column(
            "assessment",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="LLM 응답 전체(세부 점수, 주제, 새 사실, 판단 근거, 근거 청크). 조회 조건이 굳으면 컬럼으로 뺀다",
        ),
    )
    op.add_column("document", sa.Column("llm_model", sa.Text(), nullable=True, comment="평가에 쓴 모델 이름"))
    op.add_column(
        "document",
        sa.Column(
            "prompt_version",
            sa.Text(),
            nullable=True,
            comment="평가에 쓴 프롬프트 버전. 이 값이 오르면 재평가 대상이 된다",
        ),
    )
    op.add_column(
        "document",
        sa.Column(
            "assessed_content_hash",
            sa.Text(),
            nullable=True,
            comment="평가 시점의 content_hash. 현재 content_hash와 다르면 본문이 바뀐 것이라 다시 평가한다. 이 컬럼이 없으면 같은 문서를 매번 다시 평가하거나 영영 안 하거나 둘 중 하나가 된다",
        ),
    )
    op.add_column(
        "document",
        sa.Column(
            "assessed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="평가를 마친 시각(UTC). 실패하면 NULL로 남아 다음 실행이 다시 집는다",
        ),
    )

    op.execute(DAILY_SERIES_VIEW)


def downgrade_default() -> None:
    op.execute("DROP VIEW IF EXISTS daily_series")
    op.drop_column("document", "assessed_at")
    op.drop_column("document", "assessed_content_hash")
    op.drop_column("document", "prompt_version")
    op.drop_column("document", "llm_model")
    op.drop_column("document", "assessment")
    op.drop_column("document", "value_score")
    op.drop_column("document", "direction")
    op.drop_index("ix_document_instrument_ticker", table_name="document_instrument")
    op.drop_table("document_instrument")
    op.drop_index("ix_document_indicator_series", table_name="document_indicator")
    op.drop_table("document_indicator")
    op.drop_index("ix_document_source_record_id", table_name="document")
    op.drop_index("ix_document_published_at", table_name="document")
    op.drop_index("ix_document_content_hash", table_name="document")
    op.drop_index("ix_document_canonical_document_id", table_name="document")
    op.drop_table("document")
    op.drop_table("document_source")
    op.drop_index("ix_quote_daily_source_record_id", table_name="quote_daily")
    op.drop_index("ix_quote_daily_business_date", table_name="quote_daily")
    op.drop_table("quote_daily")
    op.drop_index("ix_stock_investor_trade_daily_source_record_id", table_name="stock_investor_trade_daily")
    op.drop_index("ix_stock_investor_trade_daily_business_date", table_name="stock_investor_trade_daily")
    op.drop_table("stock_investor_trade_daily")
    op.drop_index("ix_stock_investor_estimate_snapshot_source_record_id", table_name="stock_investor_estimate_snapshot")
    op.drop_table("stock_investor_estimate_snapshot")
    op.drop_index("ix_quote_bar_source_record_id", table_name="quote_bar")
    op.drop_table("quote_bar")
    op.drop_index("ix_market_session_verification_source_record_id", table_name="market_session")
    op.drop_index("ix_market_session_source_record_id", table_name="market_session")
    op.drop_index("ix_market_session_session_date", table_name="market_session")
    op.drop_table("market_session")
    op.drop_index("ix_market_movement_snapshot_source_record_id", table_name="market_movement_snapshot")
    op.drop_table("market_movement_snapshot")
    op.drop_index("ix_market_investor_flow_snapshot_source_record_id", table_name="market_investor_flow_snapshot")
    op.drop_table("market_investor_flow_snapshot")
    op.drop_index("ix_krx_stock_short_sale_daily_source_record_id", table_name="krx_stock_short_sale_daily")
    op.drop_table("krx_stock_short_sale_daily")
    op.drop_index(
        "ix_krx_stock_securities_lending_daily_source_record_id", table_name="krx_stock_securities_lending_daily"
    )
    op.drop_table("krx_stock_securities_lending_daily")
    op.drop_index("ix_krx_stock_credit_balance_daily_source_record_id", table_name="krx_stock_credit_balance_daily")
    op.drop_table("krx_stock_credit_balance_daily")
    op.drop_index(
        "ix_krx_market_securities_lending_daily_source_record_id", table_name="krx_market_securities_lending_daily"
    )
    op.drop_table("krx_market_securities_lending_daily")
    op.drop_index("ix_krx_market_funds_daily_source_record_id", table_name="krx_market_funds_daily")
    op.drop_table("krx_market_funds_daily")
    op.drop_index("ix_krx_credit_balance_ranking_daily_stock_code", table_name="krx_credit_balance_ranking_daily")
    op.drop_index("ix_krx_credit_balance_ranking_daily_source_record_id", table_name="krx_credit_balance_ranking_daily")
    op.drop_table("krx_credit_balance_ranking_daily")
    op.drop_index("ix_indicator_observation_source_record_id", table_name="indicator_observation")
    op.drop_table("indicator_observation")
    op.drop_index("ix_earnings_fact_stock_code_period_end", table_name="earnings_fact")
    op.drop_index("ix_earnings_fact_source_record_id", table_name="earnings_fact")
    op.drop_table("earnings_fact")
    op.drop_index("ix_disclosure_event_stock_code_receipt_date", table_name="disclosure_event")
    op.drop_index("ix_disclosure_event_source_record_id", table_name="disclosure_event")
    op.drop_table("disclosure_event")
    op.drop_index("ix_source_record_source_started_at", table_name="source_record")
    op.drop_table("source_record")
    op.drop_table("quote_symbol")
    op.drop_table("instrument")
    op.drop_table("indicator_series")
    op.drop_index("idx_exchange_rate_date", table_name="exchange_rate")
    op.drop_index("idx_exchange_rate_currency_date", table_name="exchange_rate")
    op.drop_table("exchange_rate")


def upgrade_market_migration() -> None:
    pass


def downgrade_market_migration() -> None:
    pass
