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
    GOVERNMENT_BOND = "government_bond"
    MONEY_MARKET = "money_market"


class IndicatorSeries(EntityBase):
    """`indicator_observation`에 쌓이는 시계열이 어느 나라 무슨 금리인지 설명한다.

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
            "kind IN ('government_bond', 'money_market')",
            name="ck_indicator_series_kind",
        ),
        CheckConstraint(
            "maturity_months > 0",
            name="ck_indicator_series_maturity_months",
        ),
        table_options(
            comment="지표 시계열이 어느 나라 무슨 금리인지 설명하는 마스터",
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
    maturity_months: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="만기 개월 수. 만기별 비교와 정렬에 쓴다(3개월=3, 10년=120). 91일물은 3으로 둔다",
    )
    kind: Mapped[SeriesKind] = mapped_column(
        SqlEnum(
            SeriesKind,
            native_enum=False,
            length=20,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        comment="금리의 종류(government_bond 또는 money_market). 국채 곡선에서 단기 자금시장 금리를 가른다",
    )
    label: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="차트와 표에 쓰는 표시 이름(예: 미국 10년물)",
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
    """시세·뉴스·시그널이 참조할 추적 종목의 기준 정보를 저장한다."""

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
            comment="시세·뉴스·시그널이 참조하는 추적 종목 마스터",
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
        comment="신규 데이터 수집과 분석을 수행할 추적 대상 여부",
    )
