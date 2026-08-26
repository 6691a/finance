"""시장 추론 — 추론 한 건, 지평별 채점·해설, 근거, 본 과거 추론.

vocabulary는 Airflow의 `modules/thesis_domain.py`에 값이 한 벌 더 있다. Airflow는 `apps/`를
보지 못해 import하지 못하므로 중복을 허용하고 `tests/models/test_analysis_models.py`가 대조한다.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from apps.core.database import EntityBase, table_options
from apps.models.analysis._columns import _enum_column


class RunSlot(StrEnum):
    """추론을 만든 슬롯. 슬롯이 곧 추론의 종류다.

    `pre_open`은 장이 열리기 전의 전망(forecast)이고 `post_close`는 KRX 정규장이 닫힌 뒤의
    리뷰(review)다. `post_nxt_close`는 NXT 애프터마켓(15:30~20:00)이 닫힌 뒤의 리뷰이고
    대상이 종목뿐이다 — NXT에는 지수가 없다. 별도 `kind` 컬럼을 두지 않는 이유는 둘이 항상
    같이 움직이기 때문이다 — 슬롯 하나에 종류 둘이 오는 경우가 없다.

    가운데 넷(`intraday_*`·`pre_close`)은 정규장 안에서 도는 전망이다. `pre_open`과 달리
    **기준가가 전일 종가가 아니라 그 슬롯 `as_of_at` 직전 봉의 종가**라, 채점이 읽는 조회가
    갈린다. 값을 시각이 아니라 뜻으로 지은 이유는 슬롯 시각이 운영 손잡이여서다 —
    시각을 30분 옮기는 순간 `intraday_1035` 같은 이름은 거짓이 된다.
    """

    PRE_OPEN = "pre_open"
    INTRADAY_MORNING = "intraday_morning"
    INTRADAY_MIDDAY = "intraday_midday"
    INTRADAY_AFTERNOON = "intraday_afternoon"
    PRE_CLOSE = "pre_close"
    POST_CLOSE = "post_close"
    POST_NXT_CLOSE = "post_nxt_close"


class ThesisSubjectKind(StrEnum):
    """추론 대상의 종류. 지수와 개별 종목은 등락률 원본 테이블이 다르다."""

    INDEX = "index"
    STOCK = "stock"


class ThesisDirection(StrEnum):
    """방향. 예측 확률과 실제 결과가 같은 세 값을 쓴다.

    그래서 `hit`/`miss` 같은 비교 결과 enum이 따로 필요 없다. 얼마나 확신 있게 맞췄는지는
    `thesis_outcome.brier_score`가 점수로 답한다.
    """

    UP = "up"
    DOWN = "down"
    FLAT = "flat"


class ThesisVerdict(StrEnum):
    """사후 해설이 내린 판정. 원 추론의 **이유**가 이후 보도로 지지됐는가.

    **`brier_score`와 다른 것을 잰다.** Brier는 "시장이 그 방향으로 움직였나"고 이것은
    "그 이유가 맞았나"다. 방향만 우연히 맞은 추론과 이유까지 맞은 추론을 가르는 것이 이
    값이다. 둘을 합친 종합 점수는 만들지 않는다 — 섞으면 둘 다 못 읽는다.

    `UNRESOLVED`가 기본이자 가장 흔한 답이어야 한다. 후속 보도가 원 추론의 이유를 직접
    다루는 경우는 흔하지 않고, 억지 판정이 무판정보다 나쁘다.
    """

    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    UNRESOLVED = "unresolved"


class ThesisEvidenceKind(StrEnum):
    """근거의 출처 종류. `thesis_evidence.evidence_ref`의 `<kind>:<id>` 앞자리와 같다."""

    DOCUMENT = "document"
    DISCLOSURE = "disclosure"
    MACRO_CHANGE = "macro_change"
    # 기술적 매매 신호(`technical_signal.id`). 지표값은 문맥이라 인용 대상이 아니지만
    # 신호는 사건이라 인용할 수 있다. 인용을 남기는 이유는 평가다 — 신호를 근거로 쓴
    # 추론이 안 쓴 추론보다 나았는지를 재려면 엣지가 있어야 한다.
    TECHNICAL_SIGNAL = "technical_signal"


class LlmRunKind(StrEnum):
    """대화 한 번의 종류. **슬롯에서 유도할 수 없다** — 같은 `post_close` 슬롯에 생성
    대화와 해설 대화가 둘 다 있다.
    """

    FORECAST = "forecast"
    REVIEW = "review"
    NXT_REVIEW = "nxt_review"
    NARRATION = "narration"


class LlmRunStatus(StrEnum):
    """대화의 끝. `running`은 "시작했지만 종료를 기록하지 못했다"이기도 하다.

    프로세스 kill이나 전원 장애는 `finally`도 못 지나므로 그 행이 `running`으로 남는다.
    삭제할 찌꺼기가 아니라 감사 기록이다 — heartbeat가 없으므로 지금 도는 중인지
    중간에 끊긴 것인지는 이 행만으로 구분하지 않는다.
    """

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ToolCallErrorKind(StrEnum):
    """툴 호출이 실패한 종류. **오류 문자열을 파싱해 분류하지 않는다.**

    `UNKNOWN_TOOL`과 `VALIDATION`은 함수에 진입하기 전이라 `validated_arguments`와
    `duration_ms`가 비어 있다. `CANCELLED`는 sibling 예외로 **실행조차 못 한** 것이고,
    끝까지 돌았지만 모델에게 못 간 것은 오류가 아니라 `delivered = false`다.
    """

    UNKNOWN_TOOL = "unknown_tool"
    VALIDATION = "validation"
    LIMIT = "limit"
    EXECUTION = "execution"
    CANCELLED = "cancelled"


# 채점·해설 지평. KRX 영업일 수이고 달력일이 아니다.
# 0은 예측일 세션 하나이며 해설을 받지 않는다(그날의 보도가 아직 쌓이지 않았다).
THESIS_HORIZON_DAYS: tuple[int, ...] = (0, 1, 3, 5)
NARRATED_HORIZON_DAYS: tuple[int, ...] = (1, 3, 5)


class Thesis(EntityBase):
    """시장 추론 하나. 그래프로 보면 노드다.

    **목적은 정확도다 — 다만 개별 추론이 아니라 판(版)의 정확도다.** 한 건의 적중은 운과
    구분되지 않으므로 "어떤 정보를 근거로 어떤 결론을 냈다"를 먼저 남기고, 채점이 쌓이면
    model·prompt 판별로 비교해 다음 변경을 유지하거나 되돌린다.

    **첫 성공본은 불변이다.** 같은 (`run_date`, `run_slot`, subject)에 행이 이미 있으면
    `INSERT ... ON CONFLICT DO NOTHING`으로 아무 것도 바꾸지 않는다. LLM은 재호출마다 답이
    달라서 덮어쓰면 최초 판단이 사라진다. 잘못된 판단도 고치지 않는다 — 승인·보류 상태
    머신도, 사람이 행을 UPDATE하는 경로도 없다.

    **이 행은 어떤 컬럼도 나중에 갱신되지 않는다.** 채점과 사후 해설은 전부 `thesis_outcome`의
    새 행이다. 채점 칸을 여기 두면 지평이 둘 이상일 때 첫 판단을 덮어써야 하고, 그것이
    이 기능의 핵심 원칙과 정면으로 충돌한다.
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
        CheckConstraint(
            "run_slot IN ('pre_open', 'intraday_morning', 'intraday_midday', "
            "'intraday_afternoon', 'pre_close', 'post_close', 'post_nxt_close')",
            name="ck_thesis_run_slot",
        ),
        CheckConstraint("subject_kind IN ('index', 'stock')", name="ck_thesis_subject_kind"),
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
        # 폭주만 받는 안전망이다. "임계보다 커야 한다" 같은 정합성은 프롬프트와 저장 전
        # 검증이 본다 — DB로 막으면 모델이 경계값을 낼 때 행 전체가 사라진다.
        CheckConstraint(
            "(up_return_pct IS NULL OR up_return_pct BETWEEN 0 AND 30)"
            " AND (down_return_pct IS NULL OR down_return_pct BETWEEN 0 AND 30)",
            name="ck_thesis_return_pct_range",
        ),
        table_options(
            comment="슬롯마다 만든 시장 추론을 불변으로 보존하는 테이블. 채점과 해설은 thesis_outcome이 갖는다",
            database="default",
        ),
    )

    run_slot: Mapped[RunSlot] = mapped_column(
        _enum_column(RunSlot),
        nullable=False,
        comment=(
            "추론을 만든 슬롯(pre_open은 장전 전망, intraday_morning·intraday_midday·"
            "intraday_afternoon·pre_close는 장중 전망, post_close는 장후 리뷰, "
            "post_nxt_close는 NXT 애프터마켓 리뷰). 슬롯이 곧 추론의 종류다"
        ),
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
            "(장전 = 당일 08:35 KST, 장중 = 당일 10:35·12:35·14:35·15:00 KST, "
            "장후 = 당일 15:30 KST, 애프터마켓 = 당일 20:00 KST). "
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
    # 방향별 **조건부** 크기다. 확률을 이미 곱한 기대값이 아니다. 단일 부호값 한 칸이 아닌
    # 이유는 Slack 결론 줄이 방향을 둘 보일 수 있어서다(`thesis_render._verdicts`).
    # nullable인 것은 이 컬럼이 생기기 전 행을 채울 방법이 없어서다 — 이 테이블은 사후
    # 갱신하지 않는다. `flat`은 정의가 이미 "±임계 안"이라 크기 칸을 두지 않는다.
    up_return_pct: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
        comment=(
            "상승한다는 조건에서 채점 창의 등락률(퍼센트, 양수). 확률을 곱하지 않은 조건부 크기다. "
            "창은 확률과 같은 지평 0이다"
        ),
    )
    down_return_pct: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
        comment=(
            "하락한다는 조건에서 채점 창의 등락률(퍼센트, **양수 크기**). 확률을 곱하지 않은 "
            "조건부 크기다. 창은 확률과 같은 지평 0이다"
        ),
    )
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
    # 원장이 판단을 인질로 잡으면 안 된다 — 원장을 지워도 추론은 남는다. nullable인 것은
    # 이 칸이 생기기 전 행을 채울 방법이 없어서다. 반대 방향 FK는 걸지 않는다: 대화 하나가
    # 추론 여럿을 만들고 실패한 대화는 추론이 없다.
    llm_run_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("thesis_llm_run.id", ondelete="SET NULL"),
        nullable=True,
        comment="이 추론을 만든 LLM 대화. 툴 호출·결과와 정확도를 조인하는 유일한 칸이다",
    )


