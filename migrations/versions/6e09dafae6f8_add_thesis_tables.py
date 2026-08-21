"""add thesis tables

Revision ID: 6e09dafae6f8
Revises: c5f81d3a9b46
Create Date: 2026-08-21 00:00:00.000000

시장 추론(thesis)과 그 근거를 담는 노드·엣지 테이블 둘을 만든다. 설계는
`docs/market-thesis/1-storage.md`에 있다.

이 리비전은 **손으로 썼다.** `config.yaml`이 운영 DB를 가리켜 autogenerate를 돌리지
않는다(프로젝트 규칙). 검증은 오프라인 `head_sql` 기반 `tests/migrations/test_thesis_schema.py`가
한다.

모델(`apps/models/analysis.py`)과 여기의 컬럼 주석·CHECK 문자열은 글자 그대로 같아야 한다.
어긋나면 다음 autogenerate가 차이를 만들어 낸다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "6e09dafae6f8"
down_revision: str | Sequence[str] | None = "c5f81d3a9b46"
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


def _entity_columns() -> list[sa.Column]:
    return [
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
    ]


def upgrade_default() -> None:
    op.create_table(
        "thesis",
        sa.Column(
            "run_slot",
            sa.String(length=20),
            nullable=False,
            comment="추론을 만든 슬롯(pre_open은 장전 전망, post_close는 장후 리뷰). 슬롯이 곧 추론의 종류다",
        ),
        sa.Column(
            "run_date",
            sa.Date(),
            nullable=False,
            comment="추론이 대상으로 삼은 세션 날짜(KST). 시각은 담지 않는다",
        ),
        sa.Column(
            "as_of_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment=(
                "관측 상태와 툴 조회의 기준 시각(UTC). 벽시계가 아니라 슬롯이 정한다"
                "(장전 = 당일 08:35 KST, 장후 = 당일 15:30 KST). "
                "event-time cutoff라 이 시각 이후 감지·평가·갱신된 행은 조회에서 뺀다"
            ),
        ),
        sa.Column(
            "dag_run_id",
            sa.Text(),
            nullable=False,
            comment="이 행을 쓴 Airflow dag_run_id. 같은 실행의 재시도인지를 DB가 증명한다",
        ),
        sa.Column(
            "subject_kind",
            sa.String(length=20),
            nullable=False,
            comment="추론 대상 종류(index 또는 stock). 실제 등락률을 어느 테이블에서 읽을지가 갈린다",
        ),
        sa.Column(
            "subject_code",
            sa.Text(),
            nullable=False,
            comment="추론 대상 식별자(지수는 KOSPI·KOSDAQ, 종목은 6자리 코드). 마스터로 외래키를 걸지 않는다",
        ),
        sa.Column(
            "label",
            sa.Text(),
            nullable=False,
            comment="추론 시점의 표시 이름 스냅샷. 마스터에서 이름이 바뀌어도 당시 표기가 남는다",
        ),
        sa.Column(
            "prob_up",
            sa.Numeric(precision=5, scale=4),
            nullable=False,
            comment="상승 확률 0~1. 셋의 합은 1이다",
        ),
        sa.Column(
            "prob_down",
            sa.Numeric(precision=5, scale=4),
            nullable=False,
            comment="하락 확률 0~1. 셋의 합은 1이다",
        ),
        sa.Column(
            "prob_flat",
            sa.Numeric(precision=5, scale=4),
            nullable=False,
            comment="횡보 확률 0~1. 셋의 합은 1이다",
        ),
        sa.Column(
            "up_reasoning",
            sa.Text(),
            nullable=False,
            comment="상승 쪽 이유(한국어). 저장 전에 500자로 자른다",
        ),
        sa.Column(
            "down_reasoning",
            sa.Text(),
            nullable=False,
            comment="하락 쪽 이유(한국어). 저장 전에 500자로 자른다",
        ),
        sa.Column(
            "flat_reasoning",
            sa.Text(),
            nullable=False,
            comment="횡보 쪽 이유(한국어). 저장 전에 500자로 자른다",
        ),
        sa.Column(
            "input_state",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment=(
                "프롬프트에 준 관측 상태 스냅샷. "
                "모델이 무엇을 보고 추론했는지의 절반이다(나머지 절반은 thesis_evidence)"
            ),
        ),
        sa.Column(
            "tool_rounds",
            sa.Integer(),
            nullable=False,
            comment="모델이 조사 단계에서 툴을 몇 왕복 불렀는지. 프롬프트·상한을 다시 볼 때의 운영 지표다",
        ),
        sa.Column(
            "llm_model",
            sa.Text(),
            nullable=False,
            comment="이 추론을 만든 모델 식별자. 모델을 바꾼 뒤 채점 결과를 가르는 기준이다",
        ),
        sa.Column(
            "prompt_version",
            sa.Text(),
            nullable=False,
            comment="프롬프트 판 식별자. 프롬프트를 고친 뒤 채점 결과를 가르는 기준이다",
        ),
        *_entity_columns(),
        sa.CheckConstraint("run_slot IN ('pre_open', 'post_close')", name="ck_thesis_run_slot"),
        sa.CheckConstraint("subject_kind IN ('index', 'stock')", name="ck_thesis_subject_kind"),
        sa.CheckConstraint(
            "prob_up BETWEEN 0 AND 1 AND prob_down BETWEEN 0 AND 1 AND prob_flat BETWEEN 0 AND 1",
            name="ck_thesis_prob_range",
        ),
        sa.CheckConstraint("abs(prob_up + prob_down + prob_flat - 1) < 0.001", name="ck_thesis_prob_sum"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_date", "run_slot", "subject_kind", "subject_code", name="uq_thesis_natural_key"),
        comment="슬롯마다 만든 시장 추론을 불변으로 보존하는 테이블. 채점과 해설은 thesis_outcome이 갖는다",
        info={"database": "default", "managed": True},
    )

    op.create_table(
        "thesis_outcome",
        sa.Column("thesis_id", sa.BigInteger(), nullable=False, comment="이 결과가 붙는 thesis 레코드 ID"),
        sa.Column(
            "horizon_days",
            sa.Integer(),
            nullable=False,
            comment=(
                "지평 길이. **KRX 영업일 수이고 달력일이 아니다.** T+N 관용 표기를 따랐다. "
                "0은 예측일 세션 하나이며 해설을 받지 않는다"
            ),
        ),
        sa.Column(
            "as_of_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="이 지평의 기준 시각(UTC). 그 영업일 장후 15:30 KST이며 해설 툴 조회의 창 끝이다",
        ),
        sa.Column("dag_run_id", sa.Text(), nullable=False, comment="이 행을 쓴 Airflow dag_run_id"),
        sa.Column(
            "evaluated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="채점한 시각(UTC). NULL은 미채점이다. 채점은 pre_open 추론에만 붙는다",
        ),
        sa.Column(
            "actual_return_pct",
            sa.Numeric(precision=8, scale=4),
            nullable=True,
            comment=(
                "예측 시점 기준가 대비 이 지평 종가의 누적 등락률(퍼센트). "
                "**기준가는 지평이 달라도 같다** — 예측일 전 영업일 종가다. "
                "그래야 T+1과 T+5를 비교할 수 있다"
            ),
        ),
        sa.Column(
            "actual_outcome",
            sa.String(length=20),
            nullable=True,
            comment=(
                "누적 등락률의 분류(up/down/flat). 임계는 지평마다 다르다 — 하루 임계를 5영업일 "
                "누적에 쓰면 flat이 사실상 사라진다. 예측과 비교하지 않는다(비교는 brier_score가 한다)"
            ),
        ),
        sa.Column(
            "brier_score",
            sa.Numeric(precision=6, scale=5),
            nullable=True,
            comment=(
                "원 추론의 세 확률을 이 지평 결과로 매긴 3-class Brier 점수. 0이 완벽, 2가 최악이다. "
                "방향만 맞고 확신이 낮았던 경우와 틀린 방향에 확신을 준 경우를 함께 잡는다"
            ),
        ),
        sa.Column(
            "narrative",
            sa.Text(),
            nullable=True,
            comment="이 지평에서 쌓인 보도를 근거로 쓴 사후 해설(한국어). 저장 전에 1000자로 자른다",
        ),
        sa.Column(
            "verdict",
            sa.String(length=20),
            nullable=True,
            comment=(
                "원 추론의 **이유**가 이후 보도로 지지됐는지(supported/contradicted/unresolved). "
                "brier_score와 다른 것을 잰다 — 저쪽은 방향, 이쪽은 이유다. 둘을 합치지 않는다. "
                "근거 인용 없이 supported·contradicted가 오면 저장 전에 unresolved로 내린다"
            ),
        ),
        sa.Column(
            "narrative_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="해설을 쓴 시각(UTC). NULL은 아직 해설이 없다는 뜻이고 다음 실행이 다시 집는다",
        ),
        sa.Column(
            "llm_model",
            sa.Text(),
            nullable=True,
            comment="해설을 만든 모델 식별자. 원 추론의 모델과 다를 수 있다",
        ),
        sa.Column(
            "prompt_version",
            sa.Text(),
            nullable=True,
            comment=(
                "해설 프롬프트 판과 변형(`<판>/<변형>`, 예: 1/informed). 변형은 실제 결과를 "
                "프롬프트에 주는 informed와 주지 않는 blind다. 어느 쪽이 나은지는 실측으로 가른다"
            ),
        ),
        *_entity_columns(),
        sa.CheckConstraint("horizon_days IN (0, 1, 3, 5)", name="ck_thesis_outcome_horizon_days"),
        sa.CheckConstraint("actual_outcome IN ('up', 'down', 'flat')", name="ck_thesis_outcome_actual_outcome"),
        sa.CheckConstraint(
            "verdict IN ('supported', 'contradicted', 'unresolved')",
            name="ck_thesis_outcome_verdict",
        ),
        sa.CheckConstraint("brier_score BETWEEN 0 AND 2", name="ck_thesis_outcome_brier_range"),
        sa.CheckConstraint(
            "(evaluated_at IS NULL AND actual_return_pct IS NULL"
            " AND actual_outcome IS NULL AND brier_score IS NULL)"
            " OR (evaluated_at IS NOT NULL AND actual_return_pct IS NOT NULL"
            " AND actual_outcome IS NOT NULL AND brier_score IS NOT NULL)",
            name="ck_thesis_outcome_grade_all_or_none",
        ),
        sa.CheckConstraint(
            "(narrative IS NULL AND verdict IS NULL AND narrative_at IS NULL"
            " AND llm_model IS NULL AND prompt_version IS NULL)"
            " OR (narrative IS NOT NULL AND verdict IS NOT NULL AND narrative_at IS NOT NULL"
            " AND llm_model IS NOT NULL AND prompt_version IS NOT NULL)",
            name="ck_thesis_outcome_narrative_all_or_none",
        ),
        sa.CheckConstraint(
            "horizon_days <> 0 OR (narrative IS NULL AND verdict IS NULL"
            " AND narrative_at IS NULL AND llm_model IS NULL AND prompt_version IS NULL)",
            name="ck_thesis_outcome_zero_horizon_has_no_narrative",
        ),
        # 채점도 해설도 없는 행은 의미가 없다.
        sa.CheckConstraint(
            "evaluated_at IS NOT NULL OR narrative IS NOT NULL",
            name="ck_thesis_outcome_not_empty",
        ),
        # 추론이 지워지면 그 결과도 함께 지운다.
        sa.ForeignKeyConstraint(["thesis_id"], ["thesis.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("thesis_id", "horizon_days", name="uq_thesis_outcome_natural_key"),
        comment="추론 하나의 지평별 채점과 사후 해설을 누적하는 테이블",
        info={"database": "default", "managed": True},
    )

    op.create_table(
        "thesis_evidence",
        sa.Column("thesis_id", sa.BigInteger(), nullable=False, comment="이 근거를 인용한 thesis 레코드 ID"),
        sa.Column(
            "outcome_horizon_days",
            sa.Integer(),
            nullable=True,
            comment=(
                "누가 인용했는지. NULL은 원 추론이 인용한 근거, 1·3·5는 그 지평의 사후 해설이 "
                "인용한 근거다. thesis_outcome으로 외래키를 걸지 않는다"
            ),
        ),
        sa.Column(
            "evidence_kind",
            sa.String(length=20),
            nullable=False,
            comment="근거의 출처 종류(document, disclosure, macro_change). evidence_ref 앞자리와 같은 값이다",
        ),
        sa.Column(
            "evidence_ref",
            sa.Text(),
            nullable=False,
            comment=(
                "툴 결과가 준 ref 그대로. `<evidence_kind>:<id>` 2단이며 앞자리는 evidence_kind와 글자 그대로 같다"
                "(document:123, disclosure:20260821000123, macro_change:SP500_FUT). "
                "접두를 kind와 같게 두면 파싱이 한 규칙으로 끝나고, 소스 이름을 ref 안에 다시 넣지 않는다"
            ),
        ),
        sa.Column(
            "evidence_title",
            sa.Text(),
            nullable=False,
            comment="인용 시점의 제목 스냅샷. 원본이 지워지거나 바뀌어도 그래프에서 무엇을 인용했는지 읽힌다",
        ),
        sa.Column(
            "evidence_url",
            sa.Text(),
            nullable=True,
            comment="문서면 canonical_url, 공시면 DART 뷰어 URL. 매크로 변화처럼 링크할 곳이 없으면 NULL이다",
        ),
        sa.Column(
            "detail",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="툴이 준 수치 스냅샷(등락률, 가치 점수 등). 근거가 당시 어떤 값이었는지를 남긴다",
        ),
        sa.Column(
            "rank",
            sa.Integer(),
            nullable=False,
            comment="모델이 인용한 순서(1부터). Slack이 상위 몇 개만 보일 때의 기준이다",
        ),
        *_entity_columns(),
        sa.CheckConstraint(
            "evidence_kind IN ('document', 'disclosure', 'macro_change')",
            name="ck_thesis_evidence_kind",
        ),
        sa.CheckConstraint("rank > 0", name="ck_thesis_evidence_rank_positive"),
        # 해설을 받는 지평만 온다. 0은 해설이 없어(thesis_outcome CHECK) 근거도 없다.
        sa.CheckConstraint(
            "outcome_horizon_days IS NULL OR outcome_horizon_days IN (1, 3, 5)",
            name="ck_thesis_evidence_outcome_horizon_days",
        ),
        # 추론이 지워지면 그 근거도 함께 지운다. 근거는 추론 없이 의미가 없다.
        sa.ForeignKeyConstraint(["thesis_id"], ["thesis.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "thesis_id",
            "outcome_horizon_days",
            "evidence_kind",
            "evidence_ref",
            name="uq_thesis_evidence_ref",
        ),
        sa.UniqueConstraint("thesis_id", "outcome_horizon_days", "rank", name="uq_thesis_evidence_rank"),
        comment="추론과 사후 해설이 인용한 근거를 순위와 함께 보존하는 테이블",
        info={"database": "default", "managed": True},
    )


def downgrade_default() -> None:
    op.drop_table("thesis_evidence")
    op.drop_table("thesis_outcome")
    op.drop_table("thesis")
