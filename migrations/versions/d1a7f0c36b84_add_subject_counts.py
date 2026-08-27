"""add subject counts to thesis llm ledger

Revision ID: d1a7f0c36b84
Revises: c4e8b17d9052
Create Date: 2026-08-27 13:20:00.000000

요청한 추론 대상 수와 실제로 답이 온 수를 원장에 남긴다.

2026-08-27 `intraday_midday`가 대상 넷을 조사해 놓고 **하나만 답했다.** 툴 호출 16건이
전부 성공했고(대상별 `daily_history` 네 번), 검증이 버린 것도 없었으며, 교정도 안 돌았다.
모델이 답변 한 번에 하나만 낸 것이다.

**그런데 그 사실이 어디에도 안 남았다.** `parse`는 온 것 중 버린 것만 세고 안 온 것은
세지 않아, 태스크는 `written=1`로 초록이었고 Slack에는 KOSPI 한 줄만 나갔다. 사람이 DB를
세어 보고서야 알았다.

이 두 칸이 그것을 SQL로 보이게 한다.

    SELECT run_slot, count(*) FILTER (WHERE subjects_answered < subjects_requested)
    FROM thesis_llm_run WHERE kind = 'forecast' GROUP BY run_slot;

**해설(narration) 대화는 NULL이다.** 대상 개념이 달라 0으로 넣으면 "전부 실패한 생성"과
같아 보인다.

**수기 리비전이다.** `config.yaml`이 운영 DB를 가리켜 autogenerate를 돌리지 않는다.
모델(`apps/models/analysis/thesis.py`)과 여기의 컬럼 주석은 **글자 그대로** 같아야 한다.

이미 있는 행은 몇 개를 요청했는지 알 수 없어 NULL로 남는다. 그 사실은 이 리비전 날짜가
말한다 — `server_default`를 두지 않는 이유다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d1a7f0c36b84"
down_revision: str | Sequence[str] | None = "c4e8b17d9052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REQUESTED_COMMENT = (
    "이 대화에 요청한 추론 대상 수. answered와 다르면 모델이 일부만 답한 것이다. "
    "해설(narration) 대화는 대상 개념이 달라 NULL이다 — 0으로 메우면 '전부 실패'와 같아진다"
)
ANSWERED_COMMENT = (
    "그중 실제로 저장된 추론 수. 요청보다 적으면 모델이 대상을 빠뜨린 것이고, "
    "교정을 한 번 돌린 뒤의 최종값이다. 해설 대화는 NULL이다"
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
        sa.Column("subjects_requested", sa.Integer(), nullable=True, comment=REQUESTED_COMMENT),
    )
    op.add_column(
        "thesis_llm_run",
        sa.Column("subjects_answered", sa.Integer(), nullable=True, comment=ANSWERED_COMMENT),
    )


def downgrade_default() -> None:
    op.drop_column("thesis_llm_run", "subjects_answered")
    op.drop_column("thesis_llm_run", "subjects_requested")
