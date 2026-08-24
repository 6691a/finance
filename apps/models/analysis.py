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

    `pre_open`은 장이 열리기 전의 전망(forecast)이고 `post_close`는 KRX 정규장이 닫힌 뒤의
    리뷰(review)다. `post_nxt_close`는 NXT 애프터마켓(15:30~20:00)이 닫힌 뒤의 리뷰이고
    대상이 종목뿐이다 — NXT에는 지수가 없다. 별도 `kind` 컬럼을 두지 않는 이유는 둘이 항상
    같이 움직이기 때문이다 — 슬롯 하나에 종류 둘이 오는 경우가 없다.
    """

    PRE_OPEN = "pre_open"
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


# 채점·해설 지평. KRX 영업일 수이고 달력일이 아니다.
# 0은 예측일 세션 하나이며 해설을 받지 않는다(그날의 보도가 아직 쌓이지 않았다).
THESIS_HORIZON_DAYS: tuple[int, ...] = (0, 1, 3, 5)
NARRATED_HORIZON_DAYS: tuple[int, ...] = (1, 3, 5)


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


class StockEventType(StrEnum):
    """기대·실제를 대조하는 종목 이벤트의 종류.

    LLM 추출에는 이 목록을 프롬프트로 주고 목록 밖 값은 저장 전에 버린다
    (`document_instrument` 태깅과 같은 패턴). 값을 늘리면 CHECK 리비전과
    `EVENT_METRICS`, 추출 프롬프트가 함께 늘어난다.
    """

    SHAREHOLDER_RETURN = "shareholder_return"
    EARNINGS = "earnings"
    GUIDANCE = "guidance"


class StockEventClaimKind(StrEnum):
    """주장의 종류. 기대(expectation)인가 실제 발표값(actual)인가."""

    EXPECTATION = "expectation"
    ACTUAL = "actual"


class StockEventMetric(StrEnum):
    """이벤트 지표. **단위는 지표가 정한다** — 전부 원(KRW)이다.

    `indicator_observation`의 교훈("단위는 계열마다 다르다")을 여기서는 지표 정의가
    받는다. 별도 unit 컬럼을 두지 않고, 추출 시점에 코드가 원 단위로 정규화한다.

    실적 셋(revenue, operating_profit, net_income)은 `market.EarningsMetric`과 글자
    그대로 같다. 판정이 `earnings_fact`를 대응표 없이 조인하기 위해서다
    (`KrxMarket`이 `quote_bar.symbol`과 값을 맞춘 것과 같은 결정). 테스트가 대조한다.
    """

    TOTAL_RETURN_AMOUNT = "total_return_amount"
    BUYBACK_AMOUNT = "buyback_amount"
    DIVIDEND_TOTAL = "dividend_total"
    DIVIDEND_PER_SHARE = "dividend_per_share"
    REVENUE = "revenue"
    OPERATING_PROFIT = "operating_profit"
    NET_INCOME = "net_income"


class SurpriseVerdict(StrEnum):
    """기대 대비 실제의 판정. 숫자 비교가 낳는 세 값이며 LLM이 만들지 않는다."""

    BEAT = "beat"
    MEET = "meet"
    MISS = "miss"


# 이벤트 종류마다 허용하는 지표. 조합 검증은 Pydantic(추출)과 순수 함수(판정)가 한다 —
# DB CHECK는 합집합만 막는다(두 컬럼을 엮는 CHECK는 값이 늘 때마다 리비전이 돼서 두지 않는다).
EVENT_METRICS: dict[StockEventType, tuple[StockEventMetric, ...]] = {
    StockEventType.SHAREHOLDER_RETURN: (
        StockEventMetric.TOTAL_RETURN_AMOUNT,
        StockEventMetric.BUYBACK_AMOUNT,
        StockEventMetric.DIVIDEND_TOTAL,
        StockEventMetric.DIVIDEND_PER_SHARE,
    ),
    StockEventType.EARNINGS: (
        StockEventMetric.REVENUE,
        StockEventMetric.OPERATING_PROFIT,
        StockEventMetric.NET_INCOME,
    ),
    StockEventType.GUIDANCE: (
        StockEventMetric.REVENUE,
        StockEventMetric.OPERATING_PROFIT,
    ),
}

# `period_key`가 허용하는 표기. 연간(2026), 분기(2026Q2), 반기(2026H1)뿐이다.
# 느슨하게 받으면 기대와 실제가 다른 표기로 저장돼 조용히 매칭이 깨진다.
PERIOD_KEY_PATTERN = r"^[0-9]{4}(Q[1-4]|H[12])?$"


class Thesis(EntityBase):
    """시장 추론 하나. 그래프로 보면 노드다.

    **맞고 틀림이 목적이 아니다.** "어떤 정보를 근거로 어떤 결론을 냈다"가 기록으로 남는 것이
    목적이고, 채점은 그 기록 위에 나중에 얹힌다.

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
        CheckConstraint("run_slot IN ('pre_open', 'post_close', 'post_nxt_close')", name="ck_thesis_run_slot"),
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
        table_options(
            comment="슬롯마다 만든 시장 추론을 불변으로 보존하는 테이블. 채점과 해설은 thesis_outcome이 갖는다",
            database="default",
        ),
    )

    run_slot: Mapped[RunSlot] = mapped_column(
        _enum_column(RunSlot),
        nullable=False,
        comment=(
            "추론을 만든 슬롯(pre_open은 장전 전망, post_close는 장후 리뷰, "
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
            "(장전 = 당일 08:35 KST, 장후 = 당일 15:30 KST, 애프터마켓 = 당일 20:00 KST). "
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
            "evidence_kind IN ('document', 'disclosure', 'macro_change')",
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


class StockEventClaim(EntityBase):
    """문서 또는 컨센서스 수집이 낸 이벤트 주장 한 건. append-only다.

    기대(리포트, 몇 주 전)와 실제(발표, 오늘)는 다른 문서에서 다른 시점에 온다. 잇는 키가
    `(stock_code, event_type, period_key) + metric`이고, 이 테이블은 그 키 위에 주장을
    누적한다. 같은 증권사가 기대를 올려 잡으면 새 문서의 새 행이다 — 갱신 이력이 그대로
    남고, "최신만 쓴다"는 판정 시점의 집계 규칙이다.

    출처는 둘 중 하나다. LLM 추출이면 `document_id`, 컨센서스 수집이면 `source_record_id`가
    차고 CHECK가 정확히 하나만 차는 것을 강제한다. `instrument`로 외래키를 걸지 않는다
    (`document_instrument` 선례 — 마스터에 없는 값 하나가 저장 전체를 죽이면 안 된다).
    """

    __tablename__ = "stock_event_claim"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "event_type",
            "period_key",
            "metric",
            "claim_kind",
            name="uq_stock_event_claim_document_claim",
        ),
        CheckConstraint(
            "event_type IN ('shareholder_return', 'earnings', 'guidance')",
            name="ck_stock_event_claim_event_type",
        ),
        CheckConstraint(
            "metric IN ('total_return_amount', 'buyback_amount', 'dividend_total',"
            " 'dividend_per_share', 'revenue', 'operating_profit', 'net_income')",
            name="ck_stock_event_claim_metric",
        ),
        CheckConstraint(
            "claim_kind IN ('expectation', 'actual')",
            name="ck_stock_event_claim_kind",
        ),
        CheckConstraint(
            "period_key ~ '^[0-9]{4}(Q[1-4]|H[12])?$'",
            name="ck_stock_event_claim_period_key",
        ),
        # 범위는 한 쌍이다. 한쪽만 있으면 "9조에서 얼마까지"인지 알 수 없다.
        CheckConstraint(
            "(value_low IS NULL AND value_high IS NULL)"
            " OR (value_low IS NOT NULL AND value_high IS NOT NULL AND value_low <= value_high)",
            name="ck_stock_event_claim_range_pair",
        ),
        # 출처는 문서(LLM 추출) 또는 컨센서스 수집 레코드 중 정확히 하나다.
        CheckConstraint(
            "(document_id IS NULL) <> (source_record_id IS NULL)",
            name="ck_stock_event_claim_source_xor",
        ),
        Index("ix_stock_event_claim_event", "stock_code", "event_type", "period_key"),
        Index("ix_stock_event_claim_source_record_id", "source_record_id"),
        table_options(
            comment="종목 이벤트에 대한 기대·실제 주장을 출처와 함께 누적하는 테이블",
            database="default",
        ),
    )

    stock_code: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="한국거래소 종목코드(예: 005930). instrument로 외래키를 걸지 않는다",
    )
    event_type: Mapped[StockEventType] = mapped_column(
        _enum_column(StockEventType),
        nullable=False,
        comment="이벤트 종류(shareholder_return, earnings, guidance)",
    )
    period_key: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="대상 기간 표기. 연간(2026), 분기(2026Q2), 반기(2026H1)만 허용한다. 기대와 실제를 잇는 키의 한 축이다",
    )
    metric: Mapped[StockEventMetric] = mapped_column(
        _enum_column(StockEventMetric),
        nullable=False,
        comment="이벤트 지표. 단위는 지표가 정하며 전부 원(KRW)이다. 실적 지표는 earnings_fact.metric과 같은 값이다",
    )
    claim_kind: Mapped[StockEventClaimKind] = mapped_column(
        _enum_column(StockEventClaimKind),
        nullable=False,
        comment="주장의 종류(expectation은 기대치, actual은 실제 발표값)",
    )
    value: Mapped[Decimal] = mapped_column(
        Numeric(24, 2),
        nullable=False,
        comment="주장 값. 원문 표기(조·억)를 원 단위로 정규화한 값이다. 범위 주장이면 중앙값을 둔다",
    )
    value_low: Mapped[Decimal | None] = mapped_column(
        Numeric(24, 2),
        nullable=True,
        comment="범위 주장의 하한(원). 단일 값 주장이면 NULL이고 value_high와 함께 차거나 함께 빈다",
    )
    value_high: Mapped[Decimal | None] = mapped_column(
        Numeric(24, 2),
        nullable=True,
        comment="범위 주장의 상한(원). 단일 값 주장이면 NULL이다",
    )
    stated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment=(
            "주장 시점(UTC). 문서면 published_at(없으면 detected_at), 컨센서스면 조회 시각이다. "
            "판정이 발표 전 기대만 고르는 기준이라 모델이 아니라 코드가 채운다"
        ),
    )
    broker: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="주장 주체 표기(증권사 등, 문서 제목 끝 낱말). 기사 인용처럼 주체를 모르면 NULL이고 컨센서스도 NULL이다",
    )
    document_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("document.id", ondelete="CASCADE"),
        nullable=True,
        comment="주장을 추출한 문서 ID. 컨센서스 수집이면 NULL이다",
    )
    source_record_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("source_record.id", ondelete="RESTRICT"),
        nullable=True,
        comment="컨센서스 수집의 source_record 레코드 ID. LLM 추출이면 NULL이다",
    )


