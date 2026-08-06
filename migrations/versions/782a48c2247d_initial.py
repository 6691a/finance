"""initial

Revision ID: 782a48c2247d
Revises:
Create Date: 2026-08-05 10:39:52.922576

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "782a48c2247d"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade(engine_name: str) -> None:
    _run(f"upgrade_{engine_name}")


def downgrade(engine_name: str) -> None:
    _run(f"downgrade_{engine_name}")


def _run(name: str) -> None:
    # A revision written before an alias existed has no section for it, and
    # there is nothing for that alias to do. Adding an alias must not force a
    # no-op edit to every past revision.
    operations = globals().get(name)
    if operations is not None:
        operations()


def upgrade_default() -> None:
    op.create_table(
        "source_record",
        sa.Column(
            "id",
            sa.BigInteger(),
            autoincrement=True,
            nullable=False,
            comment="레코드 고유 식별자",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="레코드 생성 시각(UTC)",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="레코드 최종 수정 시각(UTC)",
        ),
        sa.Column(
            "source_type",
            sa.Enum(
                "api",
                "crawl",
                "websocket",
                name="sourcetype",
                native_enum=False,
                create_constraint=False,
                length=20,
            ),
            nullable=False,
            comment="수집 방식(api, crawl 또는 websocket)",
        ),
        sa.Column("source", sa.Text(), nullable=False, comment="데이터 제공처 식별자(예: fred 또는 kis)"),
        sa.Column(
            "source_key",
            sa.Text(),
            nullable=False,
            comment="공급자 내 원천 식별자(예: 시계열 ID, URL 또는 배치 ID)",
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, comment="수집 시작 시각(UTC)"),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="수집 완료 시각(UTC); 진행 중이면 NULL",
        ),
        sa.Column(
            "status",
            sa.Enum(
                "running",
                "succeeded",
                "failed",
                "quarantined",
                name="sourcestatus",
                native_enum=False,
                create_constraint=False,
                length=20,
            ),
            nullable=False,
            comment="수집 상태(예: running, succeeded, failed 또는 quarantined)",
        ),
        sa.Column(
            "record_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="이 수집 단위에서 생성한 정규화 레코드 수",
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="작은 JSON 원본; 저장하지 않으면 NULL",
        ),
        sa.Column("payload_uri", sa.Text(), nullable=True, comment="대용량 원본의 외부 저장 위치; 없으면 NULL"),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment="HTTP 상태나 웹소켓 세션 ID 등 공급자별 부가 정보",
        ),
        sa.CheckConstraint(
            "source_type IN ('api', 'crawl', 'websocket')",
            name="ck_source_record_source_type",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'quarantined')",
            name="ck_source_record_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="API, 크롤링, 웹소켓 수집 단위의 출처와 상태를 보존하는 테이블",
    )
    op.create_index("ix_source_record_source_started_at", "source_record", ["source", "started_at"], unique=False)

    op.create_table(
        "indicator_observation",
        sa.Column(
            "id",
            sa.BigInteger(),
            autoincrement=True,
            nullable=False,
            comment="레코드 고유 식별자",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="레코드 생성 시각(UTC)",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="레코드 최종 수정 시각(UTC)",
        ),
        sa.Column("series_id", sa.Text(), nullable=False, comment="공급자가 정의한 시계열 식별자(예: DGS10)"),
        sa.Column("observation_date", sa.Date(), nullable=False, comment="지표 값의 기준일"),
        sa.Column("value", sa.Numeric(precision=18, scale=8), nullable=False, comment="정규화한 지표 값"),
        sa.Column("unit", sa.Text(), nullable=False, comment="지표 값의 단위(예: Percent)"),
        sa.Column("source_record_id", sa.BigInteger(), nullable=False, comment="근거가 되는 source_record 레코드 ID"),
        sa.ForeignKeyConstraint(["source_record_id"], ["source_record.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("series_id", "observation_date", name="uq_indicator_observation_natural_key"),
        comment="FRED 지표 관측값을 조회 가능한 형태로 정규화하고 원본과 연결하는 테이블",
    )
    op.create_index(
        "ix_indicator_observation_source_record_id",
        "indicator_observation",
        ["source_record_id"],
        unique=False,
    )

    op.create_table(
        "instrument",
        sa.Column(
            "id",
            sa.BigInteger(),
            autoincrement=True,
            nullable=False,
            comment="레코드 고유 식별자",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="레코드 생성 시각(UTC)",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="레코드 최종 수정 시각(UTC)",
        ),
        sa.Column("ticker", sa.Text(), nullable=False, comment="거래 시장에서 사용하는 종목 코드"),
        sa.Column(
            "market",
            sa.Enum(
                "kospi",
                "kosdaq",
                "nyse",
                "nasdaq",
                name="market",
                native_enum=False,
                create_constraint=False,
                length=20,
            ),
            nullable=False,
            comment="종목이 상장된 거래 시장(kospi, kosdaq, nyse 또는 nasdaq)",
        ),
        sa.Column("name", sa.Text(), nullable=False, comment="종목 표시 이름"),
        sa.Column(
            "kind",
            sa.Enum(
                "equity",
                "etf",
                "index",
                name="instrumentkind",
                native_enum=False,
                create_constraint=False,
                length=20,
            ),
            nullable=False,
            comment="가격 수집 소스를 가르는 유형(equity, etf 또는 index)",
        ),
        sa.Column("currency", sa.Text(), nullable=False, comment="종목 가격의 표시 통화(ISO 4217, 예: KRW 또는 USD)"),
        sa.Column(
            "source_symbol",
            sa.Text(),
            nullable=True,
            comment="수집 소스에서 쓰는 심볼. 티커와 다를 때만 채운다(예: KOSPI → ^KS11)",
        ),
        sa.Column(
            "is_watched",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
            comment="신규 데이터 수집과 분석을 수행할 추적 대상 여부",
        ),
        sa.CheckConstraint("kind IN ('equity', 'etf', 'index')", name="ck_instrument_kind"),
        sa.CheckConstraint("market IN ('kospi', 'kosdaq', 'nyse', 'nasdaq')", name="ck_instrument_market"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ticker", "market", name="uq_instrument_ticker_market"),
        comment="시세·뉴스·시그널이 참조하는 추적 종목 마스터",
    )


def downgrade_default() -> None:
    op.drop_table("instrument")
    op.drop_index("ix_indicator_observation_source_record_id", table_name="indicator_observation")
    op.drop_table("indicator_observation")
    op.drop_index("ix_source_record_source_started_at", table_name="source_record")
    op.drop_table("source_record")
