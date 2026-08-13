from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
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
from sqlalchemy.orm import Mapped, mapped_column

from apps.core.database import EntityBase, table_options


class IndicatorObservation(EntityBase):
    __tablename__ = "indicator_observation"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "series_id",
            "observation_date",
            name="uq_indicator_observation_natural_key",
        ),
        Index("ix_indicator_observation_source_record_id", "source_record_id"),
        table_options(
            comment="여러 제공처의 지표 관측값을 조회 가능한 형태로 정규화하고 원본과 연결하는 테이블",
            database="default",
        ),
    )

    provider: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="데이터 제공처 식별자(예: fred 또는 ecos). 같은 수집의 source_record.source와 같은 값이다",
    )
    series_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="제공처가 정의한 시계열 식별자(예: DGS10). 제공처 안에서만 고유하다",
    )
    observation_date: Mapped[date] = mapped_column(nullable=False, comment="지표 값의 기준일")
    value: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, comment="정규화한 지표 값")
    unit: Mapped[str] = mapped_column(Text, nullable=False, comment="지표 값의 단위(예: Percent)")
    source_record_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("source_record.id", ondelete="RESTRICT"),
        nullable=False,
        comment="근거가 되는 source_record 레코드 ID",
    )


class MarketCode(StrEnum):
    """휴장 캘린더를 공유하는 시장 묶음.

    `reference.Market`(`kospi`/`kosdaq`/`nyse`/`nasdaq`)과 **값 체계가 다르다.** 저쪽은 종목이
    상장된 거래소이고 이쪽은 달력이 같은 시장의 묶음이다. 이름이 비슷해도 조인 키가 아니다.

    미국을 거래소별로 나누지 않는 이유는 나스닥·뉴욕거래소·아멕스의 휴장일이 같고, KIS
    해외결제일자조회가 주는 시장별 5행의 결제일도 실측에서 모두 같았기 때문이다. NYSE도
    "All NYSE markets"로 한 벌만 고시한다.
    """

    KRX = "KRX"
    US_EQUITY = "US_EQUITY"


class SessionVerifier(StrEnum):
    """`effective_open_day`를 채운 제공처."""

    KIS = "kis"
    NYSE = "nyse"


class MarketSession(EntityBase):
    """시장별·날짜별 개장 여부와 결제일을 저장한다.

    조회하는 쪽은 `effective_open_day` 하나만 본다. 그 값이 어디서 왔는지는 `verified_by`와
    두 계보 외래키가 설명한다.

    **국내는 KIS 국내휴장일조회가, 미국은 NYSE 공식 캘린더가 판정의 주인이다.** KIS
    해외결제일자조회는 미국 행의 결제일만 채우고 판정에는 손대지 않는다. 그 API는 휴장한
    나라의 행을 아예 주지 않고 미래 날짜에는 0건으로 답해서(실측), 그것만으로는 미국 휴장일
    행이 영원히 생기지 않고 오늘 이후를 미리 알 수도 없기 때문이다.

    설계 문서는 `docs/kis-market-session-calendar.md`다.
    """

    __tablename__ = "market_session"
    __table_args__ = (
        UniqueConstraint(
            "market_code",
            "session_date",
            name="uq_market_session_natural_key",
        ),
        CheckConstraint(
            "market_code IN ('KRX', 'US_EQUITY')",
            name="ck_market_session_market_code",
        ),
        CheckConstraint(
            "verified_by IN ('kis', 'nyse')",
            name="ck_market_session_verified_by",
        ),
        Index("ix_market_session_session_date", "session_date"),
        Index("ix_market_session_source_record_id", "source_record_id"),
        Index("ix_market_session_verification_source_record_id", "verification_source_record_id"),
        table_options(
            comment="시장별·날짜별 개장 여부와 결제일을 저장하는 테이블",
            database="default",
        ),
    )

    market_code: Mapped[MarketCode] = mapped_column(
        SqlEnum(
            MarketCode,
            native_enum=False,
            length=20,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        comment="휴장 캘린더를 공유하는 시장 묶음(KRX, US_EQUITY). 상장 거래소를 뜻하는 Market enum과는 다른 체계다",
    )
    market_name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="사람이 읽는 시장 이름(예: 한국거래소)",
    )
    country_code: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="시장이 속한 국가(ISO 3166-1 alpha-2, 예: KR 또는 US)",
    )
    session_date: Mapped[date] = mapped_column(
        nullable=False,
        comment="그 시장의 현지 거래일. 시간대는 시장 현지 기준이다",
    )
    kis_weekday_code: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="국내 KIS 요일구분코드(wday_dvsn_cd). 미국 행은 NULL이다",
    )
    kis_business_day: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        comment="국내 KIS 영업일 여부(bzdy_yn). 미국 행은 NULL이다",
    )
    kis_trading_day: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        comment="국내 KIS 거래일 여부(tr_day_yn). 미국 행은 NULL이다",
    )
    kis_open_day: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        comment="국내 KIS 개장일 여부(opnd_yn). 주문 가능 여부의 원본이다. 미국 행은 NULL이다",
    )
    kis_settlement_day: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        comment="국내 KIS 결제일 여부(sttl_day_yn). 미국 행은 NULL이다",
    )
    local_settlement_date: Mapped[date | None] = mapped_column(
        nullable=True,
        comment="해외 KIS 현지결제일자(acpl_sttl_dt). 국내 행은 NULL이다",
    )
    domestic_settlement_date: Mapped[date | None] = mapped_column(
        nullable=True,
        comment="해외 KIS 국내결제일자(dmst_sttl_dt). 국내 행은 NULL이다",
    )
    effective_open_day: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        comment=(
            "소비자가 쓰는 최종 개장일 판정. 조기 폐장일은 true다. "
            "아직 판정하지 못한 날짜는 NULL이고, 이때 수집기는 시세 요청을 멈추지 않는다"
        ),
    )
    verified_by: Mapped[SessionVerifier | None] = mapped_column(
        SqlEnum(
            SessionVerifier,
            native_enum=False,
            length=20,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=True,
        comment="effective_open_day를 채운 제공처(kis, nyse)",
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="최종 판정을 확인한 시각(UTC)",
    )
    source_record_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("source_record.id", ondelete="RESTRICT"),
        nullable=False,
        comment="이 행을 만든 수집의 source_record 레코드 ID. 국내는 KIS, 미국은 NYSE 수집이다",
    )
    verification_source_record_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("source_record.id", ondelete="RESTRICT"),
        nullable=True,
        comment="같은 행을 보강한 다른 출처의 source_record 레코드 ID. 미국 행의 결제일을 채운 KIS 해외 수집이다",
    )


