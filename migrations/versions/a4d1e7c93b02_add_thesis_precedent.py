"""add thesis precedent

Revision ID: a4d1e7c93b02
Revises: 6e09dafae6f8
Create Date: 2026-08-21 21:30:00.000000

장전 추론이 프롬프트에서 본 과거 추론을 잇는 엣지 테이블을 만든다. 설계는
`docs/market-thesis/5-followup.md` 5절에 있다.

이 리비전은 **손으로 썼다.** `config.yaml`이 운영 DB를 가리켜 autogenerate를 돌리지
않는다(프로젝트 규칙). 검증은 오프라인 `head_sql` 기반 `tests/migrations/test_thesis_schema.py`가
한다.

모델(`apps/models/analysis.py`)과 여기의 컬럼 주석·CHECK 문자열은 글자 그대로 같아야 한다.
어긋나면 다음 autogenerate가 차이를 만들어 낸다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4d1e7c93b02"
down_revision: str | Sequence[str] | None = "6e09dafae6f8"
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
        "thesis_precedent",
        sa.Column("thesis_id", sa.BigInteger(), nullable=False, comment="과거 추론을 보고 낸 thesis 레코드 ID"),
        sa.Column(
            "precedent_id",
            sa.BigInteger(),
            nullable=False,
            comment="프롬프트에 실린 과거 thesis 레코드 ID. 같은 대상의 pre_open 추론이다",
        ),
        *_entity_columns(),
        sa.CheckConstraint("thesis_id <> precedent_id", name="ck_thesis_precedent_not_self"),
        # 추론이 지워지면 그것이 본 기록도 함께 지운다. 반대로 남이 본 과거 추론은 지우지
        # 못한다 — 지우면 "무엇을 보고 냈나"가 끊긴다.
        sa.ForeignKeyConstraint(["thesis_id"], ["thesis.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["precedent_id"], ["thesis.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("thesis_id", "precedent_id", name="uq_thesis_precedent_natural_key"),
        comment="추론이 프롬프트에서 본 과거 추론을 잇는 엣지 테이블. 피드백 루프의 기록이다",
        info={"database": "default", "managed": True},
    )


def downgrade_default() -> None:
    op.drop_table("thesis_precedent")
