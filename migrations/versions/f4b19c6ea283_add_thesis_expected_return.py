"""add thesis expected return

Revision ID: f4b19c6ea283
Revises: e3b7c14da902
Create Date: 2026-08-26 22:00:00.000000

방향별 기대 등락률과 그 채점을 더한다. 설계는
`docs/analysis/market-thesis/11-expected-return.md`에 있다.

- `thesis.up_return_pct`·`down_return_pct` — **조건부** 크기다. "상승한다면 몇 %"이고
  확률을 곱한 기대값이 아니다. `flat`은 정의가 이미 "±임계 안"이라 칸을 두지 않는다.
- `thesis_outcome.predicted_return_pct`·`return_error_pct` — 실현된 방향의 조건부 추정만
  실제와 대조한 결과다. 오차는 **부호를 유지한다**(양수 과소, 음수 과대).
- `thesis_precedent.precedent_id` 인덱스 — UNIQUE가 `(thesis_id, precedent_id)`라 선두만
  커버한다. 조회 API의 이웃 그래프가 **들어오는** `INFORMED_BY`를 읽어 이 인덱스가 필요하다
  (`docs/analysis/market-thesis/12-api.md`).

**넷 다 nullable이다.** `thesis`는 사후 갱신하지 않는 테이블이라 이 리비전 전에 쌓인 행을
채울 방법이 없다. `thesis_outcome`의 둘도 flat 실현·지평 1·3·5에서 정상적으로 비어 있다.

**수기 리비전이다.** `config.yaml`이 운영 DB를 가리켜 autogenerate를 돌리지 않는다.
검증은 오프라인 `head_sql` 기반 `tests/migrations/test_thesis_schema.py`가 한다.

모델(`apps/models/analysis/thesis.py`)과 여기의 CHECK 문자열·컬럼 주석은 **글자 그대로**
같아야 한다. 다르면 다음 autogenerate가 매번 차이를 만든다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4b19c6ea283"
down_revision: str | Sequence[str] | None = "e3b7c14da902"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RETURN_RANGE_CHECK = "ck_thesis_return_pct_range"
RETURN_ERROR_PAIR_CHECK = "ck_thesis_outcome_return_error_all_or_none"
RETURN_ERROR_GRADE_CHECK = "ck_thesis_outcome_return_error_needs_grade"
PRECEDENT_INDEX = "ix_thesis_precedent_precedent_id"

UP_RETURN_COMMENT = (
    "상승한다는 조건에서 채점 창의 등락률(퍼센트, 양수). 확률을 곱하지 않은 조건부 크기다. "
    "창은 확률과 같은 지평 0이다"
)
DOWN_RETURN_COMMENT = (
    "하락한다는 조건에서 채점 창의 등락률(퍼센트, **양수 크기**). 확률을 곱하지 않은 "
    "조건부 크기다. 창은 확률과 같은 지평 0이다"
)
PREDICTED_RETURN_COMMENT = (
    "실현된 방향에 대응하는 thesis의 조건부 크기 스냅샷(퍼센트, 양수). "
    "지평 0에서만, actual_outcome이 flat이 아닐 때만 채운다"
)
RETURN_ERROR_COMMENT = (
    "abs(actual_return_pct) - predicted_return_pct(퍼센트포인트). **부호를 유지한다** — "
    "양수면 과소추정, 음수면 과대추정이다. 절댓값 평균(MAE)은 조회가 만든다"
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
        "thesis",
        sa.Column("up_return_pct", sa.Numeric(5, 2), nullable=True, comment=UP_RETURN_COMMENT),
    )
    op.add_column(
        "thesis",
        sa.Column("down_return_pct", sa.Numeric(5, 2), nullable=True, comment=DOWN_RETURN_COMMENT),
    )
    op.create_check_constraint(
        RETURN_RANGE_CHECK,
        "thesis",
        "(up_return_pct IS NULL OR up_return_pct BETWEEN 0 AND 30)"
        " AND (down_return_pct IS NULL OR down_return_pct BETWEEN 0 AND 30)",
    )

    op.add_column(
        "thesis_outcome",
        sa.Column("predicted_return_pct", sa.Numeric(5, 2), nullable=True, comment=PREDICTED_RETURN_COMMENT),
    )
    op.add_column(
        "thesis_outcome",
        sa.Column("return_error_pct", sa.Numeric(8, 4), nullable=True, comment=RETURN_ERROR_COMMENT),
    )
    op.create_check_constraint(
        RETURN_ERROR_PAIR_CHECK,
        "thesis_outcome",
        "(return_error_pct IS NULL) = (predicted_return_pct IS NULL)",
    )
    op.create_check_constraint(
        RETURN_ERROR_GRADE_CHECK,
        "thesis_outcome",
        "predicted_return_pct IS NULL OR evaluated_at IS NOT NULL",
    )

    op.create_index(PRECEDENT_INDEX, "thesis_precedent", ["precedent_id"])


def downgrade_default() -> None:
    op.drop_index(PRECEDENT_INDEX, table_name="thesis_precedent")
    op.drop_constraint(RETURN_ERROR_GRADE_CHECK, "thesis_outcome", type_="check")
    op.drop_constraint(RETURN_ERROR_PAIR_CHECK, "thesis_outcome", type_="check")
    op.drop_column("thesis_outcome", "return_error_pct")
    op.drop_column("thesis_outcome", "predicted_return_pct")
    op.drop_constraint(RETURN_RANGE_CHECK, "thesis", type_="check")
    op.drop_column("thesis", "down_return_pct")
    op.drop_column("thesis", "up_return_pct")