class ThesisOutcome(EntityBase):
    """추론 하나의 한 지평 = 채점 + (선택) 사후 해설.

    **원 추론 행을 건드리지 않는다.** 사후에 알게 된 것은 전부 여기 새 행이다. 지평이
    넷이라 `thesis`의 채점 칸 한 벌로는 담을 수 없고, 담으려 하면 첫 판단을 덮어써야 한다.

    두 종류의 값이 한 행에 있고 **채우는 주체가 다르다.**

    - **채점**(`evaluated_at`·`actual_return_pct`·`actual_outcome`·`brier_score`)은 SQL과
      순수 함수가 만든다. LLM이 없다. `pre_open` 추론에만 붙는다 — `post_close` 리뷰는
      이미 일어난 일의 해석이라 예측이 아니고 채점할 대상이 없다.
    - **해설**(`narrative`·`verdict`·`narrative_at`·`llm_model`·`prompt_version`)은 LLM이
      만든다. **두 슬롯 모두** 붙는다 — 장후 리뷰는 "오늘 이래서 움직였다"는 인과 주장이라
      며칠 뒤 보도로 검증할 값어치가 오히려 크다.

    그래서 채점 칸이 nullable이다. 대신 **둘 다 비어 있는 행을 CHECK로 막는다** — 채점도
    해설도 없으면 그 행은 없는 것과 같다.

    "`post_close` 행에는 채점이 없다"는 `thesis.run_slot`을 봐야 알 수 있어 CHECK로 못 막는다.
    코드와 테스트가 지킨다(`thesis_evidence`가 마스터로 FK를 걸지 않는 것과 같은 판단).
    """

    __tablename__ = "thesis_outcome"
    __table_args__ = (
        UniqueConstraint("thesis_id", "horizon_days", name="uq_thesis_outcome_natural_key"),
        CheckConstraint("horizon_days IN (0, 1, 3, 5)", name="ck_thesis_outcome_horizon_days"),
        CheckConstraint(
            "actual_outcome IN ('up', 'down', 'flat')",
            name="ck_thesis_outcome_actual_outcome",
        ),
        CheckConstraint(
            "verdict IN ('supported', 'contradicted', 'unresolved')",
            name="ck_thesis_outcome_verdict",
        ),
        CheckConstraint("brier_score BETWEEN 0 AND 2", name="ck_thesis_outcome_brier_range"),
        # 채점 넷은 한 번에 채워지거나 전부 비어 있다. 등락률만 있고 점수가 없는 중간
        # 상태를 두면 "채점했는데 점수가 없는" 행이 조용히 생긴다.
        CheckConstraint(
            "(evaluated_at IS NULL AND actual_return_pct IS NULL"
            " AND actual_outcome IS NULL AND brier_score IS NULL)"
            " OR (evaluated_at IS NOT NULL AND actual_return_pct IS NOT NULL"
            " AND actual_outcome IS NOT NULL AND brier_score IS NOT NULL)",
            name="ck_thesis_outcome_grade_all_or_none",
        ),
        # 해설 다섯도 같다. 판정만 있고 근거 문장이 없으면 되짚을 수 없다.
        CheckConstraint(
            "(narrative IS NULL AND verdict IS NULL AND narrative_at IS NULL"
            " AND llm_model IS NULL AND prompt_version IS NULL)"
            " OR (narrative IS NOT NULL AND verdict IS NOT NULL AND narrative_at IS NOT NULL"
            " AND llm_model IS NOT NULL AND prompt_version IS NOT NULL)",
            name="ck_thesis_outcome_narrative_all_or_none",
        ),
        # 지평 0은 예측일 세션 하나다. 그날의 후속 보도가 아직 쌓이지 않아 해설을 못 쓴다.
        CheckConstraint(
            "horizon_days <> 0 OR (narrative IS NULL AND verdict IS NULL"
            " AND narrative_at IS NULL AND llm_model IS NULL AND prompt_version IS NULL)",
            name="ck_thesis_outcome_zero_horizon_has_no_narrative",
        ),
        # 채점도 해설도 없는 행은 의미가 없다.
        CheckConstraint(
            "evaluated_at IS NOT NULL OR narrative IS NOT NULL",
            name="ck_thesis_outcome_not_empty",
        ),
        # 크기 채점 둘은 함께 있거나 함께 없다. 채점 넷 그룹에 넣지 않는 이유는
        # flat 실현·지평 1·3·5·리비전 전 행에서 **정상적으로** 비어 있어서다.
        CheckConstraint(
            "(return_error_pct IS NULL) = (predicted_return_pct IS NULL)",
            name="ck_thesis_outcome_return_error_all_or_none",
        ),
        # 채점하지 않은 행에 크기 오차만 있을 수 없다.
        CheckConstraint(
            "predicted_return_pct IS NULL OR evaluated_at IS NOT NULL",
            name="ck_thesis_outcome_return_error_needs_grade",
        ),
        table_options(
            comment="추론 하나의 지평별 채점과 사후 해설을 누적하는 테이블",
            database="default",
        ),
    )

    thesis_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("thesis.id", ondelete="CASCADE"),
        nullable=False,
        comment="이 결과가 붙는 thesis 레코드 ID",
    )
    horizon_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment=(
            "지평 길이. **KRX 영업일 수이고 달력일이 아니다.** T+N 관용 표기를 따랐다. "
            "0은 예측일 세션 하나이며 해설을 받지 않는다"
        ),
    )
    as_of_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="이 지평의 기준 시각(UTC). 그 영업일 장후 15:30 KST이며 해설 툴 조회의 창 끝이다",
    )
    dag_run_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="이 행을 쓴 Airflow dag_run_id",
    )
    evaluated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="채점한 시각(UTC). NULL은 미채점이다. 채점은 pre_open 추론에만 붙는다",
    )
    actual_return_pct: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 4),
        nullable=True,
        comment=(
            "예측 시점 기준가 대비 이 지평 종가의 누적 등락률(퍼센트). "
            "**기준가는 지평이 달라도 같다** — 예측일 전 영업일 종가다. 그래야 T+1과 T+5를 비교할 수 있다"
        ),
    )
    actual_outcome: Mapped[ThesisDirection | None] = mapped_column(
        _enum_column(ThesisDirection),
        nullable=True,
        comment=(
            "누적 등락률의 분류(up/down/flat). 임계는 지평마다 다르다 — 하루 임계를 5영업일 "
            "누적에 쓰면 flat이 사실상 사라진다. 예측과 비교하지 않는다(비교는 brier_score가 한다)"
        ),
    )
    brier_score: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 5),
        nullable=True,
        comment=(
            "원 추론의 세 확률을 이 지평 결과로 매긴 3-class Brier 점수. 0이 완벽, 2가 최악이다. "
            "방향만 맞고 확신이 낮았던 경우와 틀린 방향에 확신을 준 경우를 함께 잡는다"
        ),
    )
    # 크기 채점 둘. 방향은 `brier_score`가 채점하고 이쪽은 **독립**이다 — 둘을 합친 종합
    # 점수는 만들지 않는다. **실현된 방향의 조건부 추정만** 대조한다: 방향을 틀린 것은
    # Brier가 이미 벌점을 줬으므로 여기서 또 세면 같은 실수를 두 번 세는 것이다.
    predicted_return_pct: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
        comment=(
            "실현된 방향에 대응하는 thesis의 조건부 크기 스냅샷(퍼센트, 양수). "
            "지평 0에서만, actual_outcome이 flat이 아닐 때만 채운다"
        ),
    )
    return_error_pct: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 4),
        nullable=True,
        comment=(
            "abs(actual_return_pct) - predicted_return_pct(퍼센트포인트). **부호를 유지한다** — "
            "양수면 과소추정, 음수면 과대추정이다. 절댓값 평균(MAE)은 조회가 만든다"
        ),
    )
    narrative: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="이 지평에서 쌓인 보도를 근거로 쓴 사후 해설(한국어). 저장 전에 1000자로 자른다",
    )
    verdict: Mapped[ThesisVerdict | None] = mapped_column(
        _enum_column(ThesisVerdict),
        nullable=True,
        comment=(
            "원 추론의 **이유**가 이후 보도로 지지됐는지(supported/contradicted/unresolved). "
            "brier_score와 다른 것을 잰다 — 저쪽은 방향, 이쪽은 이유다. 둘을 합치지 않는다. "
            "근거 인용 없이 supported·contradicted가 오면 저장 전에 unresolved로 내린다"
        ),
    )
    narrative_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="해설을 쓴 시각(UTC). NULL은 아직 해설이 없다는 뜻이고 다음 실행이 다시 집는다",
    )
    llm_model: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="해설을 만든 모델 식별자. 원 추론의 모델과 다를 수 있다"
    )
    prompt_version: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "해설 프롬프트 판과 변형(`<판>/<변형>`, 예: 1/informed). 변형은 실제 결과를 "
            "프롬프트에 주는 informed와 주지 않는 blind다. 어느 쪽이 나은지는 실측으로 가른다"
        ),
    )
    # 채점에는 LLM이 없으므로 연결 칸이 해설 하나뿐이다.
    narration_run_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("thesis_llm_run.id", ondelete="SET NULL"),
        nullable=True,
        comment="이 해설을 만든 LLM 대화. 채점은 순수 함수라 이 칸이 없다",
    )


