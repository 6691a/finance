"""add stock_bar ingest method and finality

Revision ID: d41f7c9b3a12
Revises: d8a4c1f27b93
Create Date: 2026-08-18 21:30:00.000000

WebSocket 실시간 수집기가 잠정 1분봉을 쓰기 시작한다. REST 확정봉이 WebSocket
잠정봉을 항상 이기는 규칙(문서 5.2)을 DB가 강제하려면 행마다 수집 경로와 확정
여부가 있어야 한다.

- 기존 행은 전부 `kis_stock_minute_bars_daily`(REST)가 썼으므로 server_default로
  `'rest'`/`true`를 백필하고 곧바로 default를 제거한다. 앞으로의 INSERT는 두 컬럼을
  명시해야 한다 — 이 리비전을 모르는 SQL이 조용히 기본값으로 저장되는 것보다
  터지는 편이 낫다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d41f7c9b3a12"
down_revision: str | Sequence[str] | None = "d8a4c1f27b93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INGEST_METHOD_CHECK = "ck_stock_bar_ingest_method"


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
    op.add_column(
        "stock_bar",
        sa.Column(
            "ingest_method",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'rest'"),
            comment="이 행을 마지막으로 쓴 수집 경로(websocket 또는 rest). REST 확정이 WebSocket 잠정을 이긴다",
        ),
    )
    op.add_column(
        "stock_bar",
        sa.Column(
            "is_final",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
            comment="REST가 완료 봉을 확정했는지. WebSocket 잠정 봉은 false이고 REST upsert만 true로 바꾼다",
        ),
    )
    op.alter_column("stock_bar", "ingest_method", server_default=None)
    op.alter_column("stock_bar", "is_final", server_default=None)
    op.create_check_constraint(
        INGEST_METHOD_CHECK,
        "stock_bar",
        "ingest_method IN ('websocket', 'rest')",
    )


def downgrade_default() -> None:
    op.drop_constraint(INGEST_METHOD_CHECK, "stock_bar", type_="check")
    op.drop_column("stock_bar", "is_final")
    op.drop_column("stock_bar", "ingest_method")
