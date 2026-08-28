"""수급·신용·대차·공매도의 응답 계약.

**단위가 칸마다 다르다.** 수량은 주, 금액은 원인데 KIS의 투자자별 대금만 백만원이라
모델 주석이 그것을 못 박고 있다. 화면이 단위를 열 이름에 적는 이유가 그것이다.

**장중 스냅샷과 확정 일별값을 섞지 않는다.** 앞은 분 단위로 갱신되는 추정이고 뒤는 장
마감 뒤의 확정이다. 같은 표에 놓으면 어느 쪽 숫자인지 읽는 사람이 못 가른다.
"""

from datetime import date

from pydantic import Field

from apps.api.schemas.common import ApiModel, Page, UtcDatetime


class InvestorFlowPoint(ApiModel):
    """시장 전체의 장중 누적 매매동향 한 점. **분 단위 추정이다.**"""

    market_code: str = Field(description="시장(KOSPI·KOSDAQ).")
    observed_at: UtcDatetime = Field(description="관측 시각(UTC).")
    foreign_net_buy_qty: int | None = Field(default=None, description="외국인 누적 순매수 수량(주).")
    institution_net_buy_qty: int | None = Field(default=None, description="기관 누적 순매수 수량(주).")
    individual_net_buy_qty: int | None = Field(default=None, description="개인 누적 순매수 수량(주).")
    foreign_net_buy_amount: float | None = Field(default=None, description="외국인 누적 순매수 대금(백만원).")
    institution_net_buy_amount: float | None = Field(default=None, description="기관 누적 순매수 대금(백만원).")
    individual_net_buy_amount: float | None = Field(default=None, description="개인 누적 순매수 대금(백만원).")
    pension_fund_net_buy_qty: int | None = Field(default=None, description="연기금 누적 순매수 수량(주).")
    investment_trust_net_buy_qty: int | None = Field(default=None, description="투신 누적 순매수 수량(주).")


InvestorFlowList = Page[InvestorFlowPoint]


class MarketMovementPoint(ApiModel):
    """상승·보합·하락 종목 수 한 점. 시장의 폭을 본다."""

    symbol: str = Field(description="지수 심볼(KOSPI·KOSDAQ).")
    observed_at: UtcDatetime = Field(description="관측 시각(UTC).")
    upper_limit_count: int | None = Field(default=None, description="상한가 종목 수.")
    rising_count: int | None = Field(default=None, description="상승 종목 수.")
    unchanged_count: int | None = Field(default=None, description="보합 종목 수.")
    falling_count: int | None = Field(default=None, description="하락 종목 수.")
    lower_limit_count: int | None = Field(default=None, description="하한가 종목 수.")


MarketMovementList = Page[MarketMovementPoint]


class StockFlowRow(ApiModel):
    """종목별 투자자 매매동향의 **확정** 일별값. 가격도 같은 행에 있다."""

    stock_code: str = Field(description="6자리 종목코드.")
    business_date: date = Field(description="거래일(KRX 영업일).")
    close_price: float = Field(description="종가(원).")
    accumulated_volume: int = Field(description="누적 거래량(주).")
    accumulated_trade_amount: float = Field(
        description="누적 거래대금. **단위는 원이다** — 투자자별 대금만 백만원이라 섞어 쓰면 안 된다."
    )
    foreign_net_buy_qty: int = Field(description="외국인 순매수 수량(주). 등록+미등록의 합이다.")
    foreign_registered_net_buy_qty: int = Field(description="외국인 등록분 순매수 수량(주).")
    foreign_unregistered_net_buy_qty: int = Field(description="외국인 미등록분 순매수 수량(주).")
    institution_net_buy_qty: int = Field(
        description="기관계 순매수 수량(주). **아래 세부 일곱의 합이다** — 기타법인·기타단체는 여기 안 들어간다."
    )
    individual_net_buy_qty: int = Field(description="개인 순매수 수량(주).")
    # 기관계의 세부 일곱. 제공처가 이 단위로 주므로 우리도 그대로 낸다 — 합쳐 두면
    # "연기금이 샀나 금융투자가 샀나"를 화면에서 되물을 수 없다.
    securities_net_buy_qty: int = Field(description="금융투자 순매수 수량(주). 기관계의 부분집합.")
    investment_trust_net_buy_qty: int = Field(description="투자신탁 순매수 수량(주). 기관계의 부분집합.")
    private_equity_net_buy_qty: int = Field(description="사모펀드 순매수 수량(주). 기관계의 부분집합.")
    bank_net_buy_qty: int = Field(description="은행 순매수 수량(주). 기관계의 부분집합.")
    insurance_net_buy_qty: int = Field(description="보험 순매수 수량(주). 기관계의 부분집합.")
    merchant_bank_net_buy_qty: int = Field(description="종금 순매수 수량(주). 기관계의 부분집합.")
    pension_fund_net_buy_qty: int = Field(description="기금(연기금) 순매수 수량(주). 기관계의 부분집합.")
    # 기관계 **밖**이다. 세부 일곱과 더하면 기관계가 아니게 된다.
    other_corporation_net_buy_qty: int = Field(description="기타법인 순매수 수량(주). 기관계 밖이다.")
    other_organization_net_buy_qty: int = Field(description="기타단체 순매수 수량(주). 기관계 밖이다.")
    foreign_net_buy_amount: float | None = Field(default=None, description="외국인 순매수 대금(백만원).")
    institution_net_buy_amount: float | None = Field(default=None, description="기관 순매수 대금(백만원).")
    individual_net_buy_amount: float | None = Field(default=None, description="개인 순매수 대금(백만원).")