class ThesisPrecedent(EntityBase):
    """추론이 프롬프트에서 본 과거 추론 하나. 그래프로 보면 `(:Thesis)-[:INFORMED_BY]->(:Thesis)` 엣지다.

    장전 추론은 같은 대상의 최근 `pre_open` 추론과 그 채점·해설을 프롬프트에 받는다. 무엇을
    봤는지를 여기 남겨야 "이 판단이 어느 과거 판단을 알고 내려졌나"를 나중에 따라갈 수 있다.
    툴 호출 흔적은 트레이스에만 남아 DB에서 보이지 않는다.

    **`thesis_evidence`에 넣지 않는다.** 근거는 모델이 **인용한** 것이고 이것은 우리가
    **보여 준** 것이다. 인용 순서(`rank`)도 없다 — 순서는 precedent의 `run_date`가 말한다.
    """

    __tablename__ = "thesis_precedent"
    __table_args__ = (
        UniqueConstraint("thesis_id", "precedent_id", name="uq_thesis_precedent_natural_key"),
        CheckConstraint("thesis_id <> precedent_id", name="ck_thesis_precedent_not_self"),
        table_options(
            comment="추론이 프롬프트에서 본 과거 추론을 잇는 엣지 테이블. 피드백 루프의 기록이다",
            database="default",
        ),
    )

    thesis_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("thesis.id", ondelete="CASCADE"),
        nullable=False,
        comment="과거 추론을 보고 낸 thesis 레코드 ID",
    )
    # 남이 본 과거 추론은 지우지 못한다 — 지우면 "무엇을 보고 냈나"가 끊긴다.
    precedent_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("thesis.id", ondelete="RESTRICT"),
        nullable=False,
        comment="프롬프트에 실린 과거 thesis 레코드 ID. 같은 대상의 pre_open 추론이다",
    )


