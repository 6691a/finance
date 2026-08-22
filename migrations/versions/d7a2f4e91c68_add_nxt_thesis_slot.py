"""add nxt thesis slot

Revision ID: d7a2f4e91c68
Revises: c2d9e4f1a7b3
Create Date: 2026-08-22 15:10:00.000000

`thesis.run_slot`에 `post_nxt_close`(NXT 애프터마켓 리뷰)를 더한다. 설계는
`docs/market-thesis/7-nxt-review.md`에 있다.

**수기 리비전이다.** `config.yaml`이 운영 DB를 가리켜 autogenerate를 돌리지 않는다.
게다가 autogenerate는 CHECK 제약을 비교하지 않으므로 이 변경은 어차피 손으로 써야 한다.
검증은 오프라인 `head_sql` 기반 `tests/migrations/test_thesis_schema.py`가 한다.

모델(`apps/models/analysis.py`)과 여기의 CHECK 문자열·컬럼 주석은 **글자 그대로** 같아야
한다. 다르면 다음 autogenerate가 매번 차이를 만든다.

**`downgrade_default()`는 값 집합을 되돌리기만 한다.** 그 시점에 `post_nxt_close` 행이
남아 있으면 CHECK 생성이 실패하고 **그게 맞다** — 되돌리기가 데이터를 조용히 지우지
않는다. 지울지는 사람이 정한다.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7a2f4e91c68"
down_revision: str | Sequence[str] | None = "c2d9e4f1a7b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SLOT_CHECK = "ck_thesis_run_slot"

# 슬롯 축이 바뀌면 CHECK와 컬럼 주석 **둘 다** 옛 값 집합을 들고 있게 된다.
# 주석이 값을 나열하므로 함께 다시 찍는다(`b91f4e2a6c53`의 거래소 축과 같은 형태).
SLOTS_WITH_NXT = ("pre_open", "post_close", "post_nxt_close")
SLOTS_BEFORE_NXT = ("pre_open", "post_close")

SLOT_COMMENT_WITH_NXT = (
    "추론을 만든 슬롯(pre_open은 장전 전망, post_close는 장후 리뷰, "
    "post_nxt_close는 NXT 애프터마켓 리뷰). 슬롯이 곧 추론의 종류다"
)
SLOT_COMMENT_BEFORE_NXT = "추론을 만든 슬롯(pre_open은 장전 전망, post_close는 장후 리뷰). 슬롯이 곧 추론의 종류다"

# `as_of_at` 주석도 슬롯마다의 기준 시각을 나열한다. 슬롯이 늘면 여기도 늘어야 한다.
AS_OF_COMMENT_WITH_NXT = (
    "관측 상태와 툴 조회의 기준 시각(UTC). 벽시계가 아니라 슬롯이 정한다"
    "(장전 = 당일 08:35 KST, 장후 = 당일 15:30 KST, 애프터마켓 = 당일 20:00 KST). "
    "event-time cutoff라 이 시각 이후 감지·평가·갱신된 행은 조회에서 뺀다"
)
AS_OF_COMMENT_BEFORE_NXT = (
    "관측 상태와 툴 조회의 기준 시각(UTC). 벽시계가 아니라 슬롯이 정한다"
    "(장전 = 당일 08:35 KST, 장후 = 당일 15:30 KST). "
    "event-time cutoff라 이 시각 이후 감지·평가·갱신된 행은 조회에서 뺀다"
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


def _set_slot_axis(slots: tuple[str, ...], slot_comment: str, as_of_comment: str) -> None:
    """슬롯 축(CHECK·컬럼 주석 둘)을 주어진 목록으로 다시 선언한다."""
    check = "run_slot IN ({})".format(", ".join(f"'{value}'" for value in slots))
    op.drop_constraint(SLOT_CHECK, "thesis", type_="check")
    op.create_check_constraint(SLOT_CHECK, "thesis", check)
    op.execute(f"COMMENT ON COLUMN thesis.run_slot IS '{slot_comment}'")
    op.execute(f"COMMENT ON COLUMN thesis.as_of_at IS '{as_of_comment}'")


def upgrade_default() -> None:
    _set_slot_axis(SLOTS_WITH_NXT, SLOT_COMMENT_WITH_NXT, AS_OF_COMMENT_WITH_NXT)


def downgrade_default() -> None:
    _set_slot_axis(SLOTS_BEFORE_NXT, SLOT_COMMENT_BEFORE_NXT, AS_OF_COMMENT_BEFORE_NXT)
