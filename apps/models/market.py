from datetime import date
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
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
            "series_id",
            "observation_date",
            name="uq_indicator_observation_natural_key",
        ),
        Index("ix_indicator_observation_source_record_id", "source_record_id"),
        table_options(
            comment="FRED 지표 관측값을 조회 가능한 형태로 정규화하고 원본과 연결하는 테이블",
            database="default",
        ),
    )

    series_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="공급자가 정의한 시계열 식별자(예: DGS10)",
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
