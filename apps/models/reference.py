from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column

from apps.core.database import EntityBase, table_options


class SeriesKind(StrEnum):
    """시계열이 무엇을 재는 값인지.

    금리 둘로 시작했지만 이 테이블은 금리 전용이 아니다. 물가지수와 실물활동이 들어오면서
    종류가 넷이 됐다. **단위가 다른 값을 한 화면에 못 놓기 때문에** 물가지수(지수, 300 근처)와
    실물활동(백만 달러, 70만 근처)을 갈라 둔다.

    **정책금리는 `money_market`이 아니다.** CD 91일은 시장이 만드는 값이고 정책금리는
    중앙은행이 정하는 값이다. 한 축에 섞으면 시장금리 패널이 정책금리 계단을 함께 그린다.

    **대차대조표는 가격이 아니라 수량이다.** 금리가 전부 퍼센트인 것과 달리 잔액은 통화별
    단위(백만 달러·억엔·십억원)로 저장하므로 금리와 한 축에 놓을 수 없다. 그리고 총자산
    (`balance_sheet`)과 그 안의 한 항목(`balance_sheet_item`)을 다시 가른다 — 영란은행은
    총자산을 분기로만 고시하고 주간으로는 준비금잔액 같은 항목만 준다. 한 종류로 두면
    "중앙은행 총자산 전부"를 묻는 쿼리가 영국의 준비금을 총자산으로 읽는다.

    **`tips_rate`도 `government_bond`가 아니다.** 실질금리와 기대인플레는 명목 국채 금리를
    분해한 값이라 만기가 같다. 국채에 넣으면 미국 10년물이 두 개로 보인다. 둘을 한 종류로
    두는 이유는 반대로 **더하면 명목이 되기 때문**이다 — 따로 보면 뜻이 없다.

    """

    GOVERNMENT_BOND = "government_bond"
    MONEY_MARKET = "money_market"
    POLICY_RATE = "policy_rate"
    TIPS_RATE = "tips_rate"
    CREDIT_SPREAD = "credit_spread"
    PRICE_INDEX = "price_index"
    ACTIVITY = "activity"
    BALANCE_SHEET = "balance_sheet"
    BALANCE_SHEET_ITEM = "balance_sheet_item"


class IndicatorSeries(EntityBase):
    """`indicator_observation`에 쌓이는 시계열이 어느 나라 무슨 값인지 설명한다.

    관측값 테이블은 `(provider, series_id)`까지만 안다. 그 문자열에서 국가와 만기를
    되짚으려면 조회하는 쪽이 시계열 목록을 알고 있어야 하고, 그러면 나라를 추가할 때마다
    대시보드 쿼리를 고쳐야 한다. 그 지식을 여기 한 곳에 모아 조인으로 푼다.

    관측값에서 이 테이블로 외래키를 걸지 않는다. 걸면 마스터 행이 없는 시계열을 수집기가
    저장하지 못해, Enum에만 추가하고 마스터를 빠뜨린 순간 DAG가 죽는다. 대신
    `tests/migrations/test_indicator_series_catalog.py`가 수집기 Enum과 시드를 대조해
    어긋남을 테스트 단계에서 잡는다.
    """

    __tablename__ = "indicator_series"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "series_id",
            name="uq_indicator_series_natural_key",
        ),
        CheckConstraint(
            "kind IN ('government_bond', 'money_market', 'policy_rate', 'tips_rate', "
            "'credit_spread', 'price_index', 'activity', 'balance_sheet', 'balance_sheet_item')",
            name="ck_indicator_series_kind",
        ),
        # 만기가 없는 지표는 NULL이다. 0으로 채우지 않는다. 0을 넣으면 만기별 비교 쿼리가
        # 그 시계열을 "0개월물"로 그린다.
        CheckConstraint(
            "maturity_months IS NULL OR maturity_months > 0",
            name="ck_indicator_series_maturity_months",
        ),
        table_options(
            comment="지표 시계열이 어느 나라 무슨 값인지 설명하는 마스터",
            database="default",
        ),
    )

    provider: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="데이터 제공처 식별자(예: fred 또는 ecos). indicator_observation.provider와 같은 값이다",
    )
    series_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="제공처 안에서 시계열을 가리키는 식별자. indicator_observation.series_id와 같은 값이다",
    )
    country: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="발행 국가(ISO 3166-1 alpha-2, 예: US 또는 KR). 유로존처럼 국가가 아닌 통화권은 XM을 쓴다",
    )
    country_name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="국가 표시 이름. 국가에 붙는 속성이 더 늘면 country 마스터 테이블로 분리한다",
    )
    maturity_months: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment=(
            "만기 개월 수. 만기별 비교와 정렬에 쓴다(3개월=3, 10년=120). 91일물은 3으로 둔다. "
            "물가지수처럼 만기 개념이 없는 지표는 NULL이다"
        ),
    )
    kind: Mapped[SeriesKind] = mapped_column(
        SqlEnum(
            SeriesKind,
            native_enum=False,
            length=20,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        comment=(
            "시계열의 종류(government_bond, money_market, policy_rate, tips_rate, credit_spread, "
            "price_index, activity, balance_sheet 또는 balance_sheet_item). 국채 곡선에서 단기 자금시장 "
            "금리·정책금리·실질금리·신용스프레드를 가르고, 단위가 다른 거시지표와 대차대조표 잔액을 "
            "그 곡선에서 뺀다"
        ),
    )
    label: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="차트와 표에 쓰는 표시 이름(예: 미국 10년물)",
    )


