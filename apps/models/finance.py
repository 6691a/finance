"""`finance` alias가 가리키는 외부 DB의 테이블 매핑.

이 모듈의 테이블은 이 프로젝트가 만든 것이 아니라 이미 운영 데이터가 들어 있는 상태로
존재한다. 스키마는 `migrations`가 추적하지만(라우팅상 `managed=True`) 첫 revision은
Django의 `migrate --fake-initial`처럼 실제 DB에 DDL을 내지 않고 리비전 포인터만 올린다.

그래서 모델 선언은 실제 DDL을 **글자 그대로** 미러링한다. 프로젝트 기본 규칙(UUID 기본키,
timezone-aware UTC 시각, 테이블·컬럼 주석)을 여기서는 적용하지 않는다. 모델과 DB가 한 글자라도
어긋나면 autogenerate가 그 차이를 ALTER로 뱉고, 그건 이 테이블에 내면 안 되는 변경이다.

파일 이름은 스키마 이름이 아니라 DB alias 이름이다. 이 테이블은 finance DB의 `public`에 있다.
"""

import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base, table_options


class ExchangeRate(Base):
    """finance DB가 소유한 통화별 환율 고시 테이블. 이 프로젝트는 읽기만 한다."""

    __tablename__ = "exchange_rate"
    __table_args__ = (
        UniqueConstraint(
            "currency",
            "date",
            "time",
            "round",
            name="unique_currency_date_time_round",
        ),
        Index("idx_exchange_rate_date", "date"),
        Index("idx_exchange_rate_currency_date", "currency", "date"),
        table_options(
            comment=None,
            database="finance",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=False),
        nullable=True,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=False),
        nullable=True,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    currency: Mapped[str] = mapped_column(String(10), nullable=False)
    round: Mapped[int] = mapped_column(Integer, nullable=False)
    date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    time: Mapped[dt.time] = mapped_column(Time, nullable=False)
    buy: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    sell: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    send: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    receive: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    exchange_standard_rate: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
