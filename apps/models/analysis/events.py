"""기업 이벤트의 기대와 실제 — 주장, 추출 원장, 서프라이즈 판정.

기대와 실제를 잇는 키는 `(stock_code, event_type, period_key) + metric`이다.
**`EVENT_METRICS`와 `market.EarningsMetric`을 import로 합치지 않는다** — 값을 중복하고
테스트로 일치를 검증해야 `apps.models` 초기화 순환이 생기지 않는다.
"""

from datetime import datetime
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
from sqlalchemy.orm import Mapped, mapped_column

from apps.core.database import EntityBase, table_options
from apps.models.analysis._columns import _enum_column


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
