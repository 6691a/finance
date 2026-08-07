from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Text,
    text,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from apps.core.database import EntityBase, table_options


class SourceType(StrEnum):
    API = "api"
    CRAWL = "crawl"
    WEBSOCKET = "websocket"


class SourceStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    QUARANTINED = "quarantined"


class SourceRecord(EntityBase):
    __tablename__ = "source_record"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('api', 'crawl', 'websocket')",
            name="ck_source_record_source_type",
        ),
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'quarantined')",
            name="ck_source_record_status",
        ),
        Index("ix_source_record_source_started_at", "source", "started_at"),
        table_options(
            comment="API, 크롤링, 웹소켓 수집 단위의 출처와 상태를 보존하는 테이블",
            database="default",
        ),
    )

    source_type: Mapped[SourceType] = mapped_column(
        SqlEnum(
            SourceType,
            native_enum=False,
            length=20,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        comment="수집 방식(api, crawl 또는 websocket)",
    )
    source: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="데이터 제공처 식별자(예: fred 또는 kis)",
    )
    source_key: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="공급자 내 원천 식별자(예: 시계열 ID, URL 또는 배치 ID)",
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="수집 시작 시각(UTC)",
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="수집 완료 시각(UTC); 진행 중이면 NULL",
    )
    status: Mapped[SourceStatus] = mapped_column(
        SqlEnum(
            SourceStatus,
            native_enum=False,
            length=20,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        comment="수집 상태(예: running, succeeded, failed 또는 quarantined)",
    )
    record_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
        comment="이 수집 단위에서 생성한 정규화 레코드 수",
    )
    payload: Mapped[dict[str, object] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="작은 JSON 원본; 저장하지 않으면 NULL",
    )
    payload_uri: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="대용량 원본의 외부 저장 위치; 없으면 NULL",
    )
    source_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment="HTTP 상태나 웹소켓 세션 ID 등 공급자별 부가 정보",
    )
