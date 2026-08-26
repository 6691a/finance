"""add stock event tables

Revision ID: a4c9e1f7b3d6
Revises: b7e2f4a18c53
Create Date: 2026-08-24 00:00:00.000000

종목 이벤트 기대치·서프라이즈 판정 테이블 셋을 만든다. 설계는
`docs/analysis/market-thesis/8-expectation.md`에 있다.

이 리비전은 **손으로 썼다.** `config.yaml`이 운영 DB를 가리켜 autogenerate를 돌리지
않는다(프로젝트 규칙). 검증은 오프라인 `head_sql` 기반
`tests/migrations/test_stock_event_schema.py`가 한다.

모델(`apps/models/analysis.py`)과 여기의 컬럼 주석·CHECK 문자열은 글자 그대로 같아야 한다.
어긋나면 다음 autogenerate가 차이를 만들어 낸다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4c9e1f7b3d6"
down_revision: str | Sequence[str] | None = "b7e2f4a18c53"
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


def _entity_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="레코드 고유 식별자"),
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
    ]


def upgrade_default() -> None:
    op.create_table(
        "stock_event_claim",
        sa.Column(
            "stock_code",
            sa.Text(),
            nullable=False,
            comment="한국거래소 종목코드(예: 005930). instrument로 외래키를 걸지 않는다",
        ),
        sa.Column(
            "event_type",
            sa.String(length=20),
            nullable=False,
            comment="이벤트 종류(shareholder_return, earnings, guidance)",
        ),
        sa.Column(
            "period_key",
            sa.Text(),
            nullable=False,
            comment="대상 기간 표기. 연간(2026), 분기(2026Q2), 반기(2026H1)만 허용한다. 기대와 실제를 잇는 키의 한 축이다",
        ),
        sa.Column(
            "metric",
            sa.String(length=20),
            nullable=False,
            comment="이벤트 지표. 단위는 지표가 정하며 전부 원(KRW)이다. 실적 지표는 earnings_fact.metric과 같은 값이다",
        ),
        sa.Column(
            "claim_kind",
            sa.String(length=20),
            nullable=False,
            comment="주장의 종류(expectation은 기대치, actual은 실제 발표값)",
        ),
        sa.Column(
            "value",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="주장 값. 원문 표기(조·억)를 원 단위로 정규화한 값이다. 범위 주장이면 중앙값을 둔다",
        ),
        sa.Column(
            "value_low",
            sa.Numeric(precision=24, scale=2),
            nullable=True,
            comment="범위 주장의 하한(원). 단일 값 주장이면 NULL이고 value_high와 함께 차거나 함께 빈다",
        ),
        sa.Column(
            "value_high",
            sa.Numeric(precision=24, scale=2),
            nullable=True,
            comment="범위 주장의 상한(원). 단일 값 주장이면 NULL이다",
        ),
        sa.Column(
            "stated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment=(
                "주장 시점(UTC). 문서면 published_at(없으면 detected_at), 컨센서스면 조회 시각이다. "
                "판정이 발표 전 기대만 고르는 기준이라 모델이 아니라 코드가 채운다"
            ),
        ),
        sa.Column(
            "broker",
            sa.Text(),
            nullable=True,
            comment="주장 주체 표기(증권사 등, 문서 제목 끝 낱말). 기사 인용처럼 주체를 모르면 NULL이고 컨센서스도 NULL이다",
        ),
        sa.Column(
            "document_id",
            sa.BigInteger(),
            nullable=True,
            comment="주장을 추출한 문서 ID. 컨센서스 수집이면 NULL이다",
        ),
        sa.Column(
            "source_record_id",
            sa.BigInteger(),
            nullable=True,
            comment="컨센서스 수집의 source_record 레코드 ID. LLM 추출이면 NULL이다",
        ),
        *_entity_columns(),
        sa.CheckConstraint(
            "event_type IN ('shareholder_return', 'earnings', 'guidance')",
            name="ck_stock_event_claim_event_type",
        ),
        sa.CheckConstraint(
            "metric IN ('total_return_amount', 'buyback_amount', 'dividend_total',"
            " 'dividend_per_share', 'revenue', 'operating_profit', 'net_income')",
            name="ck_stock_event_claim_metric",
        ),
        sa.CheckConstraint(
            "claim_kind IN ('expectation', 'actual')",
            name="ck_stock_event_claim_kind",
        ),
        sa.CheckConstraint(
            "period_key ~ '^[0-9]{4}(Q[1-4]|H[12])?$'",
            name="ck_stock_event_claim_period_key",
        ),
        sa.CheckConstraint(
            "(value_low IS NULL AND value_high IS NULL)"
            " OR (value_low IS NOT NULL AND value_high IS NOT NULL AND value_low <= value_high)",
            name="ck_stock_event_claim_range_pair",
        ),
        sa.CheckConstraint(
            "(document_id IS NULL) <> (source_record_id IS NULL)",
            name="ck_stock_event_claim_source_xor",
        ),
        sa.ForeignKeyConstraint(["document_id"], ["document.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_record_id"], ["source_record.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "event_type",
            "period_key",
            "metric",
            "claim_kind",
            name="uq_stock_event_claim_document_claim",
        ),
        comment="종목 이벤트에 대한 기대·실제 주장을 출처와 함께 누적하는 테이블",
        info={"database": "default", "managed": True},
    )
    op.create_index("ix_stock_event_claim_event", "stock_event_claim", ["stock_code", "event_type", "period_key"])
    op.create_index("ix_stock_event_claim_source_record_id", "stock_event_claim", ["source_record_id"])

    op.create_table(
        "stock_event_extraction",
        sa.Column("document_id", sa.BigInteger(), nullable=False, comment="추출한 문서 ID"),
        sa.Column(
            "extracted_content_hash",
            sa.Text(),
            nullable=False,
            comment="추출 시점의 document.content_hash. 현재 값과 다르면 본문이 바뀐 것이라 다시 뽑는다",
        ),
        sa.Column(
            "extracted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="추출을 마친 시각(UTC)",
        ),
        sa.Column("llm_model", sa.Text(), nullable=False, comment="추출에 쓴 모델 식별자"),
        sa.Column(
            "prompt_version",
            sa.Text(),
            nullable=False,
            comment="추출 프롬프트 판. 이 값이 오른 문서는 재추출 대상이 된다",
        ),
        sa.Column(
            "claim_count",
            sa.Integer(),
            nullable=False,
            comment="이 문서에서 저장된 주장 수. 0이 정상값이다 — 대부분 문서에는 이벤트 주장이 없다",
        ),
        *_entity_columns(),
        sa.CheckConstraint("claim_count >= 0", name="ck_stock_event_extraction_claim_count"),
        sa.ForeignKeyConstraint(["document_id"], ["document.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", name="uq_stock_event_extraction_document"),
        comment="문서별 이벤트 주장 추출 이력을 남기는 원장 테이블. 주장 0건도 기록한다",
        info={"database": "default", "managed": True},
    )

    op.create_table(
        "stock_event_outcome",
        sa.Column(
            "stock_code",
            sa.Text(),
            nullable=False,
            comment="한국거래소 종목코드(예: 005930). instrument로 외래키를 걸지 않는다",
        ),
        sa.Column(
            "event_type",
            sa.String(length=20),
            nullable=False,
            comment="이벤트 종류(shareholder_return, earnings, guidance)",
        ),
        sa.Column(
            "period_key",
            sa.Text(),
            nullable=False,
            comment="대상 기간 표기(2026, 2026Q2, 2026H1). stock_event_claim과 같은 규칙이다",
        ),
        sa.Column(
            "metric",
            sa.String(length=20),
            nullable=False,
            comment="이벤트 지표. 단위는 지표가 정하며 전부 원(KRW)이다",
        ),
        sa.Column(
            "expected_value",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="판정에 쓴 대표 기대치(원). 컨센서스가 있으면 최신 컨센서스, 없으면 주체별 최신 기대의 중앙값이다",
        ),
        sa.Column(
            "expectation_count",
            sa.Integer(),
            nullable=False,
            comment="대조한 기대 행 수. 기대가 없던 발표는 판정하지 않으므로 항상 1 이상이다",
        ),
        sa.Column(
            "actual_value",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="실제 발표값(원). 실적은 earnings_fact, 그 외는 actual 주장에서 온다",
        ),
        sa.Column(
            "surprise_pct",
            sa.Numeric(precision=10, scale=4),
            nullable=False,
            comment="(실제 - 기대) / |기대| × 100. 기대 대비 어긋난 정도(퍼센트)다",
        ),
        sa.Column(
            "verdict",
            sa.String(length=20),
            nullable=False,
            comment="판정(beat/meet/miss). |surprise_pct|가 허용 밴드 안이면 meet, 밖이면 부호로 가른다. LLM이 만들지 않는다",
        ),
        sa.Column(
            "announced_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="실제값 원본의 발행·감지 시각(UTC). 이 시각 전의 기대만 판정에 들어간다",
        ),
        sa.Column(
            "actual_ref",
            sa.Text(),
            nullable=False,
            comment="실제값 원본 참조(earnings_fact:<id> 또는 document:<id>). thesis_evidence.evidence_ref와 같은 2단 표기다",
        ),
        sa.Column("dag_run_id", sa.Text(), nullable=False, comment="이 행을 쓴 Airflow dag_run_id"),
        *_entity_columns(),
        sa.CheckConstraint(
            "event_type IN ('shareholder_return', 'earnings', 'guidance')",
            name="ck_stock_event_outcome_event_type",
        ),
        sa.CheckConstraint(
            "metric IN ('total_return_amount', 'buyback_amount', 'dividend_total',"
            " 'dividend_per_share', 'revenue', 'operating_profit', 'net_income')",
            name="ck_stock_event_outcome_metric",
        ),
        sa.CheckConstraint(
            "verdict IN ('beat', 'meet', 'miss')",
            name="ck_stock_event_outcome_verdict",
        ),
        sa.CheckConstraint(
            "period_key ~ '^[0-9]{4}(Q[1-4]|H[12])?$'",
            name="ck_stock_event_outcome_period_key",
        ),
        sa.CheckConstraint("expectation_count > 0", name="ck_stock_event_outcome_expectation_count"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "stock_code",
            "event_type",
            "period_key",
            "metric",
            name="uq_stock_event_outcome_natural_key",
        ),
        comment="이벤트 지표 하나의 기대 대비 실제 판정을 불변으로 보존하는 테이블",
        info={"database": "default", "managed": True},
    )


def downgrade_default() -> None:
    op.drop_table("stock_event_outcome")
    op.drop_table("stock_event_extraction")
    op.drop_index("ix_stock_event_claim_source_record_id", table_name="stock_event_claim")
    op.drop_index("ix_stock_event_claim_event", table_name="stock_event_claim")
    op.drop_table("stock_event_claim")