class ThesisEvidence(EntityBase):
    """추론이 실제로 인용한 근거 하나. 그래프로 보면 추론에서 나가는 엣지다.

    모델이 툴로 가져온 것 전부가 아니라 **답변에서 인용한 것만** 남는다. 툴 결과 레지스트리에
    없는 `ref`는 저장 전에 버린다.

    `evidence_ref`는 `document`·`instrument` 마스터로 외래키를 걸지 않는다. 원본이 지워져도
    "그때 이것을 근거로 삼았다"는 사실은 남아야 하고, 마스터에 없는 값 하나가 추론 저장 전체를
    죽이면 안 된다(`document_instrument` 선례).

    **사후 해설이 인용한 근거도 같은 테이블에 들어간다.** 행 모양이 같아서
    (`kind`·`ref`·`title`·`url`·`detail`·`rank`) 테이블을 복제하지 않고 `outcome_horizon_days`
    한 칸으로 가른다. `thesis_outcome`으로 외래키를 걸지 않는 것도 같은 이유다 — nullable FK
    둘에 XOR CHECK를 얹는 형태보다 조용히 틀릴 여지가 적고, Neo4j에서도 `(kind, ref)` 노드
    키를 그대로 재사용한다.
    """

    __tablename__ = "thesis_evidence"
    __table_args__ = (
        UniqueConstraint(
            "thesis_id",
            "outcome_horizon_days",
            "evidence_kind",
            "evidence_ref",
            name="uq_thesis_evidence_ref",
        ),
        UniqueConstraint("thesis_id", "outcome_horizon_days", "rank", name="uq_thesis_evidence_rank"),
        CheckConstraint(
            "evidence_kind IN ('document', 'disclosure', 'macro_change', 'technical_signal')",
            name="ck_thesis_evidence_kind",
        ),
        CheckConstraint("rank > 0", name="ck_thesis_evidence_rank_positive"),
        # 해설을 받는 지평만 온다. 0은 해설이 없어(thesis_outcome CHECK) 근거도 없다.
        CheckConstraint(
            "outcome_horizon_days IS NULL OR outcome_horizon_days IN (1, 3, 5)",
            name="ck_thesis_evidence_outcome_horizon_days",
        ),
        CheckConstraint(
            "direction IS NULL OR direction IN ('up', 'down', 'flat')",
            name="ck_thesis_evidence_direction",
        ),
        # 방향과 경로는 한 쌍이다. 방향만 있고 경로가 없으면 그래프 엣지가 "왜"를 잃는다.
        CheckConstraint(
            "(direction IS NULL AND mechanism IS NULL) OR (direction IS NOT NULL AND mechanism IS NOT NULL)",
            name="ck_thesis_evidence_claim_all_or_none",
        ),
        table_options(
            comment="추론과 사후 해설이 인용한 근거를 순위와 함께 보존하는 테이블",
            database="default",
        ),
    )

    thesis_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("thesis.id", ondelete="CASCADE"),
        nullable=False,
        comment="이 근거를 인용한 thesis 레코드 ID",
    )
    outcome_horizon_days: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment=(
            "누가 인용했는지. NULL은 원 추론이 인용한 근거, 1·3·5는 그 지평의 사후 해설이 "
            "인용한 근거다. thesis_outcome으로 외래키를 걸지 않는다"
        ),
    )
    evidence_kind: Mapped[ThesisEvidenceKind] = mapped_column(
        _enum_column(ThesisEvidenceKind),
        nullable=False,
        comment=(
            "근거의 출처 종류(document, disclosure, macro_change, technical_signal). "
            "evidence_ref 앞자리와 같은 값이다"
        ),
    )
    evidence_ref: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment=(
            "툴 결과가 준 ref 그대로. `<evidence_kind>:<id>` 2단이며 앞자리는 evidence_kind와 글자 그대로 같다"
            "(document:123, disclosure:20260821000123, macro_change:SP500_FUT, technical_signal:1042). "
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
    # 아래 둘은 **이 추론이 이 근거를 어떻게 썼나**다. detail의 direction은 문서 평가 때 붙은
    # 문서 자체의 방향이라 다른 값이다. 그래프 엣지 `(:Thesis)-[:CITES {direction, mechanism}]`의
    # 속성이고, 사후 해설이 인용한 근거(outcome_horizon_days NOT NULL)에는 없다.
    direction: Mapped[ThesisDirection | None] = mapped_column(
        _enum_column(ThesisDirection),
        nullable=True,
        comment=(
            "이 근거가 대상을 어느 쪽으로 미는지(up/down/flat). 원 추론이 인용한 근거에만 있고 "
            "사후 해설의 인용에는 NULL이다"
        ),
    )
    mechanism: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="그 방향으로 작용하는 경로를 적은 한 문장. direction과 함께 채워지거나 함께 비어 있다",
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


class ThesisLlmRun(EntityBase):
    """LLM 대화 한 번. 그 안에서 부른 툴이 `thesis_tool_call`이다.

    **"대화 하나"가 이 원장의 단위다.** 한 대화가 여러 대상을 한 번에 다루므로
    (`ThesisBuilder`: "실행당 대화 하나에 모든 subject를 한 번에") 툴 호출은 추론 한 건이
    아니라 대화에 속한다.

    **자연키를 두지 않는다.** 실패한 대화도 남겨야 하고 재시도는 새 대화라, 같은
    `(kind, run_date, run_slot, horizon_days)`에 행이 여럿일 수 있다. 그것이 사실이고
    패턴 분석에 필요한 정보다. **원장이지 판단이 아니라서** "첫 성공본 불변"이 적용되지 않는다.

    설계는 `docs/analysis/market-thesis/13-llm-ledger.md`에 있다.
    """

    __tablename__ = "thesis_llm_run"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('forecast', 'review', 'nxt_review', 'narration')",
            name="ck_thesis_llm_run_kind",
        ),
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="ck_thesis_llm_run_status",
        ),
        CheckConstraint(
            "horizon_days IS NULL OR horizon_days IN (1, 3, 5)",
            name="ck_thesis_llm_run_horizon_days",
        ),
        # 상태 셋이 허용하는 조합은 이것뿐이다. running인데 끝난 시각이 있거나 failed인데
        # 사유가 없는 행을 두면 "끊긴 대화"와 "실패한 대화"를 나중에 못 가른다.
        CheckConstraint(
            "(status = 'running' AND finished_at IS NULL AND error IS NULL)"
            " OR (status = 'succeeded' AND finished_at IS NOT NULL AND error IS NULL)"
            " OR (status = 'failed' AND finished_at IS NOT NULL AND error IS NOT NULL)",
            name="ck_thesis_llm_run_status_shape",
        ),
        table_options(
            comment="LLM 대화 한 번의 원장. 툴 호출 패턴과 정확도의 상관을 재려고 남긴다",
            database="default",
        ),
    )

    kind: Mapped[LlmRunKind] = mapped_column(
        _enum_column(LlmRunKind),
        nullable=False,
        comment=(
            "대화의 종류(forecast·review·nxt_review는 추론 생성, narration은 사후 해설). "
            "슬롯에서 유도할 수 없다 — 같은 post_close 슬롯에 생성과 해설이 둘 다 있다"
        ),
    )
    run_date: Mapped[date] = mapped_column(
        nullable=False,
        comment=(
            "대화가 대상으로 삼은 세션 날짜(KST). **해설이면 원 추론일이다** — "
            "thesis와 같은 축으로 조인하기 위해서다. 실행일은 as_of_at과 dag_run_id가 말한다"
        ),
    )
    run_slot: Mapped[RunSlot] = mapped_column(
        _enum_column(RunSlot),
        nullable=False,
        comment="대상 슬롯. 해설이면 원 추론의 슬롯이다",
    )
    horizon_days: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment=(
            "해설 대화의 지평(1·3·5). 생성 대화는 NULL이다. 해설이 지평마다 갈리는 것은 "
            "툴 조회의 기준 시각이 지평마다 달라 한 대화에 섞을 수 없어서다"
        ),
    )
    as_of_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="이 대화의 툴 조회 기준 시각(UTC). event-time cutoff다",
    )
    dag_run_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="이 대화를 돌린 Airflow dag_run_id",
    )
    try_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment=(
            "그 태스크의 시도 번호(1부터). dag_run_id는 재시도에도 같아서 이 칸이 없으면 "
            "재시도 대화를 서로 구분할 방법이 없다"
        ),
    )
    llm_model: Mapped[str] = mapped_column(Text, nullable=False, comment="이 대화를 돈 모델 식별자")
    prompt_version: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="프롬프트 판. 해설은 `<판>/<변형>` 형태라 생성 대화의 판 번호와 체계가 다르다",
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="대화를 시작한 시각(UTC). 이 행은 그래프 호출 전에 먼저 커밋된다",
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="대화가 끝난 시각(UTC). NULL이면 종료를 기록하지 못했다는 뜻이다",
    )
    status: Mapped[LlmRunStatus] = mapped_column(
        _enum_column(LlmRunStatus),
        nullable=False,
        comment="running·succeeded·failed. running으로 남은 행은 삭제할 찌꺼기가 아니라 감사 기록이다",
    )
    error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="실패 사유. status가 failed일 때만 채운다",
    )
    tool_rounds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="조사 왕복 수. 왕복 하나가 모델 호출 하나다",
    )
    tool_calls: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment=(
            "기록된 툴 호출 수. **상한을 재는 카운터와 다른 수다** — 이 값은 unknown tool과 "
            "인자 검증 실패도 세지만 툴박스의 예산 카운터는 함수에 진입한 것만 센다"
        ),
    )
    tool_result_chars: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment=(
            "모델에게 실제로 돌아간 결과의 누적 문자 수(delivered=true만). **예산 카운터와 "
            "다른 수다** — 그쪽은 버려진 결과도 센다. MAX_TOOL_RESULT_CHARS와 직접 비교하지 않는다"
        ),
    )


