"""add thesis forecast basis and return band

Revision ID: b2e75f9c41a8
Revises: a7c4e1b93f28
Create Date: 2026-08-28 16:00:00.000000

예측의 축과 크기의 ± 오차를 행에 남긴다. 설계는
`docs/analysis/market-thesis/15-return-basis.md`에 있다.

- `thesis.base_price`·`base_at`·`base_return_pct` — 확률 셋과 크기 두 칸이 **무엇 대비인가**.
  전에는 장중 기준가가 `input_state` JSONB에만 있었고(`select_pending_grades.sql`이 그것을
  캐냈다) 장전 기준가는 아예 없어, 축을 아는 것이 슬롯 규칙을 아는 코드뿐이었다.
  **`base_at`이 `as_of_at`에서 유도되지 않는다** — 장중은 기준 시각 직전 봉을 보고 수집이
  밀리면 최대 15분 앞선 봉이다(2026-08-28 실측: `as_of_at` 03:35Z, 본 봉 03:30Z).
- `thesis.up_return_band_pct`·`down_return_band_pct` — 크기의 **± 폭**(퍼센트포인트).
  하한·상한 두 칸이 아닌 이유는 중심값이 이미 `*_return_pct`에 있어서다. 폭 하나면 구간이
  언제나 `mid ± band`라 셋이 서로 어긋날 상태가 없다.
- `thesis_outcome.predicted_band_pct` — 실현된 방향의 오차 폭 스냅샷. 적중 여부 칸은 두지
  않는다: `abs(return_error_pct) <= predicted_band_pct`가 답이고 두 칸이 이미 그 행에 있다.

**여섯 다 nullable이다.** `thesis`는 사후 갱신하지 않는 테이블이라 이 리비전 전에 쌓인 행을
채울 방법이 없다. 오차 두 칸은 그것을 요구하는 프롬프트 판이 나가기 전까지 새 행에서도
비어 있다 — 칸을 미리 함께 넣는 것은 `ACCESS EXCLUSIVE` lock 창을 두 번 잡지 않기 위해서다.

**수기 리비전이다.** `config.yaml`이 운영 DB를 가리켜 autogenerate를 돌리지 않는다.
검증은 오프라인 `head_sql` 기반 `tests/migrations/test_thesis_schema.py`가 한다.

모델(`apps/models/analysis/thesis.py`)과 여기의 CHECK 문자열·컬럼 주석은 **글자 그대로**
같아야 한다. 다르면 다음 autogenerate가 매번 차이를 만든다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2e75f9c41a8"
down_revision: str | Sequence[str] | None = "a7c4e1b93f28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BAND_RANGE_CHECK = "ck_thesis_return_band_range"
BAND_CENTER_CHECK = "ck_thesis_return_band_needs_center"
BASE_PAIR_CHECK = "ck_thesis_base_all_or_none"
BASE_POSITIVE_CHECK = "ck_thesis_base_price_positive"
OUTCOME_BAND_CHECK = "ck_thesis_outcome_band_needs_prediction"

UP_BAND_COMMENT = (
    "up_return_pct의 ± 폭(**퍼센트포인트**, 양수). 구간은 up_return_pct ± 이 값이다. "
    "상한이 아니라 폭이고, 저장 전 검증이 중심값보다 큰 폭을 버린다"
)
DOWN_BAND_COMMENT = (
    "down_return_pct의 ± 폭(**퍼센트포인트**, 양수). 구간은 down_return_pct ± 이 값이다. "
    "상한이 아니라 폭이고, 저장 전 검증이 중심값보다 큰 폭을 버린다"
)
BASE_PRICE_COMMENT = (
    "확률 셋과 크기 두 칸의 분모(장전·장후는 직전 세션 확정 종가, 장중은 그 슬롯이 실제로 "
    "본 봉의 종가). 이 가격에서 그날 정규장 마감까지가 채점 창이다"
)
BASE_AT_COMMENT = (
    "base_price가 나온 시각(UTC). **as_of_at과 다를 수 있다** — 장중은 기준 시각 직전 봉을 "
    "보고 수집이 밀리면 최대 15분(BAR_STALENESS) 앞선 봉이다"
)
BASE_RETURN_COMMENT = (
    "직전 세션 확정 종가에서 base_price까지 **이미 온** 등락률(퍼센트). 장중이면 "
    "'오늘 여기까지'이고 장전·장후는 정의상 0이다. 예측 크기와 더하면 하루 등락이 된다"
)
PREDICTED_BAND_COMMENT = (
    "실현된 방향에 대응하는 thesis의 크기 오차 폭 스냅샷(퍼센트포인트, 양수). "
    "적중은 abs(return_error_pct) <= 이 값이다. 오차를 안 받던 판의 추론은 NULL이다"
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
        sa.Column("up_return_band_pct", sa.Numeric(5, 2), nullable=True, comment=UP_BAND_COMMENT),
    )
    op.add_column(
        "thesis",
        sa.Column("down_return_band_pct", sa.Numeric(5, 2), nullable=True, comment=DOWN_BAND_COMMENT),
    )
    op.add_column(
        "thesis",
        sa.Column("base_price", sa.Numeric(18, 8), nullable=True, comment=BASE_PRICE_COMMENT),
    )
    op.add_column(
        "thesis",
        sa.Column("base_at", sa.DateTime(timezone=True), nullable=True, comment=BASE_AT_COMMENT),
    )
    op.add_column(
        "thesis",
        sa.Column("base_return_pct", sa.Numeric(8, 4), nullable=True, comment=BASE_RETURN_COMMENT),
    )
    op.create_check_constraint(
        BAND_RANGE_CHECK,
        "thesis",
        "(up_return_band_pct IS NULL OR up_return_band_pct BETWEEN 0 AND 30)"
        " AND (down_return_band_pct IS NULL OR down_return_band_pct BETWEEN 0 AND 30)",
    )
    op.create_check_constraint(
        BAND_CENTER_CHECK,
        "thesis",
        "(up_return_band_pct IS NULL OR up_return_pct IS NOT NULL)"
        " AND (down_return_band_pct IS NULL OR down_return_pct IS NOT NULL)",
    )
    op.create_check_constraint(
        BASE_PAIR_CHECK,
        "thesis",
        "(base_price IS NULL AND base_at IS NULL AND base_return_pct IS NULL)"
        " OR (base_price IS NOT NULL AND base_at IS NOT NULL AND base_return_pct IS NOT NULL)",
    )
    op.create_check_constraint(
        BASE_POSITIVE_CHECK,
        "thesis",
        "base_price IS NULL OR base_price > 0",
    )

    op.add_column(
        "thesis_outcome",
        sa.Column("predicted_band_pct", sa.Numeric(5, 2), nullable=True, comment=PREDICTED_BAND_COMMENT),
    )
    op.create_check_constraint(
        OUTCOME_BAND_CHECK,
        "thesis_outcome",
        "predicted_band_pct IS NULL OR predicted_return_pct IS NOT NULL",
    )


def downgrade_default() -> None:
    op.drop_constraint(OUTCOME_BAND_CHECK, "thesis_outcome", type_="check")
    op.drop_column("thesis_outcome", "predicted_band_pct")
    op.drop_constraint(BASE_POSITIVE_CHECK, "thesis", type_="check")
    op.drop_constraint(BASE_PAIR_CHECK, "thesis", type_="check")
    op.drop_constraint(BAND_CENTER_CHECK, "thesis", type_="check")
    op.drop_constraint(BAND_RANGE_CHECK, "thesis", type_="check")
    op.drop_column("thesis", "base_return_pct")
    op.drop_column("thesis", "base_at")
    op.drop_column("thesis", "base_price")
    op.drop_column("thesis", "down_return_band_pct")
    op.drop_column("thesis", "up_return_band_pct")
