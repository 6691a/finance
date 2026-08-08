from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
)
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
        comment="봉 구간의 거래량. 제공처가 주지 않으면 NULL이다(지수 선물은 0으로 오기도 한다)",
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