StockFlowList = Page[StockFlowRow]


class InvestorEstimateRow(ApiModel):
    """종목별 외국인·기관 **추정** 순매수. 장중 슬롯마다 갱신된다."""

    stock_code: str = Field(description="6자리 종목코드.")
    business_date: date = Field(description="거래일.")
    source_time_code: str = Field(description="제공처의 갱신 슬롯 코드.")
    foreign_net_buy_qty: int | None = Field(default=None, description="외국인 추정 순매수(주).")
    institution_net_buy_qty: int | None = Field(default=None, description="기관 추정 순매수(주).")
    total_net_buy_qty: int | None = Field(default=None, description="합계 추정 순매수(주).")
    collected_at: UtcDatetime = Field(description="수집 시각(UTC). 같은 거래일에 여러 번 온다.")


InvestorEstimateList = Page[InvestorEstimateRow]


class ShortSaleRow(ApiModel):
    """종목별 공매도. **비중은 제공처가 준 값이다** — 우리가 다시 계산하지 않는다."""

    stock_code: str = Field(description="6자리 종목코드.")
    business_date: date = Field(description="영업일.")
    close_price: float | None = Field(default=None, description="종가(원).")
    accumulated_volume: int | None = Field(default=None, description="누적 거래량(주).")
    short_sale_quantity: int | None = Field(default=None, description="공매도 체결수량(주).")
    short_sale_volume_ratio: float | None = Field(default=None, description="거래량 대비 공매도 비중(%).")
    short_sale_amount: float | None = Field(default=None, description="공매도 거래대금(원).")
    short_sale_amount_ratio: float | None = Field(default=None, description="거래대금 대비 공매도 비중(%).")
    short_sale_average_price: float | None = Field(default=None, description="공매도 평균가(원).")


ShortSaleList = Page[ShortSaleRow]


class StockLendingRow(ApiModel):
    """종목별 대차거래. 신규·상환·잔고 셋이 한 행이다."""

    stock_code: str = Field(description="6자리 종목코드.")
    business_date: date = Field(description="영업일.")
    close_price: float | None = Field(default=None, description="종가(원).")
    new_quantity: int | None = Field(default=None, description="신규 체결 수량(주).")
    repayment_quantity: int | None = Field(default=None, description="상환 수량(주).")
    balance_quantity: int | None = Field(default=None, description="잔고 수량(주).")
    balance_amount: float | None = Field(default=None, description="잔고 금액(원).")
    balance_change_quantity: int | None = Field(default=None, description="잔고 증감(주).")


class MarketLendingRow(ApiModel):
    """시장 전체의 대차거래."""

    market_code: str = Field(description="시장(KOSPI·KOSDAQ).")
    business_date: date = Field(description="영업일.")
    index_close: float | None = Field(default=None, description="지수 종가.")
    new_quantity: int | None = Field(default=None, description="신규 체결 수량(주).")
    repayment_quantity: int | None = Field(default=None, description="상환 수량(주).")
    balance_quantity: int | None = Field(default=None, description="잔고 수량(주).")
    balance_amount: float | None = Field(default=None, description="잔고 금액(원).")


