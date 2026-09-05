"""add market shock event

Revision ID: d5b02f8a91c4
Revises: d4c7f1a9e206
Create Date: 2026-09-04 16:00:00.000000

시장 급변 포착의 표 하나. 설계는 `docs/analysis/market-shock-capture.md`에 있다.

30분 창에서 ±2% 움직인 사건이 한 행이고, 포착(장중)과 원인(사후 최대 3영업일)이 같은
행에 산다. 원인이 포착의 속성이라 표를 가르지 않는다.

**수기 리비전이다.** `config.yaml`이 운영 DB를 가리켜 autogenerate를 돌리지 않는다.
검증은 오프라인 `head_sql` 기반 `tests/migrations/`가 한다.

모델(`apps/models/market/shock.py`)과 여기의 CHECK 문자열·컬럼 주석은 **글자 그대로**
같아야 한다. 다르면 다음 autogenerate가 매번 차이를 만든다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d5b02f8a91c4"
down_revision: str | Sequence[str] | None = "d4c7f1a9e206"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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
        "market_shock_event",
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
            "symbol",
            sa.Text(),
            nullable=False,
            comment="급변을 판정한 대상(KOSPI). index_bar.symbol과 같은 값이다. 대상이 늘 수 있어 Enum이 아니다",
        ),
        sa.Column(
            "session_date",
            sa.Date(),
            nullable=False,
            comment="사건이 일어난 세션 날짜(KST). 시각은 담지 않는다",
        ),
        sa.Column(
            "direction",
            _enum("drop", "surge"),
            nullable=False,
            comment="급변의 방향(drop은 고점 대비 하락, surge는 저점 대비 상승)",
        ),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="임계에 닿은 봉의 시각(UTC). 자연키의 절반이다",
        ),
        sa.Column(
            "window_start",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="판정한 창의 시작(UTC). 창 길이는 운영 손잡이라 행마다 남긴다",
        ),
        sa.Column(
            "window_end",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="판정한 창의 끝(UTC). 아시아 지수 지연을 흡수하려고 실행 시각보다 앞선다",
        ),
        sa.Column(
            "extreme_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="창 안의 극값 봉 시각(UTC). drop이면 고점, surge면 저점이다",
        ),
        sa.Column(
            "extreme_price",
            sa.Numeric(precision=18, scale=8),
            nullable=False,
            comment="창 안의 극값. drop이면 최고가, surge면 최저가다. move_pct의 분모다",
        ),
        sa.Column(
            "trigger_price",
            sa.Numeric(precision=18, scale=8),
            nullable=False,
            comment="임계에 닿은 봉의 값. drop이면 저가, surge면 고가다",
        ),
        sa.Column(
            "move_pct",
            sa.Numeric(precision=10, scale=4),
            nullable=False,
            comment="트리거 값(퍼센트). drop이면 음수, surge면 양수다. 부호가 direction과 짝이다",
        ),
        sa.Column(
            "window_change_pct",
            sa.Numeric(precision=10, scale=4),
            nullable=True,
            comment=(
                "창의 첫 시가 대비 마지막 종가 등락(퍼센트). peers와 같은 눈금이라 나란히 읽는다. "
                "move_pct는 극값 기준이라 축이 다르다"
            ),
        ),
        sa.Column(
            "bar_count",
            sa.Integer(),
            nullable=False,
            comment="판정에 쓴 봉 수. 창이 덜 찬 채로 판정했는지를 나중에 가른다",
        ),
        sa.Column(
            "peers",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment=(
                "같은 창의 다른 시장들. 대상마다 {symbol, change_pct, bars, available}이고 "
                "available이 false면 change_pct는 null이다. 0으로 채우지 않는다 — "
                "빈 칸은 '안 움직였다'가 아니라 '못 봤다'다"
            ),
        ),
        sa.Column(
            "threshold_pct",
            sa.Numeric(precision=10, scale=4),
            nullable=False,
            comment="이 행을 만든 임계(퍼센트, 양수). 손잡이를 옮긴 뒤 옛 행과 섞이지 않게 남긴다",
        ),
        sa.Column(
            "notified_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="포착 Slack을 보낸 시각(UTC). NULL이면 저장은 됐고 발송이 실패한 것이다",
        ),
        sa.Column(
            "cause_status",
            _enum("pending", "resolved", "unknown"),
            server_default="pending",
            nullable=False,
            comment="원인 분석의 상태(pending은 아직, resolved는 찾음, unknown은 기한 안에 못 찾음)",
        ),
        sa.Column(
            "cause_deadline",
            sa.Date(),
            nullable=True,
            comment=(
                "원인을 찾는 마지막 날(KST). 포착일부터 3번째 KRX 개장일이다. "
                "달력이 아직 그날까지 안 채워졌으면 NULL이고 다음 실행이 채운다"
            ),
        ),
        sa.Column(
            "cause_attempts",
            sa.Integer(),
            server_default="0",
            nullable=False,
            comment="원인 분석을 시도한 횟수. 모델을 부르기 전에 올린다 — 안 그러면 죽은 실행이 안 세어진다",
        ),
        sa.Column("cause_text", sa.Text(), nullable=True, comment="원인 한 문장. resolved에서만 채워진다"),
        sa.Column(
            "cause_kind",
            _enum("rumor", "confirmed", "unclear"),
            nullable=True,
            comment="원인이 루머로 밝혀졌나(rumor) 사실로 확인됐나(confirmed) 가릴 수 없나(unclear)",
        ),
        sa.Column(
            "cause_document_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="근거로 든 document.id 배열. 우리가 준 목록 안의 값만 남는다 — 검증이 나머지를 버린다",
        ),
        sa.Column(
            "cause_weak",
            sa.Boolean(),
            server_default="false",
            nullable=False,
            comment="검증이 근거를 전부 버렸다. 정상 답과 같아 보이면 아무도 그날을 못 고른다",
        ),
        sa.Column(
            "cause_prompt_version",
            sa.Text(),
            nullable=True,
            comment="원인 분석이 쓴 프롬프트 판. 판을 안 올리고 문장을 고치면 이 칸이 거짓말을 한다",
        ),
        sa.Column("cause_llm_model", sa.Text(), nullable=True, comment="원인 분석이 부른 모델 이름"),
        sa.Column(
            "cause_resolved_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="원인 분석을 닫은 시각(UTC). resolved와 unknown 둘 다 채운다",
        ),
        sa.CheckConstraint("cause_attempts >= 0", name="ck_market_shock_event_cause_attempts"),
        sa.CheckConstraint(
            "cause_kind IS NULL OR cause_kind IN ('rumor', 'confirmed', 'unclear')",
            name="ck_market_shock_event_cause_kind",
        ),
        sa.CheckConstraint(
            "cause_status IN ('pending', 'resolved', 'unknown')",
            name="ck_market_shock_event_cause_status",
        ),
        sa.CheckConstraint("direction IN ('drop', 'surge')", name="ck_market_shock_event_direction"),
        sa.CheckConstraint("window_start < window_end", name="ck_market_shock_event_window_order"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", "detected_at", name="uq_market_shock_event_natural_key"),
        comment="30분 창에서 ±2% 움직인 시장 급변과 그 사후 원인을 담는 테이블",
    )
    op.create_index(
        "ix_market_shock_event_cause_pending",
        "market_shock_event",
        ["cause_status", "cause_deadline"],
        unique=False,
    )
    op.create_index(
        "ix_market_shock_event_session_date",
        "market_shock_event",
        ["session_date"],
        unique=False,
    )


def downgrade_default() -> None:
    op.drop_index("ix_market_shock_event_session_date", table_name="market_shock_event")
    op.drop_index("ix_market_shock_event_cause_pending", table_name="market_shock_event")
    op.drop_table("market_shock_event")