class QuoteSymbolKind(StrEnum):
    INDEX = "index"
    INDEX_FUTURE = "index_future"
    FX = "fx"
    RATE = "rate"
    BOND_FUTURE = "bond_future"
    COMMODITY = "commodity"
    EQUITY = "equity"
    CRYPTO = "crypto"


class QuoteSymbol(EntityBase):
    """`quote_bar`에 쌓이는 심볼이 현물 지수인지 지수선물인지 설명하는 마스터다.

    `indicator_series`가 국채 대시보드에 하는 역할을 `quote_bar` 쪽에서 한다. 봉 테이블은
    `(provider, symbol)`까지만 알고, 그 문자열이 현물인지 선물인지는 모른다. 그 지식을
    조회하는 쪽이 들고 있으면 심볼을 하나 추가할 때마다 대시보드 SQL을 전부 고쳐야 한다.

    **`kind`가 존재하는 이유는 성격이 다른 값을 다른 화면에 두기 위해서다.**

    - 현물 지수와 지수선물은 거래 시간대가 다르다. 한국 정규장 시간에 현물은 멈춰 있고
      선물만 움직이므로 한 패널에 겹치면 현물 선이 직선으로 깔린다.
    - 환율(`fx`)과 금리(`rate`)는 정상 변동폭의 자릿수가 지수와 다르다. 금리는 특히
      **변화율(%)이 아니라 bp로 읽어야 하는 값**이라 지수와 같은 축에 두면 오해를 부른다.
      4.66에서 4.70으로 가는 건 "+0.86%"가 아니라 "+4bp"다.
    - `rate`와 `bond_future`를 나눈 이유는 **하나는 수익률이고 하나는 가격**이기 때문이다.
      `US10Y`는 4.66(%)이고 `ZN=F`는 110(달러)이다. 같은 "미 10년물"이라도 방향이 반대로
      움직이므로 한 패널에 겹치면 읽는 사람이 반드시 틀린다.
    - `commodity`(금·은·구리·유가)와 `equity`(개별 종목)도 각자 변동폭이 달라 따로 둔다.
    - `crypto`는 **거래 시간대 자체가 다르다.** 다른 모든 값이 멈추는 주말 48시간에 이것만
      움직여서, 같은 패널에 두면 나머지가 전부 직선으로 깔린다. 일중 변동폭도 지수의
      몇 배라 축을 공유할 수 없다.

    관측값에서 이 테이블로 외래키를 걸지 않는다. 걸면 마스터 행이 없는 심볼을 수집기가
    저장하지 못해, Enum에만 추가하고 시드를 빠뜨린 순간 DAG가 죽는다. 대신
    `tests/migrations/test_quote_symbol_catalog.py`가 수집기 Enum과 시드를 대조해 어긋남을
    테스트 단계에서 잡는다.
    """

    __tablename__ = "quote_symbol"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "symbol",
            name="uq_quote_symbol_natural_key",
        ),
        CheckConstraint(
            "kind IN ('index', 'index_future', 'fx', 'rate', 'bond_future', 'commodity', 'equity', 'crypto')",
            name="ck_quote_symbol_kind",
        ),
        table_options(
            comment="quote_bar 심볼이 현물 지수인지 지수선물인지 설명하는 마스터",
            database="default",
        ),
    )

    provider: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="데이터 제공처 식별자(예: yahoo). quote_bar.provider와 같은 값이다",
    )
    symbol: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="제공처 안에서 심볼을 가리키는 식별자. quote_bar.symbol과 같은 값이다",
    )
    kind: Mapped[QuoteSymbolKind] = mapped_column(
        SqlEnum(
            QuoteSymbolKind,
            native_enum=False,
            length=20,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        comment=(
            "값의 종류(index, index_future, fx, rate, bond_future, commodity, equity, crypto). "
            "화면을 가르는 기준이다. 거래 시간대가 다르고 정상 변동폭의 자릿수도 달라 "
            "한 축에 겹치면 읽을 수 없다"
        ),
    )
    country: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="기초 시장의 국가(ISO 3166-1 alpha-2, 예: US 또는 KR)",
    )
    country_name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="국가 표시 이름. 국가에 붙는 속성이 더 늘면 country 마스터 테이블로 분리한다",
    )
    label: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="차트와 표에 쓰는 표시 이름(예: 나스닥100 선물)",
    )


