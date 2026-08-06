"""외부 finance DB에서 형태를 가져온 환율 고시 테이블.

원본은 다른 시스템이 만든 finance DB의 `exchange_rate`다. 지금은 이 프로젝트가 `default`
alias(news2)에 **같은 DDL로** 제 테이블을 만들어 직접 수집한다. finance DB의 데이터는
가져오지 않는다. `airflow/dags/exchange_rate_daily.py`가 `news` 연결로 여기에 쓴다.

alias만 옮기고 DDL은 원본 그대로 둔다. 그래서 프로젝트 기본 규칙(BIGSERIAL 기본키,
timezone-aware UTC 시각, 테이블·컬럼 주석)이 이 테이블에는 적용돼 있지 않다. 나중에 finance의
과거 데이터를 이관하거나 대시보드를 news2로 돌릴 때 컬럼이 1:1로 맞아야 하기 때문이다.
주석을 달거나 타입을 바꾸려면 그 계획을 먼저 접고 시작한다.

`created_at`/`updated_at`이 naive `timestamp`인 것도 원본을 따른 결과다. 이 프로젝트가 새로
만드는 테이블이라면 `EntityBase`의 timezone-aware 컬럼을 썼을 자리다.

파일 이름은 스키마 이름이 아니라 형태를 가져온 원본 DB 이름이다. 테이블 자체는 news2의
`public`에 있다.
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
    """통화별·회차별 환율 고시 테이블. 하나은행 수집 DAG가 채운다."""

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
            # [배포] 저장 위치 4/5 — 이 테이블을 **만드는** DB 별칭.
            # `config.yaml`의 같은 이름 별칭 URL이 실제 대상이다. 배포 대상을 바꾸려면
            # 그 URL을 바꾸는 게 먼저고, 별칭 자체를 옮길 때만 이 값을 건드린다.
            # `airflow/dags/exchange_rate_daily.py`의 `CONNECTION_ID`가 가리키는 DB와
            # 같은 곳이어야 한다. 어긋나면 DAG가 없는 테이블에 INSERT를 시도한다.
            database="default",
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
