"""add token counts to thesis llm ledger

Revision ID: e7d3b1f094ac
Revises: d1a7f0c36b84
Create Date: 2026-08-27 15:10:00.000000

대화 하나가 청구된 토큰을 원장에 남긴다.

**지금까지 토큰은 LangSmith 트레이스에만 있었다.** 원장은 툴 왕복·호출 수·결과 문자 수는
세지만 그것이 얼마였는지는 안 세서, 비용을 보려면 사람이 트레이스를 하나씩 열어야 했다.
2026-08-27 비용 조사가 실제로 그렇게 돌아갔다 — 슬롯별 추이는 SQL로 못 봤다.

세 칸을 나눈 이유는 셋이 다른 손잡이에 붙기 때문이다.

- `prompt_tokens`는 **왕복마다 대화 전체가 재전송된 결과**다. 프롬프트 블록 크기와
  `MAX_TOOL_ROUNDS`가 이 값을 움직인다.
- `completion_tokens`는 출력 전체이고 `reasoning_tokens`를 **포함한다.** 제공처가 사고
  토큰도 출력 단가로 청구한다.
- `reasoning_tokens`는 대화에 안 남아 재전송되지 않고 프롬프트 캐시와도 무관하다. 위 둘과
  움직이는 이유가 달라서 한 칸으로 묶으면 어느 쪽이 늘었는지 못 가른다. 2026-08-27 실측에서
  출력의 79%였고 그중 62%가 호출 하나에 몰려 있었다.

**nullable이고 server_default가 없다.** 기존 행은 잰 적이 없어 NULL이고, 앞으로는 모델을
한 번도 못 부르고 죽은 대화라도 0이 들어간다. 0으로 메우면 "안 쟀다"와 "안 썼다"가 같아진다.

**수기 리비전이다.** `config.yaml`이 운영 DB를 가리켜 autogenerate를 돌리지 않는다.
모델(`apps/models/analysis/thesis.py`)과 여기의 컬럼 주석은 **글자 그대로** 같아야 한다.
다르면 다음 autogenerate가 매번 차이를 만든다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7d3b1f094ac"
down_revision: str | Sequence[str] | None = "d1a7f0c36b84"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROMPT_COMMENT = (
    "이 대화가 청구된 입력 토큰의 합(왕복 전부). **왕복마다 대화 전체를 다시 내므로 "
    "왕복 수가 아니라 이 값이 비용이다.** 이 칸이 생기기 전 행은 NULL이다"
)
COMPLETION_COMMENT = (
    "출력 토큰의 합. **reasoning_tokens를 포함한다** — 제공처가 사고 토큰도 출력 "
    "단가로 청구한다. 이 칸이 생기기 전 행은 NULL이다"
)
REASONING_COMMENT = (
    "그중 모델이 속으로 생각한 토큰. 대화에 남지 않아 다음 왕복에 재전송되지 않고 "
    "프롬프트 캐시와도 무관하다. 제공처가 안 알려 주면 0이다. "
    "이 칸이 생기기 전 행은 NULL이다"
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
        sa.Column("prompt_tokens", sa.Integer(), nullable=True, comment=PROMPT_COMMENT),
    )
    op.add_column(
        "thesis_llm_run",
        sa.Column("completion_tokens", sa.Integer(), nullable=True, comment=COMPLETION_COMMENT),
    )
    op.add_column(
        "thesis_llm_run",
        sa.Column("reasoning_tokens", sa.Integer(), nullable=True, comment=REASONING_COMMENT),
    )


def downgrade_default() -> None:
    op.drop_column("thesis_llm_run", "reasoning_tokens")
    op.drop_column("thesis_llm_run", "completion_tokens")
    op.drop_column("thesis_llm_run", "prompt_tokens")