class QuoteBar(EntityBase):
    """지수·선물의 1분봉을 장중 알림 판단용으로 누적한다.

    `indicator_observation`과 목적이 다르다. 저쪽은 하루 한 값을 쌓아 "어제 이랬으니 오늘은
    이럴 것"을 유추하는 리포트용이고, 이 테이블은 미국 선물이 지금 빠지고 있으니 한국
    반도체가 곧 빠질 수 있다는 **실시간 알림**을 위한 것이다. 한국 정규장 시간대에는 미국
    현물 지수가 멈춰 있어 선물만이 살아 있는 신호이므로, 수집은 24시간 돈다.

    `(provider, symbol, bar_at)`이 멱등 키다. 폴링 주기보다 넓은 구간을 받아도 겹치는 봉은
    갱신으로 흡수된다. `symbol`은 제공처 안에서만 고유하므로 키에 `provider`가 함께 들어간다.
    """

    __tablename__ = "quote_bar"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "symbol",
            "bar_at",
            name="uq_quote_bar_natural_key",
        ),
        Index("ix_quote_bar_source_record_id", "source_record_id"),
        table_options(
            comment="지수·선물의 1분봉을 장중 알림 판단용으로 누적하는 테이블",
            database="default",
        ),
    )

    provider: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="데이터 제공처 식별자(예: yahoo 또는 kis). 같은 수집의 source_record.source와 같은 값이다",
    )
    symbol: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="시세 대상 식별자(예: SP500_FUT, SOX). 제공처 안에서만 고유하다",
    )
    bar_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="1분봉이 시작하는 시각(UTC). 봉은 이 시각부터 1분간의 거래를 담는다",
    )
    open: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, comment="봉 구간의 시가")
    high: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, comment="봉 구간의 고가")
    low: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, comment="봉 구간의 저가")
    close: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, comment="봉 구간의 종가")
    volume: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        comment=(
            "봉 구간의 거래량. 제공처가 주는 값을 그대로 저장한다. "
            "현물 지수처럼 거래량 개념이 없는 심볼은 제공처가 0을 실어 보내므로 0이 들어간다. "
            "즉 0은 '거래가 없었다'와 '제공처가 거래량을 주지 않는다'를 구분하지 않는다. "
            "거래량으로 판단하는 조회가 생기면 그때 심볼 종류로 갈라 읽는다"
        ),
    )
    previous_close: Mapped[Decimal] = mapped_column(
        Numeric(18, 8),
        nullable=False,
        comment=(
            "직전 정규장 종가. 알림이 쓰는 변동률의 분모다. "
            "봉마다 같은 값이 반복되지만 세션 경계 계산을 피하려고 그대로 저장한다"
        ),
    )
    contract_code: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "선물의 실제 월물 코드(예: A01609). 현물 지수와 연속 심볼은 NULL이다. "
            "월물이 바뀌면 가격에 갭이 생기는데, 이 값이 없으면 그 갭이 시장 급변인지 "
            "롤오버인지 구분할 수 없다"
        ),
    )
    source_record_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("source_record.id", ondelete="RESTRICT"),
        nullable=False,
        comment="근거가 되는 source_record 레코드 ID",
    )


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

    설계 문서는 `docs/dart-disclosure-earnings.md`다.
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


