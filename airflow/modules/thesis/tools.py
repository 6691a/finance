"""`ThesisToolbox`가 툴 응답으로 내보내는 값의 **모양**.

이 값들은 두 경계를 넘는다 — LLM 프롬프트와 `thesis_evidence.detail` JSONB다. 맨 dict로
흘리면 키 오타가 런타임까지 살아 있고, 프롬프트에 빈 칸이 실려도 아무도 못 잡는다.
`thesis/state.py`가 관측 상태에 대해 하는 일을 여기서는 툴 응답에 대해 한다.

**`thesis/state.py`에 넣지 않는 이유.** 그 모듈이 따로 있는 까닭은 `thesis.py`(LangChain)와
`thesis/common.py`(Airflow)가 서로를 모듈 수준에서 import할 수 없어서인데, 툴 응답 모델은
`thesis.py`만 쓴다. 그렇다고 이미 2950줄인 `thesis.py`에 더 얹지도 않는다.

**이 모듈은 LangChain·Airflow·DB를 import하지 않는다.** pydantic과 `modules.technical.indicators`,
`modules.thesis.state`뿐이다.

두 무리로 나뉘고 규칙이 다르다.

- **`Evidence.detail`이 되는 것**(`EvidenceDetail` 유니온) — JSONB에도 저장되므로 **이미
  저장된 행과 키·값 표기가 같아야 한다.** 그래서 시각 칸을 `date`·`datetime`이 아니라
  만드는 쪽이 이미 찍어 둔 `str`로 받는다. 타입을 올리면 `isoformat()`의 `+00:00`이
  pydantic의 `Z`로 바뀌어 과거 행과 갈린다.
- **`_body`로만 나가는 것** — 프롬프트뿐이라 그 제약이 없다. 시각은 제 타입으로 받고
  `model_dump(mode="json")`이 ISO 8601로 찍는다.
"""

from datetime import date
from typing import Any, ClassVar

from pydantic import AwareDatetime, BaseModel, ConfigDict, SerializerFunctionWrapHandler, model_serializer

from modules.technical import indicators
from modules.thesis.state import SignalObservation


class ToolModel(BaseModel):
    """툴 응답 한 조각. **frozen이다** — 재시도 경로에서 값이 바뀌면 프롬프트와 저장값이 어긋난다."""

    model_config = ConfigDict(frozen=True)


class OptionalKeyModel(ToolModel):
    """`None`이면 **키째 사라져야 하는** 칸을 가진 모델.

    `exclude_none=True`를 쓸 수 없다. 같은 모델의 `published_at`·`value`·`maturity_months`처럼
    결측을 `null`로 남겨야 하는 칸까지 함께 지우기 때문이다. 결측(값이 아직 없다)과
    해당 없음(그 종류에는 그 칸이 없다)은 다른 뜻이고, 모델이 `null`을 관측으로 읽으면
    없는 사실을 근거로 쓴다. 그래서 뺄 키를 이름으로 못박는다.
    """

    OMIT_WHEN_NONE: ClassVar[tuple[str, ...]] = ()

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = handler(self)
        for key in self.OMIT_WHEN_NONE:
            if data.get(key) is None:
                data.pop(key, None)
        return data


# ---------------------------------------------------------------------------
# `Evidence.detail`이 되는 것 — 프롬프트 + `thesis_evidence.detail` JSONB
# ---------------------------------------------------------------------------


class DocumentDetail(ToolModel):
    """문서 근거 한 건. `new_facts`와 `reason`은 만드는 쪽이 예산 안에서 이미 잘랐다."""

    source: str
    published_at: str | None = None
    value_score: int | None = None
    direction: str | None = None
    new_facts: tuple[str, ...] = ()
    reason: str = ""
    tickers: tuple[str, ...] = ()


class DisclosureDetail(ToolModel):
    """공시 한 건. 날짜 두 칸은 만드는 쪽이 `isoformat()`으로 찍은 값이다."""

    stock_code: str
    company_name: str
    report_name: str
    receipt_date: str
    detected_at: str