class StockEventExtraction(EntityBase):
    """문서 하나의 추출 원장. "이미 뽑았다"의 증거다.

    주장 0건이 정상값이라 `stock_event_claim`만으로는 "뽑았는데 없었다"와 "아직 안 뽑았다"를
    가르지 못한다(`source_record`가 관측값 0건에도 남는 것과 같은 이유). 본문이 바뀌면
    (`extracted_content_hash` 불일치) 또는 프롬프트 판이 오르면 다시 뽑는다 —
    `document.assessed_content_hash`와 같은 장치다.
    """

    __tablename__ = "stock_event_extraction"
    __table_args__ = (
        UniqueConstraint("document_id", name="uq_stock_event_extraction_document"),
        CheckConstraint("claim_count >= 0", name="ck_stock_event_extraction_claim_count"),
        table_options(
            comment="문서별 이벤트 주장 추출 이력을 남기는 원장 테이블. 주장 0건도 기록한다",
            database="default",
        ),
    )

    document_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("document.id", ondelete="CASCADE"),
        nullable=False,
        comment="추출한 문서 ID",
    )
    extracted_content_hash: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="추출 시점의 document.content_hash. 현재 값과 다르면 본문이 바뀐 것이라 다시 뽑는다",
    )
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="추출을 마친 시각(UTC)",
    )
    llm_model: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="추출에 쓴 모델 식별자",
    )
    prompt_version: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="추출 프롬프트 판. 이 값이 오른 문서는 재추출 대상이 된다",
    )
    claim_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="이 문서에서 저장된 주장 수. 0이 정상값이다 — 대부분 문서에는 이벤트 주장이 없다",
    )