class LendingList(ApiModel):
    """대차거래. **시장과 종목이 한 응답에 섞이지 않는다** — 배열을 둘로 가른다.

    배열이 둘이라 `Page[T]`를 그대로 쓸 수 없다. 대신 같은 `limit`·`offset`을 **양쪽에
    똑같이** 걸고, `has_more`는 둘 중 하나라도 더 있으면 참이다 — 한쪽이 끝났다고 다음
    버튼이 꺼지면 나머지가 잘린 채로 남는다.
    """

    market: tuple[MarketLendingRow, ...] = Field(default=(), description="시장 전체. 영업일 오름차순.")
    stock: tuple[StockLendingRow, ...] = Field(default=(), description="종목별. 영업일 오름차순.")
    limit: int = Field(description="요청한 쪽 크기. 두 배열에 같이 걸린다.")
    offset: int = Field(description="건너뛴 건수.")
    has_more: bool = Field(default=False, description="둘 중 하나라도 다음 쪽이 있나.")


class CreditBalanceRow(ApiModel):
    """종목별 신용잔고. 융자(loan)와 신용대주(short_loan)가 따로 있다."""

    stock_code: str = Field(description="6자리 종목코드.")
    trade_date: date = Field(description="거래일. **결제일과 다르다.**")
    settlement_date: date | None = Field(default=None, description="결제일.")
    close_price: float | None = Field(default=None, description="종가(원).")
    loan_balance_quantity: int | None = Field(default=None, description="융자 잔고 수량(주).")
    loan_balance_amount: float | None = Field(default=None, description="융자 잔고 금액(원).")
    loan_balance_rate: float | None = Field(default=None, description="융자 잔고 비율(%).")
    short_loan_balance_quantity: int | None = Field(default=None, description="대주 잔고 수량(주).")
    short_loan_balance_amount: float | None = Field(default=None, description="대주 잔고 금액(원).")
    short_loan_balance_rate: float | None = Field(default=None, description="대주 잔고 비율(%).")


CreditBalanceList = Page[CreditBalanceRow]


class CreditRankingRow(ApiModel):
    """융자잔고 상위 종목의 순위 스냅샷. **날짜별로 순위가 다시 매겨진다.**"""

    standard_date: date = Field(description="기준일.")
    comparison_date: date | None = Field(default=None, description="증감률을 잰 비교일.")
    rank: int = Field(description="순위(1부터).")
    stock_code: str = Field(description="6자리 종목코드.")
    stock_name: str | None = Field(default=None, description="종목 이름.")
    close_price: float | None = Field(default=None, description="종가(원).")
    loan_balance_quantity: int | None = Field(default=None, description="융자 잔고 수량(주).")
    loan_balance_amount: float | None = Field(default=None, description="융자 잔고 금액(원).")
    loan_balance_rate: float | None = Field(default=None, description="융자 잔고 비율(%).")
    loan_balance_growth_rate: float | None = Field(default=None, description="융자 잔고 증감률(%).")


class CreditRankingList(Page[CreditRankingRow]):
    """융자잔고 순위 한 쪽. 고를 수 있는 기준일을 함께 준다."""

    standard_dates: tuple[date, ...] = Field(
        default=(), description="고를 수 있는 기준일 전부. 화면의 선택지다."
    )


class MarketFundsRow(ApiModel):
    """증시자금 종합. 고객예탁금·신용융자·펀드가 한 행이다."""

    business_date: date = Field(description="영업일.")
    index_close: float | None = Field(default=None, description="지수 종가.")
    customer_deposit: float | None = Field(default=None, description="고객예탁금(억원).")
    customer_deposit_change: float | None = Field(default=None, description="고객예탁금 증감(억원).")
    credit_loan_balance: float | None = Field(default=None, description="신용융자 잔고(억원).")
    unsettled_amount: float | None = Field(default=None, description="미수금(억원).")
    turnover_ratio: float | None = Field(default=None, description="회전율(%).")
    equity_fund_amount: float | None = Field(default=None, description="주식형 펀드(억원).")
    bond_fund_amount: float | None = Field(default=None, description="채권형 펀드(억원).")
    mmf_amount: float | None = Field(default=None, description="MMF(억원).")
    securities_lending_amount: float | None = Field(default=None, description="대차 잔고(억원).")


MarketFundsList = Page[MarketFundsRow]
