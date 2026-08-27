"""add cached prompt tokens to thesis llm ledger

Revision ID: b6d02f5a91c7
Revises: e7d3b1f094ac
Create Date: 2026-08-27 16:20:00.000000

`prompt_tokens` 중 프롬프트 캐시에서 읽은 몫을 따로 센다.

**이 칸이 없으면 최적화 효과를 못 잰다.** 왕복 하나를 줄여 `prompt_tokens`가 20% 줄어도
그 20%가 전부 캐시 히트였으면 청구는 거의 그대로다. 반대로 캐시가 깨지면 왕복 수가 그대로여도
청구가 몇 배가 된다. 2026-08-27 실측에서 입력 246,395 토큰 중 캐시 적중이 21.5%였고, 그
비율은 `x-grok-conv-id` sticky 라우팅이 먹었는지에 따라 실행마다 달라진다.

`prompt_tokens`에 **포함되는** 값이라 더하지 않는다. 두 칸을 합치면 입력을 두 번 센다.

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
revision: str = "b6d02f5a91c7"
down_revision: str | Sequence[str] | None = "e7d3b1f094ac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CACHED_COMMENT = (
    "그중 프롬프트 캐시에서 읽은 입력 토큰. **prompt_tokens에 포함된다** — 제공처가 "
    "이 부분을 훨씬 싸게 청구하므로 이 칸이 없으면 prompt_tokens만으로는 실제 비용을 "
    "알 수 없다. 제공처가 안 알려 주면 0이다. 이 칸이 생기기 전 행은 NULL이다"
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
        sa.Column("cached_prompt_tokens", sa.Integer(), nullable=True, comment=CACHED_COMMENT),
    )


def downgrade_default() -> None:
    op.drop_column("thesis_llm_run", "cached_prompt_tokens")