class Market(StrEnum):
    KOSPI = "kospi"
    KOSDAQ = "kosdaq"
    NYSE = "nyse"
    NASDAQ = "nasdaq"


class InstrumentKind(StrEnum):
    EQUITY = "equity"
    ETF = "etf"
    INDEX = "index"


class Instrument(EntityBase):
    """우리가 이름을 아는 종목의 기준 정보를 저장한다.

    **행이 있다는 것과 `is_watched`는 다른 뜻이다.** 행이 있으면 문서에서 그 종목을 알아보고
    리서치 리포트를 받는다. `is_watched`가 참이어야 시세까지 받는다. 읽는 쪽은
    `instrument/select_taggable.sql`(전체)과 `select_watched.sql`(시세 대상) 중 하나를 고른다.

    **`filing_entity_id`는 세 번째 축이다.** 시세와 규제 공시는 대상이 다르다 — 공시·실적은
    받지만 시세는 안 받는 종목이 있다. `select_filing_entities.sql`이 그 목록이고, 셋을 한
    플래그로 묶으면 DART 대상을 늘릴 때 분봉·수급·실시간 구독까지 함께 끌려온다.
    """

    __tablename__ = "instrument"
    __table_args__ = (
        UniqueConstraint(
            "ticker",
            "market",
            name="uq_instrument_ticker_market",
        ),
        CheckConstraint(
            "market IN ('kospi', 'kosdaq', 'nyse', 'nasdaq')",
            name="ck_instrument_market",
        ),
        CheckConstraint(
            "kind IN ('equity', 'etf', 'index')",
            name="ck_instrument_kind",
        ),
        table_options(
            comment="우리가 이름을 아는 종목의 마스터. is_watched가 참인 종목만 시세를 받는다",
            database="default",
        ),
    )

    ticker: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="거래 시장에서 사용하는 종목 코드",
    )
    market: Mapped[Market] = mapped_column(
        SqlEnum(
            Market,
            native_enum=False,
            length=20,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        comment="종목이 상장된 거래 시장(kospi, kosdaq, nyse 또는 nasdaq)",
    )
    name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="종목 표시 이름",
    )
    kind: Mapped[InstrumentKind] = mapped_column(
        SqlEnum(
            InstrumentKind,
            native_enum=False,
            length=20,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        comment="가격 수집 소스를 가르는 유형(equity, etf 또는 index)",
    )
    currency: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="종목 가격의 표시 통화(ISO 4217, 예: KRW 또는 USD)",
    )
    source_symbol: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="수집 소스에서 쓰는 심볼. 티커와 다를 때만 채운다(예: KOSPI → ^KS11)",
    )
    is_watched: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        comment="시세를 수집할 대상 여부. 거짓이면 문서 태그 후보로만 쓴다",
    )
    filing_entity_id: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "그 나라 공시 규제기관이 이 회사에 붙인 고유번호. 값이 있으면 규제 공시·실적 수집 대상이고 "
            "NULL이면 아니다. 발급 기관은 market이 정한다(kospi·kosdaq=금융감독원 DART 회사 고유번호 8자리, "
            "nyse·nasdaq=SEC EDGAR CIK). 그래서 읽는 쪽은 market을 함께 건다"
        ),
    )
    sector: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "이 종목이 대표하는 산업(예: 반도체, 자동차, 화장품). 한국 거시 지표를 회사가 아니라 "
            "산업 단위로 집계하기 위한 축이며 대표 기업이 교체돼도 이름이 바뀌지 않는다. "
            "값이 바뀌는 것이 전제라 Enum과 CHECK를 두지 않는다"
        ),
    )
