"""시세·기업 사실·포지셔닝·수급 모델.

**이 파일이 등록 경로다.** `config.yaml`의 `model_modules`가 `apps.models`를 가리키고
`migrations/env.py`가 그것을 import하는데, 테이블 등록은 클래스를 import하는 부수효과다.
여기서 이름을 빠뜨리면 `Base.metadata`에서 그 테이블이 사라지고 autogenerate가 `DROP`을 낸다.
`tests/models/test_market_models.py`가 이 목록과 metadata를 대조한다.
"""

from apps.models.market.fundamentals import (
    AmountBasis,
    DisclosureEvent,
    EarningsFact,
    EarningsMetric,
    EarningsReleaseType,
    StatementScope,
    StockAnalystOpinion,
)
from apps.models.market.investor_flow import (
    InvestorFlowMarketCode,
    MarketInvestorFlowSnapshot,
    StockInvestorEstimateSnapshot,
    StockInvestorTradeDaily,
)
from apps.models.market.positioning import (
    KrxCreditBalanceRankingDaily,
    KrxMarket,
    KrxMarketFundsDaily,
    KrxMarketSecuritiesLendingDaily,
    KrxStockCreditBalanceDaily,
    KrxStockSecuritiesLendingDaily,
    KrxStockShortSaleDaily,
    MarketMovementSnapshot,
)
from apps.models.market.series import (
    BondFutureBar,
    BondFutureDaily,
    CommodityBar,
    CommodityDaily,
    CryptoBar,
    CryptoDaily,
    FxBar,
    FxDaily,
    IndexBar,
    IndexDaily,
    IndexFutureBar,
    IndexFutureDaily,
    MacroBarColumns,
    MacroDailyColumns,
    RateBar,
    RateDaily,
    StockBar,
    StockDaily,
    StockExchange,
)
from apps.models.market.sessions import (
    IndicatorObservation,
    MarketCode,
    MarketSession,
    SessionVerifier,
)
from apps.models.market.shock import (
    MarketShockEvent,
    MarketShockSearchHit,
    ShockCauseKind,
    ShockCauseStatus,
    ShockDirection,
)

__all__ = [
    "AmountBasis",
    "BondFutureBar",
    "BondFutureDaily",
    "CommodityBar",
    "CommodityDaily",
    "CryptoBar",
    "CryptoDaily",
    "DisclosureEvent",
    "EarningsFact",
    "EarningsMetric",
    "EarningsReleaseType",
    "FxBar",
    "FxDaily",
    "IndexBar",
    "IndexDaily",
    "IndexFutureBar",
    "IndexFutureDaily",
    "IndicatorObservation",
    "InvestorFlowMarketCode",
    "KrxCreditBalanceRankingDaily",
    "KrxMarket",
    "KrxMarketFundsDaily",
    "KrxMarketSecuritiesLendingDaily",
    "KrxStockCreditBalanceDaily",
    "KrxStockSecuritiesLendingDaily",
    "KrxStockShortSaleDaily",
    "MacroBarColumns",
    "MacroDailyColumns",
    "MarketCode",
    "MarketInvestorFlowSnapshot",
    "MarketMovementSnapshot",
    "MarketSession",
    "MarketShockEvent",
    "MarketShockSearchHit",
    "RateBar",
    "RateDaily",
    "SessionVerifier",
    "ShockCauseKind",
    "ShockCauseStatus",
    "ShockDirection",
    "StatementScope",
    "StockAnalystOpinion",
    "StockBar",
    "StockDaily",
    "StockExchange",
    "StockInvestorEstimateSnapshot",
    "StockInvestorTradeDaily",
]