class StockEventOutcome(EntityBase):
    """이벤트·지표 하나의 서프라이즈 판정. 첫 성공본 불변이다.

    실제값이 생기면 발표 전 기대들과 대조해 한 행을 남긴다. **판정에 LLM이 없다** — 대표
    기대치 집계와 분류는 순수 함수가 한다(thesis 숫자 규칙과 같다). `INSERT ... ON CONFLICT
    DO NOTHING`이라 발표 뒤 기대 행이 늦게 추출돼도 판정을 다시 내지 않는다 — 덮어쓰면
    Slack으로 이미 나간 판정과 DB가 어긋난다. 잘못된 판정도 고치지 않는다.
    """

    __tablename__ = "stock_event_outcome"
    __table_args__ = (
        UniqueConstraint(
            "stock_code",
            "event_type",
            "period_key",
            "metric",
            name="uq_stock_event_outcome_natural_key",
        ),
        CheckConstraint(
            "event_type IN ('shareholder_return', 'earnings', 'guidance')",
            name="ck_stock_event_outcome_event_type",
        ),
        CheckConstraint(
            "metric IN ('total_return_amount', 'buyback_amount', 'dividend_total',"
            " 'dividend_per_share', 'revenue', 'operating_profit', 'net_income')",
            name="ck_stock_event_outcome_metric",
        ),
        CheckConstraint(
            "verdict IN ('beat', 'meet', 'miss')",
            name="ck_stock_event_outcome_verdict",
        ),
        CheckConstraint(
            "period_key ~ '^[0-9]{4}(Q[1-4]|H[12])?$'",
            name="ck_stock_event_outcome_period_key",
        ),
        CheckConstraint("expectation_count > 0", name="ck_stock_event_outcome_expectation_count"),
        table_options(
            comment="이벤트 지표 하나의 기대 대비 실제 판정을 불변으로 보존하는 테이블",
            database="default",
        ),
    )

    stock_code: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="한국거래소 종목코드(예: 005930). instrument로 외래키를 걸지 않는다",
    )
    event_type: Mapped[StockEventType] = mapped_column(
        _enum_column(StockEventType),
        nullable=False,
        comment="이벤트 종류(shareholder_return, earnings, guidance)",
    )
    period_key: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="대상 기간 표기(2026, 2026Q2, 2026H1). stock_event_claim과 같은 규칙이다",
    )
    metric: Mapped[StockEventMetric] = mapped_column(
        _enum_column(StockEventMetric),
        nullable=False,
        comment="이벤트 지표. 단위는 지표가 정하며 전부 원(KRW)이다",
    )
    expected_value: Mapped[Decimal] = mapped_column(
        Numeric(24, 2),
        nullable=False,
        comment="판정에 쓴 대표 기대치(원). 컨센서스가 있으면 최신 컨센서스, 없으면 주체별 최신 기대의 중앙값이다",
    )
    expectation_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="대조한 기대 행 수. 기대가 없던 발표는 판정하지 않으므로 항상 1 이상이다",
    )
    actual_value: Mapped[Decimal] = mapped_column(
        Numeric(24, 2),
        nullable=False,
        comment="실제 발표값(원). 실적은 earnings_fact, 그 외는 actual 주장에서 온다",
    )
    surprise_pct: Mapped[Decimal] = mapped_column(
        Numeric(10, 4),
        nullable=False,
        comment="(실제 - 기대) / |기대| × 100. 기대 대비 어긋난 정도(퍼센트)다",
    )
    verdict: Mapped[SurpriseVerdict] = mapped_column(
        _enum_column(SurpriseVerdict),
        nullable=False,
        comment="판정(beat/meet/miss). |surprise_pct|가 허용 밴드 안이면 meet, 밖이면 부호로 가른다. LLM이 만들지 않는다",
    )
    announced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="실제값 원본의 발행·감지 시각(UTC). 이 시각 전의 기대만 판정에 들어간다",
    )
    actual_ref: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="실제값 원본 참조(earnings_fact:<id> 또는 document:<id>). thesis_evidence.evidence_ref와 같은 2단 표기다",
    )
    dag_run_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="이 행을 쓴 Airflow dag_run_id",
    )
