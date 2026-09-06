"""add kospi observation coverage

Revision ID: d4f8a2b7c913
Revises: c7e41d9b3a02
Create Date: 2026-09-06

장후 관찰이 요인을 스스로 고르던 판 1에서는 안 조회한 요인의 기록이 없었고, 그 요인의
가중치는 옛 값에 얼어붙었다(설계 §8.10). 판 2는 코드가 숫자 요인 15개 값을 표로 주고 모델이
줄마다 답한다. 그때 필요한 원장 칸 둘이다.

- `observations_unanswered` — 표에 있는데 답이 없는 요인 수. 0으로 채우지 않고 센다.
- `unlisted_drivers` — 요인 목록 밖인데 오늘 움직였다고 적은 자유 문장. 가중치에 안 들어가고
  20영업일마다 세어 요인 승격 후보를 고른다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4f8a2b7c913"
down_revision: str | Sequence[str] | None = "c7e41d9b3a02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade(engine_name: str) -> None:
    globals().get(f"upgrade_{engine_name}", lambda: None)()


def downgrade(engine_name: str) -> None:
    globals().get(f"downgrade_{engine_name}", lambda: None)()


def upgrade_default() -> None:
    op.add_column(
        "kospi_llm_run",
        sa.Column(
            "observations_unanswered",
            sa.Integer(),
            nullable=True,
            comment="요인 값 표에 있는데 모델이 답하지 않은 요인 수. 0으로 채우지 않고 센다 — 0이어야 정상. 전망 대화는 NULL",
        ),
    )
    op.add_column(
        "kospi_llm_run",
        sa.Column(
            "unlisted_drivers",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="요인 목록에 없는데 오늘 움직였다고 모델이 적은 자유 문장 목록. 가중치에 안 들어간다. 요인 승격 후보를 세는 자리다",
        ),
    )
    op.create_check_constraint(
        "ck_kospi_llm_run_observations_unanswered",
        "kospi_llm_run",
        "observations_unanswered IS NULL OR observations_unanswered >= 0",
    )


def downgrade_default() -> None:
    op.drop_constraint("ck_kospi_llm_run_observations_unanswered", "kospi_llm_run", type_="check")
    op.drop_column("kospi_llm_run", "unlisted_drivers")
    op.drop_column("kospi_llm_run", "observations_unanswered")
