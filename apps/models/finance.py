"""외부 finance DB에서 형태를 가져온 환율 고시 테이블.

원본은 다른 시스템이 만든 외부 finance DB의 `exchange_rate`다. 지금은 이 프로젝트가
`default` alias에 제 테이블을 만들어 직접 수집한다. 외부 DB의 데이터는 가져오지 않는다.
`airflow/dags/exchange_rate_daily.py`가 `finance` 연결로 여기에 쓴다.

**우리 DB 이름도 `finance`다.** 외부 원본과 이름이 같아서, 이 파일에서 원본을 가리킬 때는
반드시 `외부 finance DB`라고 쓴다. 별칭 이름(`default`, `finance`)은 또 다른 층이다.

컬럼의 **형태**는 여전히 원본을 따른다. SERIAL 기본키, 시간대 없는 `timestamp`,
`date`/`time` 분리가 그렇다. 나중에 외부 DB의 과거 행을 `INSERT INTO ... SELECT`로 옮길 때
컬럼이 1:1로 맞아야 하기 때문이다. 타입이나 컬럼 구성을 바꾸려면 그 계획을 먼저 접는다.

주석은 예외다. 테이블·컬럼 주석은 데이터 이관에 영향을 주지 않으므로 이 프로젝트의 기본
규칙대로 채운다. `currency`도 파이썬 쪽만 `Currency`로 좁혔고 저장 타입은 `VARCHAR(10)`
그대로다. 두 변경 모두 컬럼 형태를 건드리지 않는다.

파일 이름은 스키마 이름이 아니라 형태를 가져온 원본 DB 이름이다. 테이블 자체는 우리 DB의
`public`에 있다.
"""

import datetime as dt
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    Index,
    Integer,
    Numeric,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from apps.core.database import Base, table_options


class Currency(StrEnum):
    """`exchange_rate`에 고시가 쌓이는 통화. 값은 ISO 4217 코드다.

    수집 대상을 정하는 원본은 `modules.collectors.hana.HanaCurrency`다. Airflow는 `apps/`를
    보지 못하고 백엔드 런타임은 `airflow/`를 보지 못해 import로 묶을 수 없어서 값을 두 곳에
    적는다. `tests/models/test_finance_models.py`가 두 정의를 대조하므로 수집기 쪽에 통화를
    추가하면 그 테스트가 먼저 깨진다.
    """

    USD = "USD"
    JPY = "JPY"
    CNY = "CNY"
    EUR = "EUR"
    HKD = "HKD"
    TWD = "TWD"
    GBP = "GBP"
    AUD = "AUD"
    CAD = "CAD"
    RUB = "RUB"


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
            comment="하나은행이 고시한 통화별·회차별 환율",
            # [배포] 저장 위치 4/5 — 이 테이블을 **만드는** DB 별칭.
            # `config.yaml`의 같은 이름 별칭 URL이 실제 대상이다. 배포 대상을 바꾸려면
            # 그 URL을 바꾸는 게 먼저고, 별칭 자체를 옮길 때만 이 값을 건드린다.
            # `airflow/dags/exchange_rate_daily.py`의 `CONNECTION_ID`가 가리키는 DB와
            # 같은 곳이어야 한다. 어긋나면 DAG가 없는 테이블에 INSERT를 시도한다.
            database="default",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
        comment="레코드 고유 식별자",
    )
    created_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=False),
        nullable=True,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="레코드 생성 시각. 원본 DDL을 따라 시간대가 없으며 DB 서버 시각으로 채워진다",
    )
    updated_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=False),
        nullable=True,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="레코드 최종 수정 시각. 갱신은 upsert가 직접 넣으며 DB 트리거는 없다",
    )
    currency: Mapped[Currency] = mapped_column(
        # 저장 타입은 `VARCHAR(10)`이다. PostgreSQL native enum은 값을 추가·삭제할 때
        # 마이그레이션 비용이 커서 쓰지 않는다. `create_constraint`는 기본값 그대로 꺼 둔다.
        # CHECK를 걸면 원본 finance 테이블에 없는 제약이 생겨 형태가 어긋나고, 통화가 늘 때마다
        # 제약을 다시 만들어야 한다. 허용 값은 수집기와 이 Enum이 막는다.
        Enum(
            Currency,
            native_enum=False,
            length=10,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        comment="고시 통화 코드(ISO 4217). 허용 값은 Currency Enum이 정한다",
    )
    round: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="같은 고시일자 안의 고시 회차. 1부터 증가하며 값이 클수록 나중 고시다",
    )
    date: Mapped[dt.date] = mapped_column(
        Date,
        nullable=False,
        comment="고시일자. 하나은행 기준이라 KST이며 컬럼에 시간대 정보는 없다",
    )
    time: Mapped[dt.time] = mapped_column(
        Time,
        nullable=False,
        comment="고시 시각. 고시일자와 같은 KST 기준이고 시간대 정보는 없다",
    )
    buy: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
        comment="현찰 사실 때 환율(원). 고객이 외화를 현찰로 살 때 적용된다",
    )
    sell: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
        comment="현찰 파실 때 환율(원). 고객이 외화를 현찰로 팔 때 적용된다",
    )
    send: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
        comment="송금 보낼 때 환율(원)",
    )
    receive: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
        comment="송금 받을 때 환율(원)",
    )
    exchange_standard_rate: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
        comment="매매기준율(원). 일부 통화는 0으로 고시되므로 조회 쪽에서 대체값을 쓴다",
    )
