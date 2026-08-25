"""거래일 달력과 지표 관측값.

두 테이블 다 "언제의 값인가"를 정하는 축이라 같이 둔다. `market_session`은 어느 시장이
어느 날 열렸는지를, `indicator_observation`은 제공처별 시계열 관측값을 담는다.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column

from apps.core.database import EntityBase, table_options


class IndicatorObservation(EntityBase):
    __tablename__ = "indicator_observation"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "series_id",
            "observation_date",
            name="uq_indicator_observation_natural_key",
        ),
        Index("ix_indicator_observation_source_record_id", "source_record_id"),
        table_options(
            comment="여러 제공처의 지표 관측값을 조회 가능한 형태로 정규화하고 원본과 연결하는 테이블",
            database="default",
        ),
    )

    provider: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="데이터 제공처 식별자(예: fred 또는 ecos). 같은 수집의 source_record.source와 같은 값이다",
    )
    series_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="제공처가 정의한 시계열 식별자(예: DGS10). 제공처 안에서만 고유하다",
    )
    observation_date: Mapped[date] = mapped_column(nullable=False, comment="지표 값의 기준일")
    value: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, comment="정규화한 지표 값")
    unit: Mapped[str] = mapped_column(Text, nullable=False, comment="지표 값의 단위(예: Percent)")
    source_record_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("source_record.id", ondelete="RESTRICT"),
        nullable=False,
        comment="근거가 되는 source_record 레코드 ID",
    )


class MarketCode(StrEnum):
    """휴장 캘린더를 공유하는 시장 묶음.

    `reference.Market`(`kospi`/`kosdaq`/`nyse`/`nasdaq`)과 **값 체계가 다르다.** 저쪽은 종목이
    상장된 거래소이고 이쪽은 달력이 같은 시장의 묶음이다. 이름이 비슷해도 조인 키가 아니다.

    미국을 거래소별로 나누지 않는 이유는 나스닥·뉴욕거래소·아멕스의 휴장일이 같고, KIS
    해외결제일자조회가 주는 시장별 5행의 결제일도 실측에서 모두 같았기 때문이다. NYSE도
    "All NYSE markets"로 한 벌만 고시한다.
    """

    KRX = "KRX"
    US_EQUITY = "US_EQUITY"


class SessionVerifier(StrEnum):
    """`effective_open_day`를 채운 제공처."""

    KIS = "kis"
    NYSE = "nyse"


class MarketSession(EntityBase):
    """시장별·날짜별 개장 여부와 결제일을 저장한다.

    조회하는 쪽은 `effective_open_day` 하나만 본다. 그 값이 어디서 왔는지는 `verified_by`와
    두 계보 외래키가 설명한다.

    **국내는 KIS 국내휴장일조회가, 미국은 NYSE 공식 캘린더가 판정의 주인이다.** KIS
    해외결제일자조회는 미국 행의 결제일만 채우고 판정에는 손대지 않는다. 그 API는 휴장한
    나라의 행을 아예 주지 않고 미래 날짜에는 0건으로 답해서(실측), 그것만으로는 미국 휴장일
    행이 영원히 생기지 않고 오늘 이후를 미리 알 수도 없기 때문이다.
    """

    __tablename__ = "market_session"
    __table_args__ = (
        UniqueConstraint(
            "market_code",
            "session_date",
            name="uq_market_session_natural_key",
        ),
        CheckConstraint(
            "market_code IN ('KRX', 'US_EQUITY')",
            name="ck_market_session_market_code",
        ),
        CheckConstraint(
            "verified_by IN ('kis', 'nyse')",
            name="ck_market_session_verified_by",
        ),
        Index("ix_market_session_session_date", "session_date"),
        Index("ix_market_session_source_record_id", "source_record_id"),
        Index("ix_market_session_verification_source_record_id", "verification_source_record_id"),
        table_options(
            comment="시장별·날짜별 개장 여부와 결제일을 저장하는 테이블",
            database="default",
        ),
    )

    market_code: Mapped[MarketCode] = mapped_column(
        SqlEnum(
            MarketCode,
            native_enum=False,
            length=20,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        comment="휴장 캘린더를 공유하는 시장 묶음(KRX, US_EQUITY). 상장 거래소를 뜻하는 Market enum과는 다른 체계다",
    )
    market_name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="사람이 읽는 시장 이름(예: 한국거래소)",
    )
    country_code: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="시장이 속한 국가(ISO 3166-1 alpha-2, 예: KR 또는 US)",
    )
    session_date: Mapped[date] = mapped_column(
        nullable=False,
        comment="그 시장의 현지 거래일. 시간대는 시장 현지 기준이다",
    )
    kis_weekday_code: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="국내 KIS 요일구분코드(wday_dvsn_cd). 미국 행은 NULL이다",
    )
    kis_business_day: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        comment="국내 KIS 영업일 여부(bzdy_yn). 미국 행은 NULL이다",
    )
    kis_trading_day: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        comment="국내 KIS 거래일 여부(tr_day_yn). 미국 행은 NULL이다",
    )
    kis_open_day: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        comment="국내 KIS 개장일 여부(opnd_yn). 주문 가능 여부의 원본이다. 미국 행은 NULL이다",
    )
    kis_settlement_day: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        comment="국내 KIS 결제일 여부(sttl_day_yn). 미국 행은 NULL이다",
    )
    local_settlement_date: Mapped[date | None] = mapped_column(
        nullable=True,
        comment="해외 KIS 현지결제일자(acpl_sttl_dt). 국내 행은 NULL이다",
    )
    domestic_settlement_date: Mapped[date | None] = mapped_column(
        nullable=True,
        comment="해외 KIS 국내결제일자(dmst_sttl_dt). 국내 행은 NULL이다",
    )
    effective_open_day: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        comment=(
            "소비자가 쓰는 최종 개장일 판정. 조기 폐장일은 true다. "
            "아직 판정하지 못한 날짜는 NULL이고, 이때 수집기는 시세 요청을 멈추지 않는다"
        ),
    )
    verified_by: Mapped[SessionVerifier | None] = mapped_column(
        SqlEnum(
            SessionVerifier,
            native_enum=False,
            length=20,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=True,
        comment="effective_open_day를 채운 제공처(kis, nyse)",
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="최종 판정을 확인한 시각(UTC)",
    )
    source_record_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("source_record.id", ondelete="RESTRICT"),
        nullable=False,
        comment="이 행을 만든 수집의 source_record 레코드 ID. 국내는 KIS, 미국은 NYSE 수집이다",
    )
    verification_source_record_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("source_record.id", ondelete="RESTRICT"),
        nullable=True,
        comment="같은 행을 보강한 다른 출처의 source_record 레코드 ID. 미국 행의 결제일을 채운 KIS 해외 수집이다",
    )
