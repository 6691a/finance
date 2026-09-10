"""add kospi observations stale

Revision ID: e2a7c5d19b04
Revises: d4f8a2b7c913
Create Date: 2026-09-09

미국 휴장 다음 날 장후 관찰이 전날과 같은 9/4 종가를 다시 받아 `none`으로 답했고, 그 0이
"봤는데 무관"과 같은 무게로 가중치에 들어가 SOX가 하루에 0.89에서 0.50으로 내려갔다
(설계 §8.11, 2026-09-08 실측). 이제 코드가 값이 안 바뀐 줄을 표에서 빼고 여기 센다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2a7c5d19b04"
down_revision: str | Sequence[str] | None = "d4f8a2b7c913"
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
            "observations_stale",
            sa.Integer(),
            nullable=True,
            comment="값이 안 바뀌어 표에서 뺀 요인 수. 지난 관찰이 본 것과 같은 거래일의 값이거나 값이 없는 줄이다. 모델은 이 줄을 못 본다. 전망 대화는 NULL",
        ),
    )
    op.create_check_constraint(
        "ck_kospi_llm_run_observations_stale",
        "kospi_llm_run",
        "observations_stale IS NULL OR observations_stale >= 0",
    )


def downgrade_default() -> None:
    op.drop_constraint("ck_kospi_llm_run_observations_stale", "kospi_llm_run", type_="check")
    op.drop_column("kospi_llm_run", "observations_stale")
