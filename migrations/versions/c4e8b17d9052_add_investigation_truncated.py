"""add investigation_truncated to thesis llm ledger

Revision ID: c4e8b17d9052
Revises: b6f1a92c4d70
Create Date: 2026-08-27 12:40:00.000000

조사가 왕복 상한에서 끊겼는지를 원장에 남긴다.

`MAX_TOOL_ROUNDS`에 걸리면 `generation._after_investigate`가 **조용히 답변 단계로 넘어간다.**
예외도 경고도 없어서, DB에서는 "스스로 조사를 끝낸 실행"과 "더 부르려다 끊긴 실행"이
`tool_rounds` 하나로 같아 보인다. 2026-08-27 실측에서 56건 중 12건이 상한값이었는데 그중
몇이 끊긴 것인지 셀 방법이 없었다.

이 칸이 그 수를 만든다. 다음에 상한을 올릴지는 이 값이 쌓인 뒤에 판단한다 — 값 없이
올리면 같은 자리에서 같은 질문을 반복한다.

**해설(`FollowupNarrator`) 경로는 언제나 false다.** 그쪽 그래프에는 왕복 상한이 없다.

**수기 리비전이다.** `config.yaml`이 운영 DB를 가리켜 autogenerate를 돌리지 않는다.
모델(`apps/models/analysis/thesis.py`)과 여기의 컬럼 주석은 **글자 그대로** 같아야 한다.
다르면 다음 autogenerate가 매번 차이를 만든다.

`server_default`를 두는 것은 이미 있는 행 때문이다. 기존 행은 끊겼는지 알 수 없으므로
`false`로 들어가고, 그 사실은 이 리비전 날짜가 말한다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4e8b17d9052"
down_revision: str | Sequence[str] | None = "b6f1a92c4d70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMN_COMMENT = (
    "모델이 툴을 더 부르겠다고 했는데 MAX_TOOL_ROUNDS에서 끊긴 실행인지. "
    "끊긴 실행은 조용히 답변으로 넘어가므로 이 칸이 없으면 스스로 끝낸 실행과 "
    "tool_rounds 하나로는 구분되지 않는다. 상한을 올릴지 판단하는 근거다"
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
        "thesis_llm_run",
        sa.Column(
            "investigation_truncated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment=COLUMN_COMMENT,
        ),
    )


def downgrade_default() -> None:
    op.drop_column("thesis_llm_run", "investigation_truncated")
