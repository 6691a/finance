"""add market causal evidence

Revision ID: a7c4e1b93f28
Revises: f3b8d2e05a17
Create Date: 2026-08-28 13:10:00.000000

경로가 근거로 든 후보를 남긴다. 모델은 처음부터 `evidence_refs`를 냈고
`causal.generation.verify_paths`가 목록 밖 값을 버리기까지 했는데, **저장할 자리가 없어
검증을 마친 값을 그대로 버리고 있었다**(2026-08-28 발견).

그 결과 `confidence` 판정을 되짚을 수 없었다. `observed`는 "근거가 그 방향을 말했다"인데
어느 기사를 봤는지 DB에 없으면 그 판정이 옳은지 확인할 방법이 없다. 실제로 한 실행이 경로
서른넷을 전부 `plausible`로 냈을 때 원인을 가릴 근거가 없었다.

`ref`에 외래키를 걸지 않는다. 근거가 `document`·`disclosure_event`·`technical_signal` 셋에
흩어져 있어 걸 대상이 하나가 아니고, 걸면 마스터에 없는 근거 하나가 경로 전체를 죽인다.
`document_instrument`가 종목 마스터를 참조하지 않는 것과 같은 판단이다.

**수기 리비전이다.** `config.yaml`이 운영 DB를 가리켜 autogenerate를 돌리지 않는다.
모델(`apps/models/analysis/causal.py`)과 여기의 컬럼 주석은 **글자 그대로** 같아야 한다.
다르면 다음 autogenerate가 매번 차이를 만든다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c4e1b93f28"
down_revision: str | Sequence[str] | None = "f3b8d2e05a17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade(engine_name: str) -> None:
    globals().get(f"upgrade_{engine_name}", lambda: None)()


def downgrade(engine_name: str) -> None:
    globals().get(f"downgrade_{engine_name}", lambda: None)()


def upgrade_default() -> None:
    op.create_table(
        "market_causal_evidence",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="레코드 고유 식별자"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="레코드 생성 시각(UTC)",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="레코드 최종 수정 시각(UTC)",
        ),
        sa.Column(
            "path_id",
            sa.BigInteger(),
            nullable=False,
            comment="이 근거가 붙은 경로. 헤더가 지워지면 함께 지운다",
        ),
        sa.Column(
            "ref",
            sa.Text(),
            nullable=False,
            comment="후보 식별자. `document:84026`처럼 `<kind>:<id>` 규약이다",
        ),
        sa.ForeignKeyConstraint(["path_id"], ["market_causal_path.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("path_id", "ref", name="uq_market_causal_evidence_natural_key"),
        comment="주간 인과 그래프 경로가 인용한 근거. 판정을 되짚는 자리다",
    )


def downgrade_default() -> None:
    op.drop_table("market_causal_evidence")
