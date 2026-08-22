"""add thesis evidence claim

Revision ID: b7e2f4a18c53
Revises: a4d1e7c93b02
Create Date: 2026-08-21 22:10:00.000000

추론이 근거를 **어느 방향으로 어떤 경로로** 썼는지를 `thesis_evidence`에 두 칸으로 더한다.
이유 문장은 산문이라 그래프 엣지에 실을 수 없었다. 설계는 `docs/market-thesis/2-agent.md` 3절.

이 리비전은 **손으로 썼다.** `config.yaml`이 운영 DB를 가리켜 autogenerate를 돌리지
않는다(프로젝트 규칙). 검증은 오프라인 `head_sql` 기반 `tests/migrations/test_thesis_schema.py`가
한다. 모델(`apps/models/analysis.py`)과 여기의 컬럼 주석·CHECK 문자열은 글자 그대로 같아야 한다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e2f4a18c53"
down_revision: str | Sequence[str] | None = "a4d1e7c93b02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DIRECTION_CHECK = "ck_thesis_evidence_direction"
PAIR_CHECK = "ck_thesis_evidence_claim_all_or_none"


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
    # 기존 행(첫날 인용 97건)은 NULL로 남는다. 그때 모델에게 방향을 묻지 않았으니 지어 넣지 않는다.
    op.add_column(
        "thesis_evidence",
        sa.Column(
            "direction",
            sa.String(length=20),
            nullable=True,
            comment=(
                "이 근거가 대상을 어느 쪽으로 미는지(up/down/flat). 원 추론이 인용한 근거에만 있고 "
                "사후 해설의 인용에는 NULL이다"
            ),
        ),
    )
    op.add_column(
        "thesis_evidence",
        sa.Column(
            "mechanism",
            sa.Text(),
            nullable=True,
            comment="그 방향으로 작용하는 경로를 적은 한 문장. direction과 함께 채워지거나 함께 비어 있다",
        ),
    )
    op.create_check_constraint(
        DIRECTION_CHECK,
        "thesis_evidence",
        "direction IS NULL OR direction IN ('up', 'down', 'flat')",
    )
    op.create_check_constraint(
        PAIR_CHECK,
        "thesis_evidence",
        "(direction IS NULL AND mechanism IS NULL) OR (direction IS NOT NULL AND mechanism IS NOT NULL)",
    )


def downgrade_default() -> None:
    op.drop_constraint(PAIR_CHECK, "thesis_evidence", type_="check")
    op.drop_constraint(DIRECTION_CHECK, "thesis_evidence", type_="check")
    op.drop_column("thesis_evidence", "mechanism")
    op.drop_column("thesis_evidence", "direction")
