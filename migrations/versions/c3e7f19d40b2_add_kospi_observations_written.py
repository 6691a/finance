"""add kospi observations_written

Revision ID: c3e7f19d40b2
Revises: a1c74f0b8e35
Create Date: 2026-09-03

`kospi_llm_run.rejected`는 "검증이 버린 관찰 수"인데 남은 수가 어디에도 없었다. 관찰 엣지는
Neo4j로만 가서, Postgres만 보면 "몇 개 중 몇 개를 버렸나"를 읽을 수 없다. 분모 없는 카운터는
카운터가 아니다.

전망 대화는 이 칸이 NULL이다. 이유의 남은 수는 `kospi_forecast.reasons`가 이미 갖는다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3e7f19d40b2"
down_revision: str | Sequence[str] | None = "a1c74f0b8e35"
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
            "observations_written",
            sa.Integer(),
            nullable=True,
            comment="이 관찰이 그래프에 쓴 요인 엣지 수. `rejected`의 분모다. 전망 대화는 NULL",
        ),
    )
    op.create_check_constraint(
        "ck_kospi_llm_run_observations_kind",
        "kospi_llm_run",
        "observations_written IS NULL OR kind = 'review'",
    )
    op.create_check_constraint(
        "ck_kospi_llm_run_observations_written",
        "kospi_llm_run",
        "observations_written IS NULL OR observations_written >= 0",
    )


def downgrade_default() -> None:
    op.drop_constraint("ck_kospi_llm_run_observations_written", "kospi_llm_run", type_="check")
    op.drop_constraint("ck_kospi_llm_run_observations_kind", "kospi_llm_run", type_="check")
    op.drop_column("kospi_llm_run", "observations_written")
