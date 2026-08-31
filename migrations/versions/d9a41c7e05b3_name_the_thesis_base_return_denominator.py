"""name the thesis base return denominator

Revision ID: d9a41c7e05b3
Revises: d7a41f8b2c93
Create Date: 2026-08-31 15:00:00.000000

`thesis.base_return_pct`의 주석만 고친다. 컬럼도 제약도 그대로다.

전 주석의 "'오늘 여기까지'"는 **분모를 말하지 않았다.** 국내 정규장은 개장 갭이 있어
전일 종가 대비와 오늘 시가 대비가 다른 값인데, 읽는 쪽이 시가 대비로 읽었다(2026-08-31
실제 오독). 채점은 전일 종가 대비로 하므로 그 오독은 예측 크기의 해석까지 바꾼다.

같은 문장을 Slack 축 줄(`modules/thesis/render.py`)과 조회 API 설명
(`apps/api/schemas/thesis.py`)에서 함께 고쳤다.

**수기 리비전이다.** `config.yaml`이 운영 DB를 가리켜 autogenerate를 돌리지 않는다.
모델(`apps/models/analysis/thesis.py`)과 여기의 주석 문자열은 **글자 그대로** 같아야 한다.
다르면 다음 autogenerate가 매번 `COMMENT ON` 차이를 만든다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d9a41c7e05b3"
down_revision: str | Sequence[str] | None = "d7a41f8b2c93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BASE_RETURN_COMMENT = (
    "**전일(직전 세션) 확정 종가 대비** base_price까지 **이미 온** 등락률(퍼센트). "
    "오늘 시가 대비가 아니다 — 개장 갭이 이 값에 들어 있다. 장중이면 '전일 종가 대비 "
    "현재까지'이고 장전·장후는 정의상 0이다. 예측 크기와 더하면 하루 등락이 된다"
)
PREVIOUS_BASE_RETURN_COMMENT = (
    "직전 세션 확정 종가에서 base_price까지 **이미 온** 등락률(퍼센트). 장중이면 "
    "'오늘 여기까지'이고 장전·장후는 정의상 0이다. 예측 크기와 더하면 하루 등락이 된다"
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
    op.alter_column(
        "thesis",
        "base_return_pct",
        existing_type=sa.Numeric(8, 4),
        existing_nullable=True,
        existing_comment=PREVIOUS_BASE_RETURN_COMMENT,
        comment=BASE_RETURN_COMMENT,
    )


def downgrade_default() -> None:
    op.alter_column(
        "thesis",
        "base_return_pct",
        existing_type=sa.Numeric(8, 4),
        existing_nullable=True,
        existing_comment=BASE_RETURN_COMMENT,
        comment=PREVIOUS_BASE_RETURN_COMMENT,
    )
