"""add stock_analyst_opinion

Revision ID: a1f3c7e9b2d4
Revises: a3f9c1d27e64
Create Date: 2026-08-22 00:00:00.000000

증권사 애널리스트의 종목별 투자의견·목표주가를 담는 테이블을 만든다. 설계는
`docs/analysis/market-thesis/6-analyst.md` 2절에 있다.

이 리비전은 **손으로 썼다.** `config.yaml`이 운영 DB를 가리켜 autogenerate를 돌리지
않는다(프로젝트 규칙). 검증은 오프라인 `head_sql` 기반
`tests/migrations/test_stock_analyst_opinion_schema.py`가 한다.

모델(`apps/models/market.py`의 `StockAnalystOpinion`)과 여기의 컬럼 주석·제약 이름은 글자
그대로 같아야 한다. 어긋나면 다음 autogenerate가 차이를 만들어 낸다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1f3c7e9b2d4"
down_revision: str | Sequence[str] | None = "a3f9c1d27e64"
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
        "stock_analyst_opinion",
        *_entity_columns(),
        sa.Column("provider", sa.Text(), nullable=False, comment="데이터 제공처 식별자(kis)"),
        sa.Column("stock_code", sa.Text(), nullable=False, comment="한국거래소 종목코드 6자리(예: 005930)"),
        sa.Column(
            "business_date",
            sa.Date(),
            nullable=False,
            comment="투자의견 발표 영업일(stck_bsop_date). 기준 시간대는 한국이다",
        ),
        sa.Column(
            "broker_name",
            sa.Text(),
            nullable=False,
            comment="투자의견을 낸 증권사(mbcr_name). KIS 표기 그대로의 약칭이다(예: 키움, 한국투자, 신한투자증권)",
        ),
        sa.Column(
            "opinion",
            sa.Text(),
            nullable=False,
            comment="투자의견(invt_opnn). 증권사마다 표기가 달라 BUY와 매수가 섞여 온다. 기계 판독은 opinion_code로 한다",
        ),
        sa.Column("opinion_code", sa.Text(), nullable=False, comment="투자의견 구분코드(invt_opnn_cls_code)"),
        sa.Column(
            "previous_opinion",
            sa.Text(),
            nullable=False,
            comment="같은 증권사의 직전 투자의견(rgbf_invt_opnn). 표기 규칙은 opinion과 같다",
        ),
        sa.Column(
            "previous_opinion_code",
            sa.Text(),
            nullable=False,
            comment="직전 투자의견 구분코드(rgbf_invt_opnn_cls_code)",
        ),
        sa.Column(
            "target_price", sa.Numeric(precision=18, scale=4), nullable=False, comment="목표주가(hts_goal_prc). 원"
        ),
        sa.Column(
            "previous_close",
            sa.Numeric(precision=18, scale=4),
            nullable=False,
            comment="발표 전일 종가(stck_prdy_clpr). 원",
        ),
        sa.Column(
            "gap_amount",
            sa.Numeric(precision=18, scale=4),
            nullable=False,
            comment="발표 전일 종가에서 목표주가를 뺀 괴리(stck_nday_esdg). 원. 음수면 목표가가 종가보다 높다",
        ),
        sa.Column(
            "gap_rate",
            sa.Numeric(precision=12, scale=4),
            nullable=False,
            comment="목표주가 대비 괴리율(nday_dprt). KIS 표기 그대로의 퍼센트",
        ),
        sa.Column(
            "source_record_id",
            sa.BigInteger(),
            nullable=False,
            comment="이 행을 마지막으로 갱신한 수집의 source_record 레코드 ID",
        ),
        sa.ForeignKeyConstraint(["source_record_id"], ["source_record.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "stock_code",
            "business_date",
            "broker_name",
            name="uq_stock_analyst_opinion_natural_key",
        ),
        comment="증권사 애널리스트의 종목별 투자의견·목표주가를 발표일 단위로 누적하는 테이블",
    )
    op.create_index(
        "ix_stock_analyst_opinion_source_record_id",
        "stock_analyst_opinion",
        ["source_record_id"],
        unique=False,
    )


def downgrade_default() -> None:
    op.drop_index("ix_stock_analyst_opinion_source_record_id", table_name="stock_analyst_opinion")
    op.drop_table("stock_analyst_opinion")