class MovementMarket(StrEnum):
    """상승·보합·하락 분포를 주는 지수.

    값이 `quote_bar.symbol`(`DomesticIndex`)과 글자 그대로 같다. 같은 값을 다른 이름으로
    부르면 "코스피 지수 봉"과 "코스피 종목 분포"를 잇는 조회가 대응표를 들고 다녀야 한다.

    `quote_bar.symbol`이 열린 `Text`인 것과 달리 여기는 Enum이다. 저쪽은 제공처마다 값
    집합이 달라지는 열린 식별자이고 이쪽은 분포를 고시하는 지수 둘로 닫혀 있다.
    코스피200은 분포 대상이 아니다.
    """

    KOSPI = "KOSPI"
    KOSDAQ = "KOSDAQ"


class MarketMovementSnapshot(EntityBase):
    """코스피·코스닥의 상승·보합·하락 종목 수를 분 단위로 누적한다.

    **지수가 올랐다는 사실만으로는 소수 대형주가 끌어올린 장인지 시장 전반이 오른 장인지
    알 수 없다.** 그 구분이 이 테이블의 존재 이유다. `quote_bar`가 지수 값을 갖고 여기는
    그 값을 만든 종목들의 분포를 갖는다.

    전 종목을 순회해 계산하지 않는다. KIS 지수 API가 이미 다섯 종목 수를 준다.

    **다섯 값이 모두 0인 응답은 저장하지 않는다.** 장 시작 전과 마감 후에는 종목 수가 0으로
    리셋되는데(실측), 장중에는 상승·보합·하락의 합이 전 종목이라 all-zero가 나올 수 없다.
    그래서 all-zero는 분포가 아니라 "장 밖"이라는 뜻이다. 그 판정은 수집기가 한다.

    **상한가는 상승에 포함된다**(실측: 상한가가 3→4로 늘어난 순간에 상승+보합+하락 합이
    그대로였다). 그래서 전체 종목 수는 `rising + unchanged + falling`이고 다섯 값을 더하면
    상·하한가가 이중 계산된다. 그래도 다섯 값을 날것으로 보존하고 비율이나 3분류를 저장하지
    않는다. 합계가 전 종목 수라는 제약도 걸지 않는다. 거래정지로 셋 어디에도 안 들어가는
    종목이 생길 수 있고, 그때 제약이 수집을 막는 것이 값을 잃는 것보다 나쁘다.

    설계 문서는 `docs/kis-market-movement-distribution.md`다.
    """

    __tablename__ = "market_movement_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "symbol",
            "observed_at",
            name="uq_market_movement_snapshot_natural_key",
        ),
        CheckConstraint(
            "symbol IN ('KOSPI', 'KOSDAQ')",
            name="ck_market_movement_snapshot_symbol",
        ),
        CheckConstraint(
            "upper_limit_count >= 0 AND rising_count >= 0 AND unchanged_count >= 0 "
            "AND falling_count >= 0 AND lower_limit_count >= 0",
            name="ck_market_movement_snapshot_counts_not_negative",
        ),
        Index("ix_market_movement_snapshot_source_record_id", "source_record_id"),
        table_options(
            comment="코스피·코스닥의 상승·보합·하락 종목 수를 분 단위로 누적하는 테이블",
            database="default",
        ),
    )

    provider: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="데이터 제공처 식별자(kis). 같은 수집의 source_record.source와 같은 값이다",
    )
    symbol: Mapped[MovementMarket] = mapped_column(
        SqlEnum(
            MovementMarket,
            native_enum=False,
            length=20,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        comment="분포를 고시한 지수(KOSPI, KOSDAQ). quote_bar.symbol과 같은 값이다",
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment=(
            "관측이 속한 1분의 시작 시각(UTC). REST는 응답을 받은 시각을 분 단위로 절삭한 값이라 "
            "제공처가 준 원천 시각이 아니다. 과거 분포를 복구하는 백필 값으로 쓰지 않는다"
        ),
    )
    upper_limit_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="상한가 종목 수. 상승 종목 수 안에 포함된 부분집합이다(실측). 강조 표시용으로 따로 보존한다",
    )
    rising_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="상승 종목 수. 상한가를 포함한다. 보합·하락과 더하면 그날 거래 종목 수가 된다",
    )
    unchanged_count: Mapped[int] = mapped_column(Integer, nullable=False, comment="보합 종목 수")
    falling_count: Mapped[int] = mapped_column(Integer, nullable=False, comment="하락 종목 수")
    lower_limit_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="하한가 종목 수. 하락 종목 수에 포함되는지는 아직 확인하지 못했다(관측 내내 0이었다)",
    )
    source_record_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("source_record.id", ondelete="RESTRICT"),
        nullable=False,
        comment="이 행을 마지막으로 갱신한 수집의 source_record 레코드 ID",
    )
