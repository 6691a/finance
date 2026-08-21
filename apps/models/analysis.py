from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from apps.core.database import EntityBase, table_options


class RunSlot(StrEnum):
    """추론을 만든 슬롯. 슬롯이 곧 추론의 종류다.

    `pre_open`은 장이 열리기 전의 전망(forecast)이고 `post_close`는 장이 닫힌 뒤의
    리뷰(review)다. 별도 `kind` 컬럼을 두지 않는 이유는 둘이 항상 같이 움직이기 때문이다 —
    슬롯 하나에 종류 둘이 오는 경우가 없다.
    """

    PRE_OPEN = "pre_open"
    POST_CLOSE = "post_close"


class ThesisSubjectKind(StrEnum):
    """추론 대상의 종류. 지수와 개별 종목은 등락률 원본 테이블이 다르다."""

    INDEX = "index"
    STOCK = "stock"


class ThesisDirection(StrEnum):
    """방향. 예측 확률과 실제 결과가 같은 세 값을 쓴다.

    그래서 `hit`/`miss` 같은 비교 결과 enum이 따로 필요 없다. 얼마나 확신 있게 맞췄는지는
    `thesis.brier_score`가 점수로 답한다.
    """

    UP = "up"
    DOWN = "down"
    FLAT = "flat"


class ThesisEvidenceKind(StrEnum):
    """근거의 출처 종류. `thesis_evidence.evidence_ref`의 `<kind>:<id>` 앞자리와 같다."""

    DOCUMENT = "document"
    DISCLOSURE = "disclosure"
    MACRO_CHANGE = "macro_change"


def _enum_column(enum: type[StrEnum]) -> SqlEnum:
    """`StrEnum`을 VARCHAR + CHECK로 내리는 공통 형태.

    PostgreSQL native enum은 값 추가·삭제 마이그레이션 비용이 커서 쓰지 않는다(프로젝트 규칙).
    """
    return SqlEnum(
        enum,
        native_enum=False,
        length=20,
        values_callable=lambda members: [member.value for member in members],
    )


