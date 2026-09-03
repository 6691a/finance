"""drop thesis and causal tables

Revision ID: d5b8c204e7f1
Revises: c3e7f19d40b2
Create Date: 2026-09-03

옛 시장 추론과 주간 인과 그래프의 표 열둘을 지운다. 코드·문서는 커밋 `45d85a6`이 이미
지웠고 이 리비전이 스키마를 따라간다.

**지우는 순서는 외래키를 따른다.** 자식이 먼저다 — PostgreSQL이 참조되는 표의 DROP을
거절한다. `thesis_llm_run`이 마지막인 이유는 추론과 인과 양쪽이 그것을 참조했기 때문이다.

`technical_signal.rule_version`의 주석이 `thesis.prompt_version`을 가리키고 있었다. 모델만
고치면 autogenerate가 매번 `COMMENT ON` 차이를 내므로 여기서 함께 고친다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5b8c204e7f1"
down_revision: str | Sequence[str] | None = "c3e7f19d40b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 자식 -> 부모 순서. 이 목록의 순서가 그대로 DROP 순서다.
#
#   market_causal_step      -> market_causal_path, market_channel
#   market_causal_evidence  -> market_causal_path
#   market_causal_path      -> market_event, thesis_llm_run
#   market_causal_direction -> thesis_llm_run
#   thesis_outcome·evidence·precedent -> thesis
#   thesis_tool_call        -> thesis_llm_run
#   thesis                  -> thesis_llm_run
DROP_ORDER = (
    "market_causal_step",
    "market_causal_evidence",
    "market_causal_path",
    "market_causal_direction",
    "market_event",
    "market_channel",
    "thesis_outcome",
    "thesis_evidence",
    "thesis_precedent",
    "thesis_tool_call",
    "thesis",
    "thesis_llm_run",
)

RULE_VERSION_COMMENT = "검출 규칙 버전(modules/technical/indicators.py의 RULE_VERSION)"
OLD_RULE_VERSION_COMMENT = (
    "검출 규칙 버전(modules/technical/indicators.py의 RULE_VERSION). thesis.prompt_version과 같은 역할이다"
)


def upgrade(engine_name: str) -> None:
    globals().get(f"upgrade_{engine_name}", lambda: None)()


def downgrade(engine_name: str) -> None:
    globals().get(f"downgrade_{engine_name}", lambda: None)()


def upgrade_default() -> None:
    for table in DROP_ORDER:
        # 인덱스·CHECK·외래키는 표와 함께 사라진다. 따로 지우지 않는다.
        op.drop_table(table)

    op.alter_column(
        "technical_signal",
        "rule_version",
        existing_type=sa.Text(),
        existing_nullable=False,
        comment=RULE_VERSION_COMMENT,
        existing_comment=OLD_RULE_VERSION_COMMENT,
    )


def downgrade_default() -> None:
    """**표를 되살리지 않는다.**

    빈 표 열둘을 다시 만들어도 3,250행은 돌아오지 않고, 그것을 쓰던 모델 클래스와 SQL도
    이미 없다. 되살릴 일이 생기면 `git revert`로 코드를 되돌리고 그 표를 만든 리비전들을
    다시 타는 것이 유일한 길이다.

    주석은 되돌린다 — 그것만은 데이터가 아니라 스키마라 정직하게 뒤집을 수 있다.
    """
    op.alter_column(
        "technical_signal",
        "rule_version",
        existing_type=sa.Text(),
        existing_nullable=False,
        comment=OLD_RULE_VERSION_COMMENT,
        existing_comment=RULE_VERSION_COMMENT,
    )
