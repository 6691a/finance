"""add disclosure_event body

Revision ID: c9f30e1ab745
Revises: b2e75f9c41a8
Create Date: 2026-08-29 17:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9f30e1ab745"
down_revision: str | Sequence[str] | None = "b2e75f9c41a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 공시 목록 API는 회사명·보고서명·접수번호만 준다. 인과 그래프가 그 한 줄로 사건을 만들려다
# 내용을 지어냈다 — `반기보고서 (2026.06)`을 인용하며 "AI 반도체 수출 호황"이라고 썼다.
# 원문(`document.xml`)은 이미 잠정실적에만 받고 있었고, 같은 호출이 다른 종류에도 먹힌다
# (2026-08-29 실측: 조회공시요구 220자, 파생상품거래손실발생 921자).
BODY_COMMENT = (
    "공시 원문에서 태그를 걷어낸 본문 텍스트. 시장이 반응하는 종류만 채우고 "
    "정기보고서처럼 방대한 것은 비운다. NULL은 아직 못 받았거나 대상이 아니라는 뜻이다"
)


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
        "disclosure_event",
        sa.Column("body", sa.Text(), nullable=True, comment=BODY_COMMENT),
    )


def downgrade_default() -> None:
    op.drop_column("disclosure_event", "body")