class MacroDetail(OptionalKeyModel):
    """심볼 하나의 창 변화. **금리는 `change_bp`, 나머지는 `change_pct`다.**

    기준값이 0이면 둘 다 없다 — 변화를 0으로 꾸미지 않는다.
    """

    OMIT_WHEN_NONE: ClassVar[tuple[str, ...]] = ("change_bp", "change_pct")

    kind: str
    country: str
    first_close: float
    last_close: float
    window_start: str
    window_end: str
    bar_count: int
    change_bp: float | None = None
    change_pct: float | None = None


class UsCloseDetail(OptionalKeyModel):
    """미국장 마감 한 건. `closed_at_kst`는 이름이 시간대를 밝히는 표시용 문자열이다."""

    OMIT_WHEN_NONE: ClassVar[tuple[str, ...]] = ("change_bp", "change_pct")

    kind: str
    close: float
    previous_close: float
    closed_at_kst: str
    change_bp: float | None = None
    change_pct: float | None = None


class SignalDetail(ToolModel):
    """매매 신호 근거 한 건. 툴 본문에 실리는 축약본은 `SignalObservation`으로 따로 나간다."""

    symbol: str
    signal_date: str
    kind: str
    direction: str
    close: float | None = None
    rsi14: float | None = None
    volume_ratio20: float | None = None


EvidenceDetail = DocumentDetail | DisclosureDetail | MacroDetail | UsCloseDetail | SignalDetail
"""`Evidence.detail`에 들어갈 수 있는 것 전부. 종류가 늘면 여기와 `ThesisEvidenceKind`가 함께 는다."""


# ---------------------------------------------------------------------------
# `_body`로만 나가는 것 — 프롬프트뿐이라 JSONB 호환 제약이 없다
# ---------------------------------------------------------------------------


class IndicatorDetail(OptionalKeyModel):
    """지표 계열 하나의 최신값과 직전값 대비 변화. 직전값이 없으면 변화 칸이 없다."""

    OMIT_WHEN_NONE: ClassVar[tuple[str, ...]] = ("change_bp", "change")

    provider: str
    series_id: str
    country: str
    country_name: str
    label: str
    maturity_months: int | None = None
    unit: str
    observation_date: date
    value: float | None = None
    previous_date: date | None = None
    previous_value: float | None = None
    change_bp: float | None = None
    change: float | None = None


class IndicatorPayload(ToolModel):
    """`macro_indicators` 툴의 응답. `unit_note`가 변화 칸을 어떻게 읽을지 알린다."""

    kind: str
    unit_note: str
    series: tuple[IndicatorDetail, ...] = ()


class MarketFlowRow(ToolModel):
    """시장 전체 투자자 수급 스냅샷 한 건."""

    market_code: str
    observed_at: AwareDatetime
    foreign_net_buy_amount: float | None = None
    institution_net_buy_amount: float | None = None
    individual_net_buy_amount: float | None = None
    pension_fund_net_buy_qty: float | None = None
    investment_trust_net_buy_qty: float | None = None
    amount_unit: str = "백만원"


class MarketBreadthRow(ToolModel):
    """등락 종목 수 스냅샷 한 건."""

    symbol: str
    observed_at: AwareDatetime
    rising: int | None = None
    unchanged: int | None = None
    falling: int | None = None
    upper_limit: int | None = None
    lower_limit: int | None = None


class StockFlowSettledRow(ToolModel):
    """종목 하나의 마감 확정 수급 한 날."""

    stock_code: str
    business_date: date
    close_price: float | None = None
    volume: float | None = None
    foreign_net_buy_qty: float | None = None
    institution_net_buy_qty: float | None = None
    individual_net_buy_qty: float | None = None
    foreign_net_buy_amount: float | None = None
    institution_net_buy_amount: float | None = None
    individual_net_buy_amount: float | None = None


class StockFlowEstimateRow(ToolModel):
    """종목 하나의 장중 추정 수급 한 건. 확정값과 어긋날 수 있다."""

    stock_code: str
    business_date: date
    source_time_code: str | None = None
    collected_at: AwareDatetime
    foreign_net_buy_qty: float | None = None
    institution_net_buy_qty: float | None = None
    total_net_buy_qty: float | None = None


