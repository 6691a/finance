"""add technical signal

Revision ID: c9f4b2e70a18
Revises: a4c9e1f7b3d6
Create Date: 2026-08-24 12:00:00.000000

확정 일봉에서 검출한 매매 신호를 사건으로 보존한다. 지표값(SMA·RSI·MACD)은 원천 OHLCV에서
언제든 다시 계산되므로 저장하지 않지만, "언제 교차했는지"는 값에서 되살릴 수 없고 그 뒤
실제로 어떻게 움직였는지를 채점하려면 사건이 행으로 남아야 한다.
설계는 `docs/market-technical-indicators.md` 12절.

이 리비전은 **손으로 썼다.** `config.yaml`이 운영 DB를 가리켜 autogenerate를 돌리지
않는다(프로젝트 규칙). 검증은 오프라인 `head_sql` 기반 `tests/migrations/test_technical_signal_schema.py`가
한다. 모델(`apps/models/analysis.py`)과 여기의 컬럼 주석·CHECK 문자열은 글자 그대로 같아야 한다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9f4b2e70a18"
down_revision: str | Sequence[str] | None = "a4c9e1f7b3d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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
        "technical_signal",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="대리 기본키"),
        sa.Column(
            "provider",
            sa.Text(),
            nullable=False,
            comment="원천 일봉을 준 제공처. 현재는 kis뿐이다",
        ),
        sa.Column(
            "symbol",
            sa.Text(),
            nullable=False,
            comment="대상 식별자(지수는 KOSPI·KOSDAQ, 종목은 6자리 코드). 마스터로 외래키를 걸지 않는다",
        ),
        sa.Column(
            "signal_date",
            sa.Date(),
            nullable=False,
            comment="사건이 일어난 KRX 거래일. 그날 확정 종가로 계산한 지표가 직전 거래일과 교차했다",
        ),
        sa.Column(
            "kind",
            sa.String(length=20),
            nullable=False,
            comment=(
                "신호 종류(sma_cross는 SMA20/SMA60 교차, macd_cross는 MACD와 시그널 라인 교차, "
                "rsi_reversal은 RSI14의 30·70 재돌파)"
            ),
        ),
        sa.Column(
            "direction",
            sa.String(length=20),
            nullable=False,
            comment=(
                "사건의 방향(up 또는 down). 골든크로스·MACD 상향·과매도 탈출이 up이다. "
                "매수·매도 판정이 아니라 사건이 어느 쪽으로 일어났는지다"
            ),
        ),
        sa.Column(
            "close",
            sa.Numeric(precision=18, scale=4),
            nullable=False,
            comment="사건일 종가. 사후 수익률 계산의 기준가다",
        ),
        sa.Column(
            "sma20",
            sa.Numeric(precision=18, scale=4),
            nullable=False,
            comment="사건일의 20거래일 단순이동평균",
        ),
        sa.Column(
            "sma60",
            sa.Numeric(precision=18, scale=4),
            nullable=False,
            comment="사건일의 60거래일 단순이동평균",
        ),
        sa.Column(
            "rsi14",
            sa.Numeric(precision=6, scale=2),
            nullable=False,
            comment="사건일의 14일 RSI(Wilder 평활). 0~100",
        ),
        sa.Column(
            "macd",
            sa.Numeric(precision=18, scale=4),
            nullable=False,
            comment="사건일의 MACD 라인(EMA12 - EMA26)",
        ),
        sa.Column(
            "macd_signal",
            sa.Numeric(precision=18, scale=4),
            nullable=False,
            comment="사건일의 시그널 라인(MACD의 EMA9)",
        ),
        sa.Column(
            "volume_ratio20",
            sa.Numeric(precision=10, scale=4),
            nullable=True,
            comment=(
                "사건일 거래량 / 직전 20거래일 평균 거래량. 거래량이 없거나 직전 평균이 0이면 NULL이다 — "
                "1로 채우지 않는다"
            ),
        ),
        sa.Column(
            "rule_version",
            sa.Text(),
            nullable=False,
            comment="검출 규칙 버전(modules/technical.py의 RULE_VERSION). thesis.prompt_version과 같은 역할이다",
        ),
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
        sa.CheckConstraint(
            "kind IN ('sma_cross', 'macd_cross', 'rsi_reversal')",
            name="ck_technical_signal_kind",
        ),
        sa.CheckConstraint("direction IN ('up', 'down')", name="ck_technical_signal_direction"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "symbol",
            "signal_date",
            "kind",
            name="uq_technical_signal_natural_key",
        ),
        comment="확정 일봉에서 검출한 기술적 매매 신호를 사후 채점용으로 누적하는 테이블",
    )

    # 신호를 근거로 인용할 수 있게 `thesis_evidence`의 종류 집합을 넓힌다. 지표값은 문맥이라
    # 인용 대상이 아니지만 신호는 행 ID를 가진 사건이다(문서 14.3절).
    op.drop_constraint("ck_thesis_evidence_kind", "thesis_evidence", type_="check")
    op.create_check_constraint(
        "ck_thesis_evidence_kind",
        "thesis_evidence",
        "evidence_kind IN ('document', 'disclosure', 'macro_change', 'technical_signal')",
    )
    op.alter_column(
        "thesis_evidence",
        "evidence_kind",
        existing_type=sa.String(length=20),
        existing_nullable=False,
        comment=(
            "근거의 출처 종류(document, disclosure, macro_change, technical_signal). evidence_ref 앞자리와 같은 값이다"
        ),
    )
    op.alter_column(
        "thesis_evidence",
        "evidence_ref",
        existing_type=sa.Text(),
        existing_nullable=False,
        comment=(
            "툴 결과가 준 ref 그대로. `<evidence_kind>:<id>` 2단이며 앞자리는 evidence_kind와 글자 그대로 같다"
            "(document:123, disclosure:20260821000123, macro_change:SP500_FUT, technical_signal:1042). "
            "접두를 kind와 같게 두면 파싱이 한 규칙으로 끝나고, 소스 이름을 ref 안에 다시 넣지 않는다"
        ),
    )


def downgrade_default() -> None:
    op.drop_constraint("ck_thesis_evidence_kind", "thesis_evidence", type_="check")
    op.create_check_constraint(
        "ck_thesis_evidence_kind",
        "thesis_evidence",
        "evidence_kind IN ('document', 'disclosure', 'macro_change')",
    )
    op.drop_table("technical_signal")
