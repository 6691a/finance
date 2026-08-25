"""기업 단위 사실 — 공시, 실적, 애널리스트 의견.

시세가 아니라 그 종목에 대해 누가 무엇을 발표했는가다. `earnings_fact`가 실적 실제값의
원본이고 `stock_event_outcome`의 판정이 이것을 읽는다(`apps/models/analysis.py`).
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column

from apps.core.database import EntityBase, table_options


class DisclosureEvent(EntityBase):
    """DART에 접수된 공시 하나를 종류와 관계없이 보존한다.

    이 테이블이 있는 이유는 **분봉·수급과 같은 시간축에 이벤트를 놓기 위해서다.** 가격이
    움직이기 전에 어떤 공시가 나왔는지 알 수 없으면 급변을 시장 신호로 오독한다.

    시각이 둘이고 의미가 다르다. `receipt_date`는 DART가 준 접수일(날짜뿐)이고 `detected_at`은
    우리가 그 접수번호를 처음 본 시각이다. **`receipt_date`를 자정이나 장 마감 시각으로 꾸며
    시각 컬럼을 만들지 않는다.**

    분 단위 접수 시각은 저장하지 않는다. 그 값은 공식 RSS에만 있는데 RSS가 전 상장사 최신
    50건뿐이라 실측에서 1시간 35분치만 덮었고, 우리가 저장한 공시와 겹치는 접수번호가
    하나도 없었다. 2분 폴링의 `detected_at`이 이미 그만한 해상도를 주므로 호출 하나와
    계보 행 하나를 매 폴링에 더할 이유가 없다.
    """

    __tablename__ = "disclosure_event"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "rcept_no",
            name="uq_disclosure_event_natural_key",
        ),
        Index("ix_disclosure_event_stock_code_receipt_date", "stock_code", "receipt_date"),
        Index("ix_disclosure_event_source_record_id", "source_record_id"),
        table_options(
            comment="DART 공시 접수 이벤트를 접수번호 단위로 보존하는 테이블",
            database="default",
        ),
    )

    provider: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="데이터 제공처 식별자(dart). 같은 수집의 source_record.source와 같은 값이다",
    )
    corp_code: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="DART 회사 고유번호(예: 00126380). 종목코드와 다른 체계다",
    )
    stock_code: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="한국거래소 종목코드(예: 005930)",
    )
    company_name: Mapped[str] = mapped_column(Text, nullable=False, comment="DART가 준 회사명 원문")
    rcept_no: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="DART 접수번호. 공시·원문·재무제표를 잇는 키이며 제공처 안에서 고유하다",
    )
    report_name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="보고서명 원문. 정정 접두사를 포함해 손대지 않고 그대로 저장한다",
    )
    filer_name: Mapped[str] = mapped_column(Text, nullable=False, comment="제출인 이름 원문(flr_nm)")
    corp_class: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="DART 법인 구분(corp_cls). Y=유가증권, K=코스닥, N=코넥스, E=기타",
    )
    receipt_date: Mapped[date] = mapped_column(
        nullable=False,
        comment="DART 접수일(rcept_dt). 날짜뿐이고 시·분이 없다. 기준 시간대는 한국이다",
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment=(
            "이 접수번호를 처음 수집한 시각(UTC). 재수집해도 갱신하지 않는다. "
            "실제 공시 시각이 아니라 최초 감지 시각이므로 화면에도 그렇게 표시한다. "
            "2분 폴링이라 공시 시각의 상한이며 오차는 폴링 주기와 DART 반영 지연의 합이다"
        ),
    )
    remarks: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="DART 비고 원문(rm). 정정·철회·유가증권신고서 관련 표시가 들어온다",
    )
    source_record_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("source_record.id", ondelete="RESTRICT"),
        nullable=False,
        comment="이 행을 만든 회사별 공시 목록 조회의 source_record 레코드 ID",
    )


class EarningsReleaseType(StrEnum):
    """실적 숫자의 출처 종류."""

    # 연결재무제표기준영업(잠정)실적(공정공시)과 그 정정. 공시 원문 표에서 읽는다.
    PROVISIONAL = "provisional"
    # 사업·반기·분기보고서. OpenDART 재무제표 API에서 읽는다.
    PERIODIC = "periodic"


class StatementScope(StrEnum):
    """재무제표 범위. 연결과 별도를 합치거나 서로 대체하지 않는다."""

    CFS = "CFS"
    OFS = "OFS"


class AmountBasis(StrEnum):
    """금액의 기간 기준. 3개월치와 누계는 다른 값이라 자연키를 나눈다."""

    PERIOD = "period"
    CUMULATIVE = "cumulative"


class EarningsMetric(StrEnum):
    """1차 저장 지표 셋. 전년 대비 증감률은 저장하지 않고 조회에서 계산한다."""

    REVENUE = "revenue"
    OPERATING_PROFIT = "operating_profit"
    NET_INCOME = "net_income"


class EarningsFact(EntityBase):
    """공시에서 추출한 실적 지표를 지표당 한 행으로 저장한다.

    `disclosure_event`와 `rcept_no`로 이어지지만 **외래키를 걸지 않는다.** 걸면 원문 파싱이
    공시 이벤트 저장보다 앞서야 하고, 잠정실적 표 형식이 바뀐 날 공시 이벤트까지 잃는다.
    둘은 별개 트랜잭션이며 실적 추출 실패가 이벤트 수집을 막지 않는다.

    **새 접수번호의 정정 공시는 새 행이다.** 이전 행을 덮어쓰지 않고, 조회하는 쪽이 같은
    회사·기간·지표 중 가장 최근 접수번호를 고른다.

    금액은 원문 단위를 그대로 두지 않고 원 단위로 정규화한다. 변환 배수는
    `source_record.metadata`에 남긴다. 음수와 0은 정상값이고, 공시에 없는 지표는 행을
    만들지 않는다.
    """

    __tablename__ = "earnings_fact"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "rcept_no",
            "statement_scope",
            "amount_basis",
            "metric",
            name="uq_earnings_fact_natural_key",
        ),
        CheckConstraint(
            "release_type IN ('provisional', 'periodic')",
            name="ck_earnings_fact_release_type",
        ),
        CheckConstraint(
            "statement_scope IN ('CFS', 'OFS')",
            name="ck_earnings_fact_statement_scope",
        ),
        CheckConstraint(
            "amount_basis IN ('period', 'cumulative')",
            name="ck_earnings_fact_amount_basis",
        ),
        CheckConstraint(
            "metric IN ('revenue', 'operating_profit', 'net_income')",
            name="ck_earnings_fact_metric",
        ),
        Index("ix_earnings_fact_stock_code_period_end", "stock_code", "period_end"),
        Index("ix_earnings_fact_source_record_id", "source_record_id"),
        table_options(
            comment="DART 공시에서 추출한 실적 지표를 지표당 한 행으로 저장하는 테이블",
            database="default",
        ),
    )

    provider: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="데이터 제공처 식별자(dart). 같은 수집의 source_record.source와 같은 값이다",
    )
    stock_code: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="한국거래소 종목코드(예: 005930)",
    )
    rcept_no: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="숫자의 출처가 된 공시의 DART 접수번호. disclosure_event와 같은 값이지만 외래키는 걸지 않는다",
    )
    release_type: Mapped[EarningsReleaseType] = mapped_column(
        SqlEnum(
            EarningsReleaseType,
            native_enum=False,
            length=20,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        comment="숫자의 출처 종류(provisional=잠정실적 공시 원문, periodic=정기보고서 재무제표 API)",
    )
    period_end: Mapped[date] = mapped_column(
        nullable=False,
        comment="실적 대상 기간의 종료일(예: 2026-06-30). 기준 시간대는 한국이다",
    )
    statement_scope: Mapped[StatementScope] = mapped_column(
        SqlEnum(
            StatementScope,
            native_enum=False,
            length=20,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        comment="재무제표 범위(CFS=연결, OFS=별도). 연결을 우선하고 없을 때만 별도를 저장한다",
    )
    amount_basis: Mapped[AmountBasis] = mapped_column(
        SqlEnum(
            AmountBasis,
            native_enum=False,
            length=20,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        comment="금액의 기간 기준(period=해당 분기·반기, cumulative=사업연도 누계)",
    )
    metric: Mapped[EarningsMetric] = mapped_column(
        SqlEnum(
            EarningsMetric,
            native_enum=False,
            length=20,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        comment="지표(revenue=매출액, operating_profit=영업이익, net_income=당기순이익)",
    )
    current_amount: Mapped[Decimal] = mapped_column(
        Numeric(24, 2),
        nullable=False,
        comment="당기 금액. 원문 단위를 원 단위로 정규화한 값이며 음수와 0은 정상값이다",
    )
    prior_year_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(24, 2),
        nullable=True,
        comment="비교 가능한 전년 동기 금액(원). 원문에 없으면 NULL이고 0으로 바꾸지 않는다",
    )
    currency: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="원문이 밝힌 통화(예: KRW). 임의로 환산하지 않는다",
    )
    source_account_id: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="OpenDART 원계정 ID(예: ifrs-full_Revenue). 원문 표에서 읽은 잠정실적은 NULL이다",
    )
    source_account_name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="원문 항목명. 어느 줄에서 읽은 숫자인지 되짚을 근거다",
    )
    source_record_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("source_record.id", ondelete="RESTRICT"),
        nullable=False,
        comment="숫자를 얻은 원문 또는 재무제표 API 조회의 source_record 레코드 ID",
    )


class StockAnalystOpinion(EntityBase):
    """증권사 애널리스트의 종목별 투자의견·목표주가(KIS `invest-opinion`).

    발표일·증권사마다 한 행이다. 리포트 본문은 여기 없다 — 글은 `document`가 네이버 리서치
    출처로 갖고, 이 테이블은 숫자만 갖는다(`docs/market-thesis/6-analyst.md`).

    괴리 값은 **발표 전일 종가 대비**만 둔다. KIS가 함께 주는 조회 시점 현재가 대비 괴리
    (`stft_esdg`·`dprt`)는 매일 바뀌는 값이라 발표일 행에 섞지 않는다.
    """

    __tablename__ = "stock_analyst_opinion"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "stock_code",
            "business_date",
            "broker_name",
            name="uq_stock_analyst_opinion_natural_key",
        ),
        Index("ix_stock_analyst_opinion_source_record_id", "source_record_id"),
        table_options(
            comment="증권사 애널리스트의 종목별 투자의견·목표주가를 발표일 단위로 누적하는 테이블",
            database="default",
        ),
    )

    provider: Mapped[str] = mapped_column(Text, nullable=False, comment="데이터 제공처 식별자(kis)")
    stock_code: Mapped[str] = mapped_column(Text, nullable=False, comment="한국거래소 종목코드 6자리(예: 005930)")
    business_date: Mapped[date] = mapped_column(
        nullable=False, comment="투자의견 발표 영업일(stck_bsop_date). 기준 시간대는 한국이다"
    )
    broker_name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="투자의견을 낸 증권사(mbcr_name). KIS 표기 그대로의 약칭이다(예: 키움, 한국투자, 신한투자증권)",
    )
    opinion: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="투자의견(invt_opnn). 증권사마다 표기가 달라 BUY와 매수가 섞여 온다. 기계 판독은 opinion_code로 한다",
    )
    opinion_code: Mapped[str] = mapped_column(Text, nullable=False, comment="투자의견 구분코드(invt_opnn_cls_code)")
    previous_opinion: Mapped[str] = mapped_column(
        Text, nullable=False, comment="같은 증권사의 직전 투자의견(rgbf_invt_opnn). 표기 규칙은 opinion과 같다"
    )
    previous_opinion_code: Mapped[str] = mapped_column(
        Text, nullable=False, comment="직전 투자의견 구분코드(rgbf_invt_opnn_cls_code)"
    )
    target_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, comment="목표주가(hts_goal_prc). 원")
    previous_close: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, comment="발표 전일 종가(stck_prdy_clpr). 원"
    )
    gap_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        nullable=False,
        comment="발표 전일 종가에서 목표주가를 뺀 괴리(stck_nday_esdg). 원. 음수면 목표가가 종가보다 높다",
    )
    gap_rate: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, comment="목표주가 대비 괴리율(nday_dprt). KIS 표기 그대로의 퍼센트"
    )
    source_record_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("source_record.id", ondelete="RESTRICT"),
        nullable=False,
        comment="이 행을 마지막으로 갱신한 수집의 source_record 레코드 ID",
    )