class Thesis(EntityBase):
    """시장 추론 하나. 그래프로 보면 노드다.

    **맞고 틀림이 목적이 아니다.** "어떤 정보를 근거로 어떤 결론을 냈다"가 기록으로 남는 것이
    목적이고, 채점은 그 기록 위에 나중에 얹힌다.

    **첫 성공본은 불변이다.** 같은 (`run_date`, `run_slot`, subject)에 행이 이미 있으면
    `INSERT ... ON CONFLICT DO NOTHING`으로 아무 것도 바꾸지 않는다. LLM은 재호출마다 답이
    달라서 덮어쓰면 최초 판단이 사라지고, 옛 확률로 매긴 Brier가 새 확률 옆에 남는다.
    잘못된 판단도 고치지 않는다 — 승인·보류 상태 머신도, 사람이 행을 UPDATE하는 경로도 없다.

    채점 컬럼 넷(`evaluated_at`·`actual_return_pct`·`actual_outcome`·`brier_score`)만
    나중에 별도 UPDATE로 채워진다. 추론 컬럼은 건드리지 않는다.
    """

    __tablename__ = "thesis"
    __table_args__ = (
        UniqueConstraint(
            "run_date",
            "run_slot",
            "subject_kind",
            "subject_code",
            name="uq_thesis_natural_key",
        ),
        Index("ix_thesis_run_slot_evaluated_at", "run_slot", "evaluated_at"),
        CheckConstraint("run_slot IN ('pre_open', 'post_close')", name="ck_thesis_run_slot"),
        CheckConstraint("subject_kind IN ('index', 'stock')", name="ck_thesis_subject_kind"),
        CheckConstraint(
            "actual_outcome IN ('up', 'down', 'flat')",
            name="ck_thesis_actual_outcome",
        ),
        CheckConstraint(
            "prob_up BETWEEN 0 AND 1 AND prob_down BETWEEN 0 AND 1 AND prob_flat BETWEEN 0 AND 1",
            name="ck_thesis_prob_range",
        ),
        # 저장 전에 애플리케이션이 이미 ±0.02 오차를 정규화해 정확히 1로 맞춘다. 이 제약은
        # 그 뒤의 최종 안전장치라 허용 폭이 훨씬 좁다.
        CheckConstraint(
            "abs(prob_up + prob_down + prob_flat - 1) < 0.001",
            name="ck_thesis_prob_sum",
        ),
        # 채점은 넷이 한 번에 채워지거나 전부 비어 있다. 등락률만 있고 점수가 없는 중간
        # 상태를 두면 "채점했는데 점수가 없는" 행이 조용히 생긴다.
        CheckConstraint(
            "(evaluated_at IS NULL AND actual_return_pct IS NULL"
            " AND actual_outcome IS NULL AND brier_score IS NULL)"
            " OR (evaluated_at IS NOT NULL AND actual_return_pct IS NOT NULL"
            " AND actual_outcome IS NOT NULL AND brier_score IS NOT NULL)",
            name="ck_thesis_outcome_all_or_none",
        ),
        CheckConstraint("brier_score BETWEEN 0 AND 2", name="ck_thesis_brier_range"),
        table_options(
            comment="슬롯마다 만든 시장 추론과 그 자동 채점 결과를 불변으로 보존하는 테이블",
            database="default",
        ),
    )

    run_slot: Mapped[RunSlot] = mapped_column(
        _enum_column(RunSlot),
        nullable=False,
        comment="추론을 만든 슬롯(pre_open은 장전 전망, post_close는 장후 리뷰). 슬롯이 곧 추론의 종류다",
    )
    run_date: Mapped[date] = mapped_column(
        nullable=False,
        comment="추론이 대상으로 삼은 세션 날짜(KST). 시각은 담지 않는다",
    )
    as_of_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment=(
            "관측 상태와 툴 조회의 기준 시각(UTC). 벽시계가 아니라 슬롯이 정한다"
            "(장전 = 당일 08:35 KST, 장후 = 당일 15:30 KST). "
            "event-time cutoff라 이 시각 이후 감지·평가·갱신된 행은 조회에서 뺀다"
        ),
    )
    dag_run_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="이 행을 쓴 Airflow dag_run_id. 같은 실행의 재시도인지를 DB가 증명한다",
    )
    subject_kind: Mapped[ThesisSubjectKind] = mapped_column(
        _enum_column(ThesisSubjectKind),
        nullable=False,
        comment="추론 대상 종류(index 또는 stock). 실제 등락률을 어느 테이블에서 읽을지가 갈린다",
    )
    subject_code: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="추론 대상 식별자(지수는 KOSPI·KOSDAQ, 종목은 6자리 코드). 마스터로 외래키를 걸지 않는다",
    )
    label: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="추론 시점의 표시 이름 스냅샷. 마스터에서 이름이 바뀌어도 당시 표기가 남는다",
    )
    prob_up: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False, comment="상승 확률 0~1. 셋의 합은 1이다")
    prob_down: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False, comment="하락 확률 0~1. 셋의 합은 1이다")
    prob_flat: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False, comment="횡보 확률 0~1. 셋의 합은 1이다")
    up_reasoning: Mapped[str] = mapped_column(
        Text, nullable=False, comment="상승 쪽 이유(한국어). 저장 전에 500자로 자른다"
    )
    down_reasoning: Mapped[str] = mapped_column(
        Text, nullable=False, comment="하락 쪽 이유(한국어). 저장 전에 500자로 자른다"
    )
    flat_reasoning: Mapped[str] = mapped_column(
        Text, nullable=False, comment="횡보 쪽 이유(한국어). 저장 전에 500자로 자른다"
    )
    input_state: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        comment="프롬프트에 준 관측 상태 스냅샷. 모델이 무엇을 보고 추론했는지의 절반이다(나머지 절반은 thesis_evidence)",
    )
    tool_rounds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="모델이 조사 단계에서 툴을 몇 왕복 불렀는지. 프롬프트·상한을 다시 볼 때의 운영 지표다",
    )
    llm_model: Mapped[str] = mapped_column(
        Text, nullable=False, comment="이 추론을 만든 모델 식별자. 모델을 바꾼 뒤 채점 결과를 가르는 기준이다"
    )
    prompt_version: Mapped[str] = mapped_column(
        Text, nullable=False, comment="프롬프트 판 식별자. 프롬프트를 고친 뒤 채점 결과를 가르는 기준이다"
    )
    evaluated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="채점한 시각(UTC). NULL은 미채점이다. 채점은 pre_open 행에만 붙는다",
    )
    actual_return_pct: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 4),
        nullable=True,
        comment="실제 세션 등락률(퍼센트). 종목은 확정 종가, 지수는 15:30 봉이 원본이다",
    )
    actual_outcome: Mapped[ThesisDirection | None] = mapped_column(
        _enum_column(ThesisDirection),
        nullable=True,
        comment=(
            "실제 등락률의 분류(up/down/flat). |등락률| < 0.3이면 flat이고 아니면 부호를 따른다. "
            "예측과 비교하지 않는다 — 비교는 brier_score가 대신한다"
        ),
    )
    brier_score: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 5),
        nullable=True,
        comment=(
            "3-class Brier 점수. 세 확률과 실제 결과 원-핫 벡터의 차 제곱합이며 0이 완벽, 2가 최악이다. "
            "방향만 맞고 확신이 낮았던 경우와 틀린 방향에 확신을 준 경우를 함께 잡는다"
        ),
    )


