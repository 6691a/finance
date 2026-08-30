"""add market_causal_path target source

Revision ID: d51c9a7be402
Revises: c9f30e1ab745
Create Date: 2026-08-30 09:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d51c9a7be402"
down_revision: str | Sequence[str] | None = "c9f30e1ab745"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 경로의 출발점을 사건 하나로 고정하면 "US10Y가 내려서 SOX가 올랐다"를 담을 자리가 없다.
# 그 움직임을 새 `market_event`로 만들면 화살표는 그려지지만 `target:US10Y`와
# `event:미국 국채금리 하락`이 다른 노드라 조회가 거기서 끊긴다 — 문자열만 깊어지고 홉은
# 늘지 않는다(설계 §11.4). 그래서 출발점을 사건 또는 대상 중 하나로 열고 CHECK로 막는다.
#
# **자연키를 `event_id`가 아니라 `source_key`로 옮긴다.** nullable 컬럼을 자연키에 두면
# PostgreSQL이 NULL을 서로 다른 값으로 봐서 같은 대상 출발 경로가 중복 삽입된다.
SOURCE_KEY_COMMENT = (
    "출발점을 한 칸에 담은 문자열. 사건이면 'e:<event_id>', "
    "대상이면 't:<kind>:<code>:<sign>'이다. chain_key와 같은 이유로 둔다 — "
    "nullable 컬럼을 자연키에 두면 NULL이 서로 달라 중복이 들어온다"
)
SOURCE_TARGET_KIND_COMMENT = "대상에서 출발한 경로의 원인 대상 종류. 사건 출발이면 NULL"
SOURCE_TARGET_CODE_COMMENT = (
    "대상에서 출발한 경로의 원인 대상 식별자. 같은 주 다른 경로의 대상이어야 한다. "
    "사건 출발이면 NULL"
)
SOURCE_SIGN_COMMENT = (
    "원인 대상이 그 주에 움직인 방향(up 또는 down). 실현 등락의 부호와 맞아야 하고 "
    "저장 전에 코드가 대조한다. 사건 출발이면 NULL"
)
EVENT_ID_COMMENT = (
    "이 경로의 출발 사건. 지우면 그래프가 끊기므로 RESTRICT다. "
    "대상에서 출발한 경로는 NULL이고 그때 source_target_* 셋이 채워진다"
)
CONFIDENCE_COMMENT = (
    "observed는 근거 문서가 방향을 직접 말함, endpoint_observed는 양 끝 값이 그렇게 "
    "움직임, plausible은 해석. 셋 다 인과의 증명이 아니다"
)

SOURCE_EXCLUSIVE = (
    "(event_id IS NOT NULL AND source_target_kind IS NULL"
    " AND source_target_code IS NULL AND source_sign IS NULL)"
    " OR (event_id IS NULL AND source_target_kind IS NOT NULL"
    " AND source_target_code IS NOT NULL AND source_sign IS NOT NULL)"
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
        "market_causal_path",
        sa.Column("source_key", sa.Text(), nullable=True, comment=SOURCE_KEY_COMMENT),
    )
    op.add_column(
        "market_causal_path",
        sa.Column(
            "source_target_kind",
            sa.String(length=20),
            nullable=True,
            comment=SOURCE_TARGET_KIND_COMMENT,
        ),
    )
    op.add_column(
        "market_causal_path",
        sa.Column(
            "source_target_code", sa.Text(), nullable=True, comment=SOURCE_TARGET_CODE_COMMENT
        ),
    )
    op.add_column(
        "market_causal_path",
        sa.Column(
            "source_sign", sa.String(length=20), nullable=True, comment=SOURCE_SIGN_COMMENT
        ),
    )

    # 기존 행은 전부 사건에서 출발했다. 채운 뒤에 NOT NULL을 건다.
    op.execute("UPDATE market_causal_path SET source_key = 'e:' || event_id")
    op.alter_column(
        "market_causal_path",
        "source_key",
        existing_type=sa.Text(),
        nullable=False,
        existing_comment=SOURCE_KEY_COMMENT,
    )

    op.alter_column(
        "market_causal_path",
        "event_id",
        existing_type=sa.BigInteger(),
        nullable=True,
        comment=EVENT_ID_COMMENT,
    )

    op.drop_constraint(
        "uq_market_causal_path_natural_key", "market_causal_path", type_="unique"
    )
    op.create_unique_constraint(
        "uq_market_causal_path_natural_key",
        "market_causal_path",
        ["week_start", "source_key", "target_kind", "target_code", "chain_key"],
    )

    op.drop_constraint(
        "ck_market_causal_path_confidence", "market_causal_path", type_="check"
    )
    op.create_check_constraint(
        "ck_market_causal_path_confidence",
        "market_causal_path",
        "confidence IN ('observed', 'endpoint_observed', 'plausible')",
    )
    op.alter_column(
        "market_causal_path",
        "confidence",
        existing_type=sa.String(length=20),
        comment=CONFIDENCE_COMMENT,
    )

    op.create_check_constraint(
        "ck_market_causal_path_source_exclusive", "market_causal_path", SOURCE_EXCLUSIVE
    )
    op.create_check_constraint(
        "ck_market_causal_path_source_sign",
        "market_causal_path",
        "source_sign IS NULL OR source_sign IN ('up', 'down')",
    )
    op.create_check_constraint(
        "ck_market_causal_path_source_target_kind",
        "market_causal_path",
        "source_target_kind IS NULL OR source_target_kind IN"
        " ('instrument', 'index', 'quote', 'indicator')",
    )
    op.create_check_constraint(
        "ck_market_causal_path_source_not_self",
        "market_causal_path",
        "source_target_code IS NULL"
        " OR NOT (source_target_kind = target_kind AND source_target_code = target_code)",
    )
    op.create_check_constraint(
        "ck_market_causal_path_endpoint_needs_source",
        "market_causal_path",
        "confidence <> 'endpoint_observed' OR source_target_code IS NOT NULL",
    )


def downgrade_default() -> None:
    # 대상에서 출발한 경로는 사건 출발로 되돌릴 수 없다. 지우고 내려간다.
    op.execute("DELETE FROM market_causal_path WHERE event_id IS NULL")

    for name in (
        "ck_market_causal_path_endpoint_needs_source",
        "ck_market_causal_path_source_not_self",
        "ck_market_causal_path_source_target_kind",
        "ck_market_causal_path_source_sign",
        "ck_market_causal_path_source_exclusive",
    ):
        op.drop_constraint(name, "market_causal_path", type_="check")

    op.drop_constraint(
        "ck_market_causal_path_confidence", "market_causal_path", type_="check"
    )
    op.create_check_constraint(
        "ck_market_causal_path_confidence",
        "market_causal_path",
        "confidence IN ('observed', 'plausible')",
    )

    op.drop_constraint(
        "uq_market_causal_path_natural_key", "market_causal_path", type_="unique"
    )
    op.create_unique_constraint(
        "uq_market_causal_path_natural_key",
        "market_causal_path",
        ["week_start", "event_id", "target_kind", "target_code", "chain_key"],
    )

    op.alter_column(
        "market_causal_path",
        "event_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
    op.drop_column("market_causal_path", "source_sign")
    op.drop_column("market_causal_path", "source_target_code")
    op.drop_column("market_causal_path", "source_target_kind")
    op.drop_column("market_causal_path", "source_key")
