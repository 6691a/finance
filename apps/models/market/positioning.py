"""시장 포지셔닝 — 등락, 신용, 증시자금, 공매도·대차.

"지금 시장이 어느 쪽으로 기울어 있나"를 말하는 KRX 집계다. 전부 일별이고 자연키에
`market_code` 또는 종목 코드가 들어간다.
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
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column

from apps.core.database import EntityBase, table_options


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