class ThesisEvidence(EntityBase):
    """추론이 실제로 인용한 근거 하나. 그래프로 보면 추론에서 나가는 엣지다.

    모델이 툴로 가져온 것 전부가 아니라 **답변에서 인용한 것만** 남는다. 툴 결과 레지스트리에
    없는 `ref`는 저장 전에 버린다.

    `evidence_ref`는 `document`·`instrument` 마스터로 외래키를 걸지 않는다. 원본이 지워져도
    "그때 이것을 근거로 삼았다"는 사실은 남아야 하고, 마스터에 없는 값 하나가 추론 저장 전체를
    죽이면 안 된다(`document_instrument` 선례).
    """

    __tablename__ = "thesis_evidence"
    __table_args__ = (
        UniqueConstraint("thesis_id", "evidence_kind", "evidence_ref", name="uq_thesis_evidence_ref"),
        UniqueConstraint("thesis_id", "rank", name="uq_thesis_evidence_rank"),
        CheckConstraint(
            "evidence_kind IN ('document', 'disclosure', 'macro_change')",
            name="ck_thesis_evidence_kind",
        ),
        CheckConstraint("rank > 0", name="ck_thesis_evidence_rank_positive"),
        table_options(
            comment="추론이 인용한 근거를 추론별로 순위와 함께 보존하는 테이블",
            database="default",
        ),
    )

    thesis_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("thesis.id", ondelete="CASCADE"),
        nullable=False,
        comment="이 근거를 인용한 thesis 레코드 ID",
    )
    evidence_kind: Mapped[ThesisEvidenceKind] = mapped_column(
        _enum_column(ThesisEvidenceKind),
        nullable=False,
        comment="근거의 출처 종류(document, disclosure, macro_change). evidence_ref 앞자리와 같은 값이다",
    )
    evidence_ref: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment=(
            "툴 결과가 준 ref 그대로. `<evidence_kind>:<id>` 2단이며 앞자리는 evidence_kind와 글자 그대로 같다"
            "(document:123, disclosure:20260821000123, macro_change:SP500_FUT). "
            "접두를 kind와 같게 두면 파싱이 한 규칙으로 끝나고, 소스 이름을 ref 안에 다시 넣지 않는다"
        ),
    )
    evidence_title: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="인용 시점의 제목 스냅샷. 원본이 지워지거나 바뀌어도 그래프에서 무엇을 인용했는지 읽힌다",
    )
    evidence_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="문서면 canonical_url, 공시면 DART 뷰어 URL. 매크로 변화처럼 링크할 곳이 없으면 NULL이다",
    )
    detail: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        comment="툴이 준 수치 스냅샷(등락률, 가치 점수 등). 근거가 당시 어떤 값이었는지를 남긴다",
    )
    rank: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="모델이 인용한 순서(1부터). Slack이 상위 몇 개만 보일 때의 기준이다",
    )
