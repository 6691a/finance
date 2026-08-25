"""투자자 수급 — 장중 추정치와 확정치.

장중 추정(`*_estimate_snapshot`)과 18:10 확정(`stock_investor_trade_daily`)을 나눠 담는다.
추정은 스냅샷이라 같은 날 여러 행이고 확정은 하루 한 행이다.
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


class InvestorFlowMarketCode(StrEnum):
    """시장별 투자자 매매동향이 붙는 시장 구분.

    `KrxMarket`과 값 집합이 다르다. 저쪽은 "코스피·코스닥 두 현물 시장"이고 여기는 그 둘에
    **파생과 ETF까지** 더한 일곱이다. 상승·보합·하락 분포나 시장 대차 잔고에 콜옵션이라는
    값이 있으면 안 되므로 Enum을 합치지 않는다.

    값은 KIS 조회 코드가 아니라 우리 이름이다. 조회 코드 두 개는 수집기의
    `modules/collectors/market/kis_investor_flow.InvestorFlowMarket`이 든다. 이름을 코드로 두면
    (`K2I`, `999`) DB만 보고 무엇인지 알 수 없다.
    """

    KOSPI = "KOSPI"
    KOSDAQ = "KOSDAQ"
    FUTURES = "FUTURES"
    CALL_OPTION = "CALL_OPTION"
    PUT_OPTION = "PUT_OPTION"
    STOCK_FUTURES = "STOCK_FUTURES"
    ETF = "ETF"


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
            "market_code IN ('KOSPI', 'KOSDAQ', 'FUTURES', 'CALL_OPTION', 'PUT_OPTION', 'STOCK_FUTURES', 'ETF')",
            name="ck_market_investor_flow_snapshot_market_code",
        ),
        Index("ix_market_investor_flow_snapshot_source_record_id", "source_record_id"),
        table_options(
            comment="시장별 외국인·기관·개인의 장중 누적 매매동향을 분 단위로 누적하는 테이블",
            database="default",
        ),
    )

    provider: Mapped[str] = mapped_column(Text, nullable=False, comment="데이터 제공처 식별자(kis)")
    market_code: Mapped[InvestorFlowMarketCode] = mapped_column(
        SqlEnum(
            InvestorFlowMarketCode,
            native_enum=False,
            length=20,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        comment=(
            "시장 구분(KOSPI, KOSDAQ, FUTURES, CALL_OPTION, PUT_OPTION, STOCK_FUTURES, ETF). "
            "현물과 파생이 한 테이블에 섞여 있으므로 조회하는 쪽은 이 칸을 반드시 건다"
        ),
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

    **일봉 네 값도 같은 응답에 있어 함께 저장한다.** 수급과 가격은 겹쳐 봐야 읽힌다. 외국인이
    파는 날 주가가 버텼는지 밀렸는지가 그 자체로 신호다. 지금 종목 가격을 담는 곳이 여기뿐이라
    일봉이 이 테이블에 얹혀 있다. 종목 분봉이 생기면 그쪽으로 옮긴다.
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
    open_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, comment="시가(stck_oprc). 단위는 원")
    high_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, comment="고가(stck_hgpr). 단위는 원")
    low_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, comment="저가(stck_lwpr). 단위는 원")
    close_price: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        nullable=False,
        comment="종가(stck_clpr). 단위는 원. 수급과 가격을 한 화면에서 겹치려고 저장한다",
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
        BigInteger,
        nullable=False,
        comment="외국인 순매수 수량(frgn_ntby_qty). 단위는 주. 등록+미등록과 일치하는지 검증한다",
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
        BigInteger,
        nullable=False,
        comment="기관계 순매수 수량(orgn_ntby_qty). 단위는 주. 세부 일곱의 합과 일치하는지 검증한다",
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
