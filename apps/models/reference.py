from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column

from core.database import EntityBase, table_options


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
