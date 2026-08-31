"""add market causal direction

Revision ID: d7a41f8b2c93
Revises: c4e28b71fa09
Create Date: 2026-08-31 21:00:00.000000

주간 인과 그래프를 대상별로 접은 방향성 하나. 설계는
`docs/analysis/market-thesis/17-graph-query.md`에 있다.

- `market_causal_direction` — 자연키가 `(week_start, target_kind, target_code)`이고
  `ON CONFLICT DO UPDATE`다. 경로의 **파생 요약**이라 그래프가 다시 밀리면 따라 갱신된다.
  추론의 "첫 성공본 불변"과 다른 판단인데, 추론이 그때 무엇을 봤나는 `thesis.input_state`에
  관측 상태가 통째로 박혀 이미 남기 때문이다.
- `thesis_llm_run.kind`에 `causal_direction`을 더한다. 슬롯 CHECK도 함께 넓힌다 —
  슬롯 없는 종류가 `causal` 하나에서 둘이 됐다.
- `thesis_evidence.evidence_kind`에 `causal_path`를 더한다. 추론이 이 방향성이 딛고 선
  경로를 인용할 수 있어야 한다.

**수기 리비전이다.** `config.yaml`이 운영 DB를 가리켜 autogenerate를 돌리지 않는다.
검증은 오프라인 `head_sql` 기반 `tests/migrations/`가 한다.

모델(`apps/models/analysis/causal.py`·`thesis.py`)과 여기의 CHECK 문자열·컬럼 주석은
**글자 그대로** 같아야 한다. 다르면 다음 autogenerate가 매번 차이를 만든다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d7a41f8b2c93"
down_revision: str | Sequence[str] | None = "c4e28b71fa09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LLM_RUN_KIND_CHECK = "ck_thesis_llm_run_kind"
LLM_RUN_SLOT_CHECK = "ck_thesis_llm_run_slot_shape"
EVIDENCE_KIND_CHECK = "ck_thesis_evidence_kind"

OLD_LLM_RUN_KINDS = "kind IN ('forecast', 'review', 'nxt_review', 'narration', 'causal')"
NEW_LLM_RUN_KINDS = "kind IN ('forecast', 'review', 'nxt_review', 'narration', 'causal', 'causal_direction')"

OLD_SLOT_SHAPE = "(kind = 'causal' AND run_slot IS NULL) OR (kind <> 'causal' AND run_slot IS NOT NULL)"
NEW_SLOT_SHAPE = (
    "(kind IN ('causal', 'causal_direction') AND run_slot IS NULL)"
    " OR (kind NOT IN ('causal', 'causal_direction') AND run_slot IS NOT NULL)"
)

OLD_EVIDENCE_KINDS = "evidence_kind IN ('document', 'disclosure', 'macro_change', 'technical_signal')"
NEW_EVIDENCE_KINDS = (
    "evidence_kind IN ('document', 'disclosure', 'macro_change', 'technical_signal', 'causal_path')"
)


def _enum(*values: str) -> sa.Enum:
    """모델의 `_enum_column`과 같은 형태. native enum을 쓰지 않는다(프로젝트 규칙)."""
    return sa.Enum(*values, native_enum=False, length=20)


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
    op.create_table(
        "market_causal_direction",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="대리키"),
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
            comment="레코드 수정 시각(UTC)",
        ),
        sa.Column(
            "week_start",
            sa.Date(),
            nullable=False,
            comment="접은 주의 월요일(KST). market_causal_path.week_start와 같은 값이다",
        ),
        sa.Column(
            "target_kind",
            _enum("instrument", "index", "quote", "indicator"),
            nullable=False,
            comment="대상이 어느 마스터에서 오는지. market_causal_path.target_kind와 같은 값 집합이다",
        ),
        sa.Column(
            "target_code",
            sa.Text(),
            nullable=False,
            comment="대상 식별자(005930, KOSPI, US10Y 등). 마스터로 외래키를 걸지 않는다",
        ),
        sa.Column(
            "bias",
            _enum("up", "down", "mixed", "flat"),
            nullable=False,
            comment="그 주 경로들을 모은 방향. **LLM이 정한다** — 세기 다수결로는 갈리지 않는다",
        ),
        sa.Column(
            "reasoning",
            sa.Text(),
            nullable=False,
            comment="어느 채널이 우위였는지 한 문장(한국어). LLM이 쓰고 저장 전에 자른다",
        ),
        sa.Column(
            "up_count",
            sa.Integer(),
            nullable=False,
            comment="그 대상을 up으로 민 경로 수. 코드가 센다",
        ),
        sa.Column(
            "down_count",
            sa.Integer(),
            nullable=False,
            comment="그 대상을 down으로 민 경로 수. 코드가 센다",
        ),
        sa.Column(
            "flat_count",
            sa.Integer(),
            nullable=False,
            comment="방향을 못 정한 경로 수. market_causal_path.sign은 up/down뿐이라 지금은 언제나 0이다",
        ),
        sa.Column(
            "path_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment=(
                "이 방향성이 딛고 선 market_causal_path.id 전부(정수 배열). 추론이 "
                "`causal_path:<id>` 형태로 인용한다. 다중 홉은 경로 여럿을 이은 것이라 그 전부가 들어간다"
            ),
        ),
        sa.Column(
            "channels",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment=(
                '채널별 방향 집계. `[{"name": "할인율", "up": 2, "down": 1}]` 형태다. '
                "추론이 종합을 못 믿을 때 보는 재료라 종합 문장과 함께 나간다"
            ),
        ),
        sa.Column(
            "llm_run_id",
            sa.BigInteger(),
            nullable=True,
            comment="이 행을 만든 대화(thesis_llm_run.kind='causal_direction'). 원장이 지워져도 행은 남는다",
        ),
        sa.CheckConstraint("bias IN ('up', 'down', 'mixed', 'flat')", name="ck_market_causal_direction_bias"),
        sa.CheckConstraint(
            "target_kind IN ('instrument', 'index', 'quote', 'indicator')",
            name="ck_market_causal_direction_target_kind",
        ),
        sa.CheckConstraint(
            "up_count >= 0 AND down_count >= 0 AND flat_count >= 0"
            " AND up_count + down_count + flat_count > 0",
            name="ck_market_causal_direction_counts",
        ),
        sa.ForeignKeyConstraint(["llm_run_id"], ["thesis_llm_run.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "week_start",
            "target_kind",
            "target_code",
            name="uq_market_causal_direction_natural_key",
        ),
        comment="주간 인과 그래프를 대상별로 접은 방향성. 시장 추론의 관측 상태로 나간다",
    )

    # 대화 종류가 하나 늘고, 그래서 "슬롯이 없는 종류"도 하나에서 둘이 된다.
    op.drop_constraint(LLM_RUN_KIND_CHECK, "thesis_llm_run", type_="check")
    op.create_check_constraint(LLM_RUN_KIND_CHECK, "thesis_llm_run", NEW_LLM_RUN_KINDS)
    op.drop_constraint(LLM_RUN_SLOT_CHECK, "thesis_llm_run", type_="check")
    op.create_check_constraint(LLM_RUN_SLOT_CHECK, "thesis_llm_run", NEW_SLOT_SHAPE)

    op.drop_constraint(EVIDENCE_KIND_CHECK, "thesis_evidence", type_="check")
    op.create_check_constraint(EVIDENCE_KIND_CHECK, "thesis_evidence", NEW_EVIDENCE_KINDS)


def downgrade_default() -> None:
    op.drop_constraint(EVIDENCE_KIND_CHECK, "thesis_evidence", type_="check")
    op.create_check_constraint(EVIDENCE_KIND_CHECK, "thesis_evidence", OLD_EVIDENCE_KINDS)

    op.drop_constraint(LLM_RUN_SLOT_CHECK, "thesis_llm_run", type_="check")
    op.create_check_constraint(LLM_RUN_SLOT_CHECK, "thesis_llm_run", OLD_SLOT_SHAPE)
    op.drop_constraint(LLM_RUN_KIND_CHECK, "thesis_llm_run", type_="check")
    op.create_check_constraint(LLM_RUN_KIND_CHECK, "thesis_llm_run", OLD_LLM_RUN_KINDS)

    op.drop_table("market_causal_direction")
