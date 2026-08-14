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


class KrxMarket(StrEnum):
    """국내 시장 단위 값이 쓰는 시장 구분.

    종목이 아니라 **시장 전체**를 가리키는 행에 붙는다. 지금은 상승·보합·하락 분포와 시장
    대차 잔고가 함께 쓴다. 둘이 같은 값 집합이라 Enum을 하나만 둔다.

    값이 `quote_bar.symbol`(`DomesticIndex`)과 글자 그대로 같다. 같은 값을 다른 이름으로
    부르면 "코스피 지수 봉"과 "코스피 시장 값"을 잇는 조회가 대응표를 들고 다녀야 한다.

    `quote_bar.symbol`이 열린 `Text`인 것과 달리 여기는 Enum이다. 저쪽은 제공처마다 값
    집합이 달라지는 열린 식별자이고 이쪽은 두 시장으로 닫혀 있다. 코스피200은 코스피의
    부분집합이라 시장이 아니다.
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
    symbol: Mapped[KrxMarket] = mapped_column(
        SqlEnum(
            KrxMarket,
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


class KrxStockCreditBalanceDaily(EntityBase):
    """종목별 신용잔고 일별추이(KRX 주 경로).

    **날짜가 둘이고 뜻이 다르다.** `trade_date`(`deal_date`)가 그 값이 만들어진 거래일이고
    `settlement_date`(`stlm_date`)가 결제일이다. 실측에서 결제 시차가 2영업일이었다. 결제일만
    저장하면 사용자가 보는 추이가 실제 거래일에서 이틀씩 밀린다.

    금액과 비율은 KIS 표기를 그대로 둔다. 수집기에서 억원이나 소수 비율로 바꾸지 않는다.
    """

    __tablename__ = "krx_stock_credit_balance_daily"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "stock_code",
            "trade_date",
            name="uq_krx_stock_credit_balance_daily_natural_key",
        ),
        Index("ix_krx_stock_credit_balance_daily_source_record_id", "source_record_id"),
        table_options(
            comment="종목별 신용잔고(융자·신용대주) 일별추이를 거래일 기준으로 누적하는 테이블",
            database="default",
        ),
    )

    provider: Mapped[str] = mapped_column(
        Text, nullable=False, comment="데이터 제공처 식별자(kis). 같은 수집의 source_record.source와 같은 값이다"
    )
    stock_code: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="한국거래소 종목코드 6자리(예: 005930). disclosure_event.stock_code와 같은 체계다",
    )
    trade_date: Mapped[date] = mapped_column(
        nullable=False, comment="값이 만들어진 거래일(deal_date). 기준 시간대는 한국이다"
    )
    settlement_date: Mapped[date] = mapped_column(
        nullable=False, comment="결제일(stlm_date). 거래일보다 통상 2영업일 뒤다(실측)"
    )
    close_price: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, comment="그 거래일 종가(stck_prpr). 원"
    )
    accumulated_volume: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="그 거래일 누적 거래량(acml_vol). 주"
    )
    loan_new_quantity: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="융자 신규 수량(whol_loan_new_stcn). 주"
    )
    loan_repayment_quantity: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="융자 상환 수량(whol_loan_rdmp_stcn). 주"
    )
    loan_balance_quantity: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="융자 잔고 수량(whol_loan_rmnd_stcn). 주"
    )
    loan_new_amount: Mapped[Decimal] = mapped_column(
        Numeric(24, 2), nullable=False, comment="융자 신규 금액(whol_loan_new_amt). KIS 표기 그대로 저장한다"
    )
    loan_repayment_amount: Mapped[Decimal] = mapped_column(
        Numeric(24, 2), nullable=False, comment="융자 상환 금액(whol_loan_rdmp_amt). KIS 표기 그대로 저장한다"
    )
    loan_balance_amount: Mapped[Decimal] = mapped_column(
        Numeric(24, 2), nullable=False, comment="융자 잔고 금액(whol_loan_rmnd_amt). KIS 표기 그대로 저장한다"
    )
    loan_balance_rate: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, comment="융자 잔고 비율(whol_loan_rmnd_rate). KIS 표기 그대로의 퍼센트"
    )
    loan_supply_rate: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, comment="융자 공여율(whol_loan_gvrt). KIS 표기 그대로의 퍼센트"
    )
    short_loan_new_quantity: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="신용대주 신규 수량(whol_stln_new_stcn). 주"
    )
    short_loan_repayment_quantity: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="신용대주 상환 수량(whol_stln_rdmp_stcn). 주"
    )
    short_loan_balance_quantity: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="신용대주 잔고 수량(whol_stln_rmnd_stcn). 주"
    )
    short_loan_new_amount: Mapped[Decimal] = mapped_column(
        Numeric(24, 2), nullable=False, comment="신용대주 신규 금액(whol_stln_new_amt). KIS 표기 그대로 저장한다"
    )
    short_loan_repayment_amount: Mapped[Decimal] = mapped_column(
        Numeric(24, 2), nullable=False, comment="신용대주 상환 금액(whol_stln_rdmp_amt). KIS 표기 그대로 저장한다"
    )
    short_loan_balance_amount: Mapped[Decimal] = mapped_column(
        Numeric(24, 2), nullable=False, comment="신용대주 잔고 금액(whol_stln_rmnd_amt). KIS 표기 그대로 저장한다"
    )
    short_loan_balance_rate: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, comment="신용대주 잔고 비율(whol_stln_rmnd_rate). KIS 표기 그대로의 퍼센트"
    )
    short_loan_supply_rate: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, comment="신용대주 공여율(whol_stln_gvrt). KIS 표기 그대로의 퍼센트"
    )
    source_record_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("source_record.id", ondelete="RESTRICT"),
        nullable=False,
        comment="이 행을 마지막으로 갱신한 수집의 source_record 레코드 ID",
    )


class KrxCreditBalanceRankingDaily(EntityBase):
    """신용잔고 상위 종목 일별 스냅샷(KRX 주 경로).

    **과거 기준일 입력이 없는 API다.** 배포 전 과거 순위를 백필할 수 없고 운영 시작일부터
    매일 스냅샷이 쌓인다.

    **`standard_date`가 최신이고 `comparison_date`가 그보다 과거다.** 응답의 `stnd_date2`가
    기준일, `stnd_date1`이 비교일이다. 초판 설계는 이 둘을 반대로 적었다(실측으로 정정).

    응답 건수를 상수로 박지 않는다. 실측이 100건이었을 뿐 제공처가 바꿀 수 있다.
    """

    __tablename__ = "krx_credit_balance_ranking_daily"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "standard_date",
            "universe_code",
            "sort_code",
            "period_days",
            "rank",
            name="uq_krx_credit_balance_ranking_daily_natural_key",
        ),
        CheckConstraint("rank >= 1", name="ck_krx_credit_balance_ranking_daily_rank"),
        Index("ix_krx_credit_balance_ranking_daily_stock_code", "stock_code", "standard_date"),
        Index("ix_krx_credit_balance_ranking_daily_source_record_id", "source_record_id"),
        table_options(
            comment="융자잔고금액 상위 종목의 일별 순위 스냅샷을 저장하는 테이블",
            database="default",
        ),
    )

    provider: Mapped[str] = mapped_column(Text, nullable=False, comment="데이터 제공처 식별자(kis)")
    standard_date: Mapped[date] = mapped_column(
        nullable=False, comment="순위 기준일(응답 stnd_date2). 둘 중 최신 날짜다"
    )
    comparison_date: Mapped[date] = mapped_column(
        nullable=False, comment="증가율 비교일(응답 stnd_date1). 기준일보다 period_days 영업일 앞이다"
    )
    universe_code: Mapped[str] = mapped_column(
        Text, nullable=False, comment="조회 대상 코드(FID_INPUT_ISCD). 0000은 전체다"
    )
    sort_code: Mapped[str] = mapped_column(
        Text, nullable=False, comment="정렬 코드(FID_RANK_SORT_CLS_CODE). 2는 융자잔고금액 상위다"
    )
    period_days: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="증가율 비교 기간(FID_OPTION). 영업일 수다"
    )
    rank: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="순위. 응답에 순번 필드가 없어 배열 순서로 1부터 매긴다(실측: 순번 필드 없음)",
    )
    stock_code: Mapped[str] = mapped_column(Text, nullable=False, comment="한국거래소 종목코드 6자리(mksc_shrn_iscd)")
    stock_name: Mapped[str] = mapped_column(Text, nullable=False, comment="종목명 원문(hts_kor_isnm)")
    close_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, comment="현재가(stck_prpr). 원")
    accumulated_volume: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="누적 거래량(acml_vol). 주")
    loan_balance_quantity: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="융자 잔고 수량(whol_loan_rmnd_stcn). 주"
    )
    loan_balance_amount: Mapped[Decimal] = mapped_column(
        Numeric(24, 2), nullable=False, comment="융자 잔고 금액(whol_loan_rmnd_amt). 정렬 기준 값이다"
    )
    loan_balance_rate: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, comment="융자 잔고 비율(whol_loan_rmnd_rate). KIS 표기 그대로의 퍼센트"
    )
    short_loan_balance_quantity: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="신용대주 잔고 수량(whol_stln_rmnd_stcn). 주"
    )
    short_loan_balance_amount: Mapped[Decimal] = mapped_column(
        Numeric(24, 2), nullable=False, comment="신용대주 잔고 금액(whol_stln_rmnd_amt)"
    )
    short_loan_balance_rate: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, comment="신용대주 잔고 비율(whol_stln_rmnd_rate). KIS 표기 그대로의 퍼센트"
    )
    loan_balance_growth_rate: Mapped[Decimal] = mapped_column(
        Numeric(12, 4),
        nullable=False,
        comment="비교일 대비 융자잔고 증가율(nday_vrss_loan_rmnd_inrt). 변화량이 아니라 증가율이다",
    )
    short_loan_balance_growth_rate: Mapped[Decimal] = mapped_column(
        Numeric(12, 4),
        nullable=False,
        comment="비교일 대비 신용대주잔고 증가율(nday_vrss_stln_rmnd_inrt). 변화량이 아니라 증가율이다",
    )
    source_record_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("source_record.id", ondelete="RESTRICT"),
        nullable=False,
        comment="이 행을 마지막으로 갱신한 수집의 source_record 레코드 ID",
    )


class KrxMarketFundsDaily(EntityBase):
    """국내 증시자금 종합 일별(KRX 주 경로).

    고객예탁금·신용융자잔고·펀드 설정액처럼 **시장에 들어와 있는 돈**을 담는다. 종목이 아니라
    시장 단위라 자연키에 종목이 없다.

    **한 번 호출에 100영업일이 온다**(실측). 요청 날짜는 종료일이고 그 전날부터 과거로 채워진다.
    그래서 하루 한 번 부르면 5개월치를 매번 덮으며, 되돌아볼 일수를 따로 줄 이유가 없다.

    **응답의 `prdy_ctrt`를 저장하지 않는다.** 실측에서 지수 6345.53에 전일대비 45.87인데 그
    값이 100.73이었다. 등락률(0.73%)이 아니다. 의미가 확인되기 전에는 넣지 않고, 필요하면
    지수와 전일대비로 조회에서 계산한다.

    금액 단위는 컬럼마다 다를 수 있다. KIS 표기를 그대로 저장하고 환산하지 않는다.
    """

    __tablename__ = "krx_market_funds_daily"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "business_date",
            name="uq_krx_market_funds_daily_natural_key",
        ),
        Index("ix_krx_market_funds_daily_source_record_id", "source_record_id"),
        table_options(
            comment="고객예탁금·신용융자·펀드 등 국내 증시자금 종합을 영업일 단위로 누적하는 테이블",
            database="default",
        ),
    )

    provider: Mapped[str] = mapped_column(Text, nullable=False, comment="데이터 제공처 식별자(kis)")
    business_date: Mapped[date] = mapped_column(
        nullable=False,
        comment="응답이 준 영업일(bsop_date). 요청일이나 수집일을 대신 넣지 않는다",
    )
    index_close: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, comment="그날 시장지수(bstp_nmix_prpr)"
    )
    index_change: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, comment="지수 전일대비(bstp_nmix_prdy_vrss)"
    )
    market_capitalization: Mapped[Decimal] = mapped_column(
        Numeric(24, 2), nullable=False, comment="시가총액(hts_avls). 포털 표기는 백만원이며 환산하지 않는다"
    )
    customer_deposit: Mapped[Decimal] = mapped_column(
        Numeric(24, 2), nullable=False, comment="고객예탁금(cust_dpmn_amt). 포털 표기는 억원이며 환산하지 않는다"
    )
    customer_deposit_change: Mapped[Decimal] = mapped_column(
        Numeric(24, 2), nullable=False, comment="고객예탁금 전일대비(cust_dpmn_amt_prdy_vrss). 음수는 정상값이다"
    )
    turnover_ratio: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, comment="금액 회전율(amt_tnrt). KIS 표기 그대로의 퍼센트"
    )
    unsettled_amount: Mapped[Decimal] = mapped_column(
        Numeric(24, 2), nullable=False, comment="미수금(uncl_amt). 포털 표기는 억원이며 환산하지 않는다"
    )
    credit_loan_balance: Mapped[Decimal] = mapped_column(
        Numeric(24, 2), nullable=False, comment="신용융자 잔고(crdt_loan_rmnd). 포털 표기는 억원이며 환산하지 않는다"
    )
    futures_margin_amount: Mapped[Decimal] = mapped_column(
        Numeric(24, 2), nullable=False, comment="선물 관련 자금(futs_tfam_amt)"
    )
    equity_fund_amount: Mapped[Decimal] = mapped_column(
        Numeric(24, 2), nullable=False, comment="주식형 펀드 설정액(sttp_amt)"
    )
    mixed_fund_amount: Mapped[Decimal] = mapped_column(
        Numeric(24, 2), nullable=False, comment="혼합형 펀드 설정액(mxtp_amt)"
    )
    bond_fund_amount: Mapped[Decimal] = mapped_column(
        Numeric(24, 2), nullable=False, comment="채권형 펀드 설정액(bntp_amt)"
    )
    mmf_amount: Mapped[Decimal] = mapped_column(Numeric(24, 2), nullable=False, comment="MMF 설정액(mmf_amt)")
    securities_lending_amount: Mapped[Decimal] = mapped_column(
        Numeric(24, 2), nullable=False, comment="대차 금액(secu_lend_amt)"
    )
    source_record_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("source_record.id", ondelete="RESTRICT"),
        nullable=False,
        comment="이 행을 마지막으로 갱신한 수집의 source_record 레코드 ID",
    )


class KrxStockShortSaleDaily(EntityBase):
    """종목별 공매도 일별추이(KRX 주 경로).

    당일 행이 와도 장중 확정치로 쓰지 않는다. 다음 영업일 아침에 최근 며칠을 다시 받아
    영업일별 행을 갱신한다.

    비중 값은 KIS 표기 그대로의 퍼센트다. 소수 비율로 바꾸지 않는다.
    """

    __tablename__ = "krx_stock_short_sale_daily"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "stock_code",
            "business_date",
            name="uq_krx_stock_short_sale_daily_natural_key",
        ),
        Index("ix_krx_stock_short_sale_daily_source_record_id", "source_record_id"),
        table_options(
            comment="종목별 공매도 체결수량·거래대금과 그 비중을 영업일 단위로 누적하는 테이블",
            database="default",
        ),
    )

    provider: Mapped[str] = mapped_column(Text, nullable=False, comment="데이터 제공처 식별자(kis)")
    stock_code: Mapped[str] = mapped_column(Text, nullable=False, comment="한국거래소 종목코드 6자리(예: 005930)")
    business_date: Mapped[date] = mapped_column(
        nullable=False, comment="영업일(stck_bsop_date). 기준 시간대는 한국이다"
    )
    close_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, comment="그날 종가(stck_clpr). 원")
    accumulated_volume: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="그날 누적 거래량(acml_vol). 주"
    )
    short_sale_quantity: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="그날 공매도 체결수량(ssts_cntg_qty). 주"
    )
    short_sale_volume_ratio: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, comment="공매도 거래량 비중(ssts_vol_rlim). KIS 표기 그대로의 퍼센트"
    )
    accumulated_short_sale_quantity: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="누적 공매도 수량(acml_ssts_cntg_qty). 주"
    )
    accumulated_short_sale_volume_ratio: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, comment="누적 공매도 거래량 비중(acml_ssts_cntg_qty_rlim). 퍼센트"
    )
    short_sale_amount: Mapped[Decimal] = mapped_column(
        Numeric(24, 2),
        nullable=False,
        comment="그날 공매도 거래대금(ssts_tr_pbmn). **원 단위다**(실측: 수량×종가와 거의 같다)",
    )
    short_sale_amount_ratio: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, comment="공매도 거래대금 비중(ssts_tr_pbmn_rlim). 퍼센트"
    )
    accumulated_short_sale_amount: Mapped[Decimal] = mapped_column(
        Numeric(24, 2), nullable=False, comment="누적 공매도 거래대금(acml_ssts_tr_pbmn). 원"
    )
    accumulated_short_sale_amount_ratio: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, comment="누적 공매도 거래대금 비중(acml_ssts_tr_pbmn_rlim). 퍼센트"
    )
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(24, 2), nullable=False, comment="그날 전체 거래대금(acml_tr_pbmn). 원"
    )
    short_sale_average_price: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, comment="공매도 평균가(avrg_prc). 원"
    )
    source_record_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("source_record.id", ondelete="RESTRICT"),
        nullable=False,
        comment="이 행을 마지막으로 갱신한 수집의 source_record 레코드 ID",
    )


class KrxStockSecuritiesLendingDaily(EntityBase):
    """종목별 일별 대차거래추이(KRX 주 경로).

    대차 잔고는 공매도의 재고에 해당해서 공매도 수량과 함께 봐야 뜻이 산다.

    **이 API는 `MRKT_DIV_CLS_CODE=3`으로만 부른다.** `1`은 시장 전체를 돌려준다(실측:
    `1`의 종가가 코스피 지수 6579.04였다). `1`로 부르면 종목 코드를 보냈는데도 시장 전체
    숫자가 이 테이블에 들어간다.
    """

    __tablename__ = "krx_stock_securities_lending_daily"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "stock_code",
            "business_date",
            name="uq_krx_stock_securities_lending_daily_natural_key",
        ),
        Index("ix_krx_stock_securities_lending_daily_source_record_id", "source_record_id"),
        table_options(
            comment="종목별 대차거래 신규·상환·잔고를 영업일 단위로 누적하는 테이블",
            database="default",
        ),
    )

    provider: Mapped[str] = mapped_column(Text, nullable=False, comment="데이터 제공처 식별자(kis)")
    stock_code: Mapped[str] = mapped_column(Text, nullable=False, comment="한국거래소 종목코드 6자리(예: 005930)")
    business_date: Mapped[date] = mapped_column(nullable=False, comment="영업일(bsop_date). 기준 시간대는 한국이다")
    close_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, comment="그날 종가(stck_prpr). 원")
    price_change: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, comment="전일대비 가격(prdy_vrss). 음수는 정상값이다"
    )
    accumulated_volume: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="그날 누적 거래량(acml_vol). 주"
    )
    new_quantity: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="대차 신규 체결 수량(new_stcn). 주")
    repayment_quantity: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="대차 상환 수량(rdmp_stcn). 주")
    balance_change_quantity: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="전일대비 잔고 증감 수량(prdy_rmnd_vrss). 음수는 정상값이다"
    )
    balance_quantity: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="대차 잔고 수량(rmnd_stcn). 주")
    balance_amount: Mapped[Decimal] = mapped_column(
        Numeric(24, 2),
        nullable=False,
        comment="대차 잔고 금액(rmnd_amt). **백만원 단위다**(실측: 잔고수량×종가의 1/1,000,000)",
    )
    source_record_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("source_record.id", ondelete="RESTRICT"),
        nullable=False,
        comment="이 행을 마지막으로 갱신한 수집의 source_record 레코드 ID",
    )


class KrxMarketSecuritiesLendingDaily(EntityBase):
    """코스피·코스닥 **시장 전체**의 대차거래 일별추이.

    종목별 대차(`krx_stock_securities_lending_daily`)가 삼성전자·SK하이닉스 둘만 보는 것과
    달리 여기는 시장 잔고 전체다. 대차 잔고는 공매도의 재고라, 시장 단위로 쌓이고 있는지
    풀리고 있는지가 종목 하나보다 먼저 보인다.

    같은 API의 조회 분류만 바꿔 얻는다. 실측에서 `1`이 코스피(잔고 1,619,264,288주),
    `2`가 코스닥(1,444,553,429주)이었다.

    **합계는 저장하지 않는다.** 조회 분류 `5`가 전체를 주는데 5영업일 내내 코스피와 코스닥의
    정확한 합이었다. 유도되는 값을 한 벌 더 두면 둘이 어긋날 때 어느 쪽이 맞는지 알 수 없다.
    """

    __tablename__ = "krx_market_securities_lending_daily"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "market_code",
            "business_date",
            name="uq_krx_market_securities_lending_daily_natural_key",
        ),
        CheckConstraint(
            "market_code IN ('KOSPI', 'KOSDAQ')",
            name="ck_krx_market_securities_lending_daily_market_code",
        ),
        Index("ix_krx_market_securities_lending_daily_source_record_id", "source_record_id"),
        table_options(
            comment="코스피·코스닥 시장 전체의 대차거래 신규·상환·잔고를 영업일 단위로 누적하는 테이블",
            database="default",
        ),
    )

    provider: Mapped[str] = mapped_column(Text, nullable=False, comment="데이터 제공처 식별자(kis)")
    market_code: Mapped[KrxMarket] = mapped_column(
        SqlEnum(
            KrxMarket,
            native_enum=False,
            length=20,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        comment="시장 구분(KOSPI, KOSDAQ). market_movement_snapshot.symbol과 같은 값 집합이다",
    )
    business_date: Mapped[date] = mapped_column(nullable=False, comment="영업일(bsop_date). 기준 시간대는 한국이다")
    index_close: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        nullable=False,
        comment="그날 시장지수 종가(stck_prpr). 종목 조회에서는 주가지만 시장 조회에서는 지수다",
    )
    index_change: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, comment="지수 전일대비(prdy_vrss). 음수는 정상값이다"
    )
    accumulated_volume: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="그날 시장 전체 거래량(acml_vol). 주"
    )
    new_quantity: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="대차 신규 체결 수량(new_stcn). 주")
    repayment_quantity: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="대차 상환 수량(rdmp_stcn). 주")
    balance_change_quantity: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="전일대비 잔고 증감 수량(prdy_rmnd_vrss). 음수는 정상값이다"
    )
    balance_quantity: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="시장 전체 대차 잔고 수량(rmnd_stcn). 주"
    )
    balance_amount: Mapped[Decimal] = mapped_column(
        Numeric(24, 2),
        nullable=False,
        comment="시장 전체 대차 잔고 금액(rmnd_amt). **백만원 단위다**(종목 대차와 같은 표기)",
    )
    source_record_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("source_record.id", ondelete="RESTRICT"),
        nullable=False,
        comment="이 행을 마지막으로 갱신한 수집의 source_record 레코드 ID",
    )


class StockInvestorEstimateSnapshot(EntityBase):
    """종목별 외국인·기관 **추정** 순매수.

    **확정치가 아니다.** KIS가 장중에 집계해 하루 몇 차례 갱신하는 값이라 이름에 `estimate`를
    넣었다. 화면에도 `추정`으로 표시하고 확정 수급과 합치지 않는다.

    **한 번 조회에 여러 행이 온다.** 갱신 슬롯(`bsop_hour_gb`)마다 한 행이고 장이 진행되면
    행이 늘어난다(실측: 10:44에 두 행). 그래서 슬롯이 자연키에 들어간다. 수집 시각을 키로
    쓰면 한 응답의 행들이 같은 분에 몰려 마지막 하나만 남는다.

    **슬롯 코드를 시각으로 환산하지 않는다.** 공식 예제가 갱신 시각이 변동될 수 있다고
    밝히고 있어, 우리가 표를 만들면 그 표가 틀리는 날 조용히 어긋난다.
    """

    __tablename__ = "stock_investor_estimate_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "stock_code",
            "business_date",
            "source_time_code",
            name="uq_stock_investor_estimate_snapshot_natural_key",
        ),
        Index("ix_stock_investor_estimate_snapshot_source_record_id", "source_record_id"),
        table_options(
            comment="종목별 외국인·기관 추정 순매수를 갱신 슬롯 단위로 누적하는 테이블",
            database="default",
        ),
    )

    provider: Mapped[str] = mapped_column(Text, nullable=False, comment="데이터 제공처 식별자(kis)")
    stock_code: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="한국거래소 종목코드 6자리(예: 005930). disclosure_event.stock_code와 같은 체계다",
    )
    business_date: Mapped[date] = mapped_column(
        nullable=False, comment="이 값이 속한 거래일(KST). 응답에 날짜가 없어 수집 시각의 KST 날짜를 쓴다"
    )
    source_time_code: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="응답의 갱신 슬롯 코드(bsop_hour_gb). 시각이 아니라 코드이며 환산하지 않는다",
    )
    foreign_net_buy_qty: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="외국인 추정 순매수 수량(frgn_fake_ntby_qty). 음수는 정상값이다"
    )
    institution_net_buy_qty: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="기관 추정 순매수 수량(orgn_fake_ntby_qty). 음수는 정상값이다"
    )
    total_net_buy_qty: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        comment="합계 추정 순매수 수량(sum_fake_ntby_qty). 외국인+기관과 다르면 수집기가 실패시킨다",
    )
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="이 슬롯 값을 받은 시각(UTC). 자연키가 아니라 값이며 재수집하면 갱신된다",
    )
    source_record_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("source_record.id", ondelete="RESTRICT"),
        nullable=False,
        comment="이 행을 마지막으로 갱신한 수집의 source_record 레코드 ID",
    )


class MarketInvestorFlowSnapshot(EntityBase):
    """시장별 투자자 누적 매매동향.

    **한 응답에 12개 투자자 분류가 온다.** 상위 셋(외국인·기관계·개인)은 매도·매수·순매수·대금을
    모두 담고, 기관 세부와 기타 분류는 **순매수 수량만** 담는다. 그쪽에서 필요한 것은 방향이고
    (투신이 사는지 연기금이 파는지), 대금은 배율이 미확정이라 지금 넣어도 읽을 수 없다. 같은
    응답에 이미 있으므로 필요해지면 재호출 없이 컬럼만 늘린다.

    두 항등식이 실측으로 성립한다. 수집기가 이것을 검증한다.

    ```text
    기관계 = 금융투자 + 투자신탁 + 사모펀드 + 은행 + 보험 + 종금 + 기금
    개인 + 외국인 + 기관계 + 기타법인 + 기타단체 = 0
    ```

    **접미사가 분류마다 다르다.** 사모펀드·기타법인·기타단체만 `_ntby_vol`이고 나머지는
    `_ntby_qty`다. 한 벌로 조립하면 세 분류가 조용히 0이 된다.

    **누적값이라 델타를 저장하지 않는다.** 5분 변화량은 조회에서 `lag()`로 계산한다. 재수집과
    누락이 있는 환경에서 수집기가 델타를 저장하면 복구가 더 어렵다.

    **수량과 대금의 배율이 종목 추정 API와 다르다.** 실측에서 총매도 대금을 수량으로 나누면
    2.5~3.4가 나왔다. 주·원 단위라면 평균단가가 3원이라는 뜻이라 그럴 수 없다. 정확한 배율은
    확정하지 못했으므로 KIS 표기를 그대로 저장하고 화면 축에 단위를 붙이지 않는다.
    """

    __tablename__ = "market_investor_flow_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "market_code",
            "observed_at",
            name="uq_market_investor_flow_snapshot_natural_key",
        ),
        CheckConstraint(
            "market_code IN ('KOSPI', 'KOSDAQ')",
            name="ck_market_investor_flow_snapshot_market_code",
        ),
        Index("ix_market_investor_flow_snapshot_source_record_id", "source_record_id"),
        table_options(
            comment="시장별 외국인·기관·개인의 장중 누적 매매동향을 분 단위로 누적하는 테이블",
            database="default",
        ),
    )

    provider: Mapped[str] = mapped_column(Text, nullable=False, comment="데이터 제공처 식별자(kis)")
    market_code: Mapped[KrxMarket] = mapped_column(
        SqlEnum(
            KrxMarket,
            native_enum=False,
            length=20,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        comment="시장 구분(KOSPI, KOSDAQ). 코스닥 조회 코드는 아직 확인하지 못해 KOSPI만 채워진다",
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="관측이 속한 1분의 시작 시각(UTC). 응답에 원천 시각이 없어 수집 시각을 절삭한 값이다",
    )
    foreign_sell_qty: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="외국인 누적 매도 수량(frgn_seln_vol). 단위 미확정이라 환산하지 않는다"
    )
    foreign_buy_qty: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="외국인 누적 매수 수량(frgn_shnu_vol)"
    )
    foreign_net_buy_qty: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="외국인 순매수 수량(frgn_ntby_qty). 매수-매도와 일치하는지 검증한다"
    )
    foreign_net_buy_amount: Mapped[Decimal] = mapped_column(
        Numeric(24, 2), nullable=False, comment="외국인 순매수 대금(frgn_ntby_tr_pbmn). 단위 미확정"
    )
    institution_sell_qty: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="기관 누적 매도 수량(orgn_seln_vol)"
    )
    institution_buy_qty: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="기관 누적 매수 수량(orgn_shnu_vol)"
    )
    institution_net_buy_qty: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="기관 순매수 수량(orgn_ntby_qty)"
    )
    institution_net_buy_amount: Mapped[Decimal] = mapped_column(
        Numeric(24, 2), nullable=False, comment="기관 순매수 대금(orgn_ntby_tr_pbmn). 단위 미확정"
    )
    individual_sell_qty: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="개인 누적 매도 수량(prsn_seln_vol)"
    )
    individual_buy_qty: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="개인 누적 매수 수량(prsn_shnu_vol)"
    )
    individual_net_buy_qty: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="개인 순매수 수량(prsn_ntby_qty)"
    )
    individual_net_buy_amount: Mapped[Decimal] = mapped_column(
        Numeric(24, 2), nullable=False, comment="개인 순매수 대금(prsn_ntby_tr_pbmn). 단위 미확정"
    )
    securities_net_buy_qty: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="금융투자 순매수 수량(scrt_ntby_qty). 기관계의 부분집합이다"
    )
    investment_trust_net_buy_qty: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="투자신탁 순매수 수량(ivtr_ntby_qty). 기관계의 부분집합이다"
    )
    private_equity_net_buy_qty: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        comment="사모펀드 순매수 수량(pe_fund_ntby_vol). 이 분류만 접미사가 _vol이다",
    )
    bank_net_buy_qty: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="은행 순매수 수량(bank_ntby_qty). 기관계의 부분집합이다"
    )
    insurance_net_buy_qty: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="보험 순매수 수량(insu_ntby_qty). 기관계의 부분집합이다"
    )
    merchant_bank_net_buy_qty: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="종금 순매수 수량(mrbn_ntby_qty). 기관계의 부분집합이다"
    )
    pension_fund_net_buy_qty: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="기금 순매수 수량(fund_ntby_qty). 기관계의 부분집합이다"
    )
    other_corporation_net_buy_qty: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        comment="기타법인 순매수 수량(etc_corp_ntby_vol). 기관계 밖이며 접미사가 _vol이다",
    )
    other_organization_net_buy_qty: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        comment="기타단체 순매수 수량(etc_orgt_ntby_vol). 기관계 밖이며 접미사가 _vol이다",
    )
    source_record_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("source_record.id", ondelete="RESTRICT"),
        nullable=False,
        comment="이 행을 마지막으로 갱신한 수집의 source_record 레코드 ID",
    )


class StockInvestorTradeDaily(EntityBase):
    """종목별 투자자 매매동향 확정 일별값.

    `StockInvestorEstimateSnapshot`이 장중 **추정치**라면 이쪽은 장 마감 뒤의 **확정값**이다.
    둘을 합치거나 같은 축에 그리지 않는다. 추정은 하루 다섯 회차뿐이고 개인이 없지만, 여기는
    12개 분류가 전부 있고 외국인이 등록·미등록으로 갈린다.

    네 항등식이 실측으로 정확히 성립한다. 수집기가 이것을 검증한다.

    ```text
    외국인 = 외국인등록 + 외국인미등록
    기관계 = 금융투자 + 투자신탁 + 사모펀드 + 은행 + 보험 + 종금 + 기금
    기타   = 기타법인 + 기타단체
    개인 + 외국인 + 기관계 + 기타 = 0
    ```

    **단위는 이 API에서 확정됐다.** 수량은 주, 투자자별 대금은 백만원이다. 실측에서
    `frgn_seln_tr_pbmn / frgn_seln_vol × 1e6`이 271,200원으로 그날 VWAP 271,093원과 맞았다.
    장중 API(`market_investor_flow_snapshot`)의 배율은 여전히 미확정이며 이 값과 다르다.

    **같은 응답 안에서 대금 단위가 섞인다.** `accumulated_trade_amount`(`acml_tr_pbmn`)만
    **원**이고 투자자별 대금은 백만원이다. 한 벌로 환산하면 백만 배 어긋난다.

    매도·매수 총량은 12분류 전부 응답에 있지만 저장하지 않는다. 확정값에서 읽는 것은 방향과
    규모이고, 회전율이 필요해지면 재호출 없이 컬럼만 늘린다.
    """

    __tablename__ = "stock_investor_trade_daily"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "stock_code",
            "business_date",
            name="uq_stock_investor_trade_daily_natural_key",
        ),
        Index("ix_stock_investor_trade_daily_source_record_id", "source_record_id"),
        Index("ix_stock_investor_trade_daily_business_date", "business_date"),
        table_options(
            comment="종목별 투자자 매매동향의 장 마감 뒤 확정 일별값을 누적하는 테이블",
            database="default",
        ),
    )

    provider: Mapped[str] = mapped_column(Text, nullable=False, comment="데이터 제공처 식별자(kis)")
    stock_code: Mapped[str] = mapped_column(
        Text, nullable=False, comment="6자리 종목코드(005930, 000660). 종목 이름은 instrument 마스터가 갖는다"
    )
    business_date: Mapped[date] = mapped_column(
        nullable=False,
        comment="거래일(stck_bsop_date). KRX 영업일 기준이며 시각은 담지 않는다",
    )
    close_price: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, comment="종가(stck_clpr). 단위는 원. 수급과 가격을 한 화면에서 겹치려고 저장한다"
    )
    accumulated_volume: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="누적 거래량(acml_vol). 단위는 주"
    )
    accumulated_trade_amount: Mapped[Decimal] = mapped_column(
        Numeric(24, 2),
        nullable=False,
        comment="누적 거래대금(acml_tr_pbmn). **단위는 원이다.** 투자자별 대금만 백만원이라 섞어 쓰면 안 된다",
    )
    foreign_net_buy_qty: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="외국인 순매수 수량(frgn_ntby_qty). 단위는 주. 등록+미등록과 일치하는지 검증한다"
    )
    foreign_registered_net_buy_qty: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="외국인 등록분 순매수 수량(frgn_reg_ntby_qty). 단위는 주"
    )
    foreign_unregistered_net_buy_qty: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="외국인 미등록분 순매수 수량(frgn_nreg_ntby_qty). 단위는 주"
    )
    individual_net_buy_qty: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="개인 순매수 수량(prsn_ntby_qty). 단위는 주. 장중 추정 API에는 없는 값이다"
    )
    institution_net_buy_qty: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="기관계 순매수 수량(orgn_ntby_qty). 단위는 주. 세부 일곱의 합과 일치하는지 검증한다"
    )
    securities_net_buy_qty: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="금융투자 순매수 수량(scrt_ntby_qty). 기관계의 부분집합이다"
    )
    investment_trust_net_buy_qty: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="투자신탁 순매수 수량(ivtr_ntby_qty). 기관계의 부분집합이다"
    )
    private_equity_net_buy_qty: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        comment="사모펀드 순매수 수량(pe_fund_ntby_vol). 이 분류만 접미사가 _vol이다",
    )
    bank_net_buy_qty: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="은행 순매수 수량(bank_ntby_qty). 기관계의 부분집합이다"
    )
    insurance_net_buy_qty: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="보험 순매수 수량(insu_ntby_qty). 기관계의 부분집합이다"
    )
    merchant_bank_net_buy_qty: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="종금 순매수 수량(mrbn_ntby_qty). 기관계의 부분집합이다"
    )
    pension_fund_net_buy_qty: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="기금 순매수 수량(fund_ntby_qty). 기관계의 부분집합이다"
    )
    other_corporation_net_buy_qty: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        comment="기타법인 순매수 수량(etc_corp_ntby_vol). 기관계 밖이며 접미사가 _vol이다",
    )
    other_organization_net_buy_qty: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        comment="기타단체 순매수 수량(etc_orgt_ntby_vol). 기관계 밖이며 접미사가 _vol이다",
    )
    foreign_net_buy_amount: Mapped[Decimal] = mapped_column(
        Numeric(24, 2), nullable=False, comment="외국인 순매수 대금(frgn_ntby_tr_pbmn). **단위는 백만원이다**"
    )
    institution_net_buy_amount: Mapped[Decimal] = mapped_column(
        Numeric(24, 2), nullable=False, comment="기관계 순매수 대금(orgn_ntby_tr_pbmn). 단위는 백만원"
    )
    individual_net_buy_amount: Mapped[Decimal] = mapped_column(
        Numeric(24, 2), nullable=False, comment="개인 순매수 대금(prsn_ntby_tr_pbmn). 단위는 백만원"
    )
    source_record_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("source_record.id", ondelete="RESTRICT"),
        nullable=False,
        comment="이 행을 마지막으로 갱신한 수집의 source_record 레코드 ID",
    )
