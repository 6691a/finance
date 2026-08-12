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

    설계 문서는 `docs/kis-market-session-calendar.md`다.
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


class QuoteBar(EntityBase):
    """지수·선물의 1분봉을 장중 알림 판단용으로 누적한다.

    `indicator_observation`과 목적이 다르다. 저쪽은 하루 한 값을 쌓아 "어제 이랬으니 오늘은
    이럴 것"을 유추하는 리포트용이고, 이 테이블은 미국 선물이 지금 빠지고 있으니 한국
    반도체가 곧 빠질 수 있다는 **실시간 알림**을 위한 것이다. 한국 정규장 시간대에는 미국
    현물 지수가 멈춰 있어 선물만이 살아 있는 신호이므로, 수집은 24시간 돈다.

    `(provider, symbol, bar_at)`이 멱등 키다. 폴링 주기보다 넓은 구간을 받아도 겹치는 봉은
    갱신으로 흡수된다. `symbol`은 제공처 안에서만 고유하므로 키에 `provider`가 함께 들어간다.
    """

    __tablename__ = "quote_bar"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "symbol",
            "bar_at",
            name="uq_quote_bar_natural_key",
        ),
        Index("ix_quote_bar_source_record_id", "source_record_id"),
        table_options(
            comment="지수·선물의 1분봉을 장중 알림 판단용으로 누적하는 테이블",
            database="default",
        ),
    )

    provider: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="데이터 제공처 식별자(예: yahoo 또는 kis). 같은 수집의 source_record.source와 같은 값이다",
    )
    symbol: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="시세 대상 식별자(예: SP500_FUT, SOX). 제공처 안에서만 고유하다",
    )
    bar_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="1분봉이 시작하는 시각(UTC). 봉은 이 시각부터 1분간의 거래를 담는다",
    )
    open: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, comment="봉 구간의 시가")
    high: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, comment="봉 구간의 고가")
    low: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, comment="봉 구간의 저가")
    close: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, comment="봉 구간의 종가")
    volume: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        comment=(
            "봉 구간의 거래량. 제공처가 주는 값을 그대로 저장한다. "
            "현물 지수처럼 거래량 개념이 없는 심볼은 제공처가 0을 실어 보내므로 0이 들어간다. "
            "즉 0은 '거래가 없었다'와 '제공처가 거래량을 주지 않는다'를 구분하지 않는다. "
            "거래량으로 판단하는 조회가 생기면 그때 심볼 종류로 갈라 읽는다"
        ),
    )
    previous_close: Mapped[Decimal] = mapped_column(
        Numeric(18, 8),
        nullable=False,
        comment=(
            "직전 정규장 종가. 알림이 쓰는 변동률의 분모다. "
            "봉마다 같은 값이 반복되지만 세션 경계 계산을 피하려고 그대로 저장한다"
        ),
    )
    contract_code: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "선물의 실제 월물 코드(예: A01609). 현물 지수와 연속 심볼은 NULL이다. "
            "월물이 바뀌면 가격에 갭이 생기는데, 이 값이 없으면 그 갭이 시장 급변인지 "
            "롤오버인지 구분할 수 없다"
        ),
    )
    source_record_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("source_record.id", ondelete="RESTRICT"),
        nullable=False,
        comment="근거가 되는 source_record 레코드 ID",
    )