class StockFlowPayload(ToolModel):
    """`stock_investor_flows` 툴의 응답. 확정과 추정을 **한 배열에 섞지 않는다.**"""

    settled: tuple[StockFlowSettledRow, ...] = ()
    intraday_estimate: tuple[StockFlowEstimateRow, ...] = ()
    note: str = "settled는 마감 뒤 확정값, intraday_estimate는 장중 추정값이다. 둘은 어긋날 수 있다"


class MarketFundsRow(ToolModel):
    """증시 자금 지표 한 날."""

    business_date: date
    index_close: float | None = None
    index_change: float | None = None
    customer_deposit: float | None = None
    customer_deposit_change: float | None = None
    credit_loan_balance: float | None = None
    unsettled_amount: float | None = None
    turnover_ratio: float | None = None


class DailyBarRow(ToolModel):
    """일봉 하나. 지표가 아니라 원시 값이다."""

    label: str
    kind: str
    country: str
    business_date: date
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None


class AvailableSymbolRow(ToolModel):
    """일봉을 가진 심볼 하나. 없는 심볼을 물었을 때만 보여 준다."""

    symbol: str
    label: str
    kind: str


class DailyHistoryPayload(ToolModel):
    """`daily_history` 툴의 응답."""

    symbol: str
    bars: tuple[DailyBarRow, ...] = ()
    technical_snapshot: indicators.TechnicalSnapshot | None = None
    recent_signals: tuple[SignalObservation, ...] = ()


class DailyHistoryEmptyPayload(ToolModel):
    """일봉이 아예 없을 때의 `daily_history` 응답.

    **`recent_signals` 칸이 없고 `available_symbols`가 있다.** 한 모델에 둘 다 선택 칸으로
    담지 않는다 — 빈 배열이 실리면 모델이 "이력이 없다"가 아니라 "신호가 없었다"로 읽는다.
    """

    symbol: str
    bars: tuple[DailyBarRow, ...] = ()
    technical_snapshot: None = None
    note: str
    available_symbols: tuple[AvailableSymbolRow, ...] = ()


class ShortCreditRow(ToolModel):
    """종목 하나의 공매도·대차·신용 잔고 한 날."""

    stock_code: str
    label: str
    business_date: date
    short_sale_quantity: float | None = None
    short_sale_volume_ratio: float | None = None
    short_sale_amount: float | None = None
    lending_balance_quantity: float | None = None
    lending_balance_change_quantity: float | None = None
    credit_loan_balance_quantity: float | None = None
    credit_loan_balance_amount: float | None = None
    credit_loan_balance_rate: float | None = None


class OpinionDetail(OptionalKeyModel):
    """투자의견 한 건. 같은 날 같은 증권사 리포트 요약이 있을 때만 `reason`이 붙는다."""

    OMIT_WHEN_NONE: ClassVar[tuple[str, ...]] = ("reason",)

    business_date: date
    broker_name: str
    opinion: str
    previous_opinion: str | None = None
    target_price: float | None = None
    previous_close: float | None = None
    gap_rate: float | None = None
    reason: str | None = None


class AnalystOpinionsPayload(ToolModel):
    """`analyst_opinions` 툴의 응답."""

    stock_code: str
    opinions: tuple[OpinionDetail, ...] = ()


class SurpriseDetail(ToolModel):
    """기대 대비 발표 판정 한 건. 금액은 원 단위 그대로다."""

    event_type: str
    period_key: str
    metric: str
    expected_value: float | None = None
    expectation_count: int | None = None
    actual_value: float | None = None
    surprise_pct: float | None = None
    verdict: str
    announced_at: AwareDatetime


class PendingExpectationDetail(ToolModel):
    """아직 발표되지 않은 이벤트의 대표 기대치."""

    event_type: str
    period_key: str
    metric: str
    expected_value: float | None = None
    expectation_count: int | None = None
    latest_stated_at: AwareDatetime


class EventSurprisesPayload(ToolModel):
    """`event_surprises` 툴의 응답."""

    stock_code: str
    outcomes: tuple[SurpriseDetail, ...] = ()
    pending_expectations: tuple[PendingExpectationDetail, ...] = ()