class ThesisToolCall(EntityBase):
    """대화 하나 안의 툴 호출 하나. 요청·인자·결과 전문을 그대로 남긴다.

    **결과 본문을 버리지 않는다.** `document`는 upsert로 덮어써서 그때 값을 복원할 수 없고,
    이 행이 모델이 실제로 본 스냅샷의 유일한 사본이 된다.
    """

    __tablename__ = "thesis_tool_call"
    __table_args__ = (
        UniqueConstraint("llm_run_id", "seq", name="uq_thesis_tool_call_seq"),
        UniqueConstraint("llm_run_id", "round_no", "tool_call_id", name="uq_thesis_tool_call_id"),
        CheckConstraint(
            "error_kind IN ('unknown_tool', 'validation', 'limit', 'execution', 'cancelled')",
            name="ck_thesis_tool_call_error_kind",
        ),
        # 결과와 오류는 배타다. 둘 다 비면 "돌았는데 아무 것도 없다"가 되어 조용히 틀린다.
        CheckConstraint(
            "(result IS NULL) <> (error IS NULL)",
            name="ck_thesis_tool_call_result_xor_error",
        ),
        # 오류 종류는 오류가 있을 때만 있다. 문자열을 파싱해 분류하지 않으려는 칸이다.
        CheckConstraint(
            "(error IS NULL) = (error_kind IS NULL)",
            name="ck_thesis_tool_call_error_kind_pairs",
        ),
        CheckConstraint("seq > 0 AND round_no > 0", name="ck_thesis_tool_call_positive_order"),
        table_options(
            comment="LLM 대화가 부른 툴 하나. 인자와 결과 전문을 남겨 나중에 패턴과 상관을 잰다",
            database="default",
        ),
    )

    llm_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("thesis_llm_run.id", ondelete="CASCADE"),
        nullable=False,
        comment="이 호출이 속한 대화",
    )
    seq: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="대화 안의 기록 순서(1부터). **인과 순서가 아니다** — 같은 라운드의 호출은 병렬일 수 있다",
    )
    round_no: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="몇 번째 tool round의 요청인가(1부터). 한 라운드가 모델 응답 하나다",
    )
    tool_call_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="제공처가 준 tool call id. AIMessage의 요청과 ToolMessage의 결과를 잇는 키다",
    )
    tool_name: Mapped[str] = mapped_column(Text, nullable=False, comment="부른 툴 이름")
    arguments: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        comment="모델이 보낸 인자 원본(StructuredTool 검증 전). AIMessage.tool_calls의 args 그대로다",
    )
    validated_arguments: Mapped[dict[str, object] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment=(
            "검증·기본값 적용 뒤 실제 함수에 들어간 인자. unknown tool과 인자 검증 실패는 "
            "함수에 진입하지 않아 NULL이다"
        ),
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="모델의 호출 요청을 등록한 시각(UTC)",
    )
    duration_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="실제 함수가 돈 시간(밀리초). 진입 전 거절은 NULL이다. 툴 SQL이 느려지는 것을 본다",
    )
    result_chars: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="결과 문자 수. 오류면 0이다",
    )
    result: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "결과 본문 전문. 성공이면 툴이 돌려준 JSON 문자열이다. jsonb로 굳히지 않는 것은 "
            "실패 본문이 평문이어서다 — 분석은 error IS NULL 뒤에 result::jsonb를 쓴다"
        ),
    )
    delivered: Mapped[bool] = mapped_column(
        nullable=False,
        comment=(
            "이 결과·오류가 모델 대화에 실제로 돌아갔나. sibling 예외로 ToolNode가 결과를 버리면 "
            "false다 — 결과는 진짜이고 모델만 못 봤다. 실행조차 못 한 것은 error_kind=cancelled다"
        ),
    )
    error_kind: Mapped[ToolCallErrorKind | None] = mapped_column(
        _enum_column(ToolCallErrorKind),
        nullable=True,
        comment="실패 종류. 오류가 있을 때만 채운다. 오류 문자열을 파싱해 분류하지 않는다",
    )
    error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "실패 사유. delivered면 ToolNode가 만든 ToolMessage 본문 그대로이고, "
            "ToolMessage가 없으면 래퍼가 잡은 예외 문자열이다"
        ),
    )
