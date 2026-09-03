"""툴이 돌려주는 값의 모양.

**모듈 경계를 넘는 값은 Pydantic 모델이다.** 이 모델들이 JSON으로 펴져 `ToolMessage` 본문이
되고 그대로 `kospi_tool_call.result`에 저장된다 — 모델이 실제로 무엇을 봤는지의 원본이다.

`args_schema`(`tool_args.py`)와 자리를 나눈 이유는 축이 다르기 때문이다. 저쪽은 모델이 우리에게
보내는 것이고 여기는 우리가 모델에게 보내는 것이다.
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from modules.kospi.domain import Factor, FactorUnit


class _Payload(BaseModel):
    model_config = ConfigDict(frozen=True)


class FactorPoint(_Payload):
    """요인 하나의 관측 하루."""

    date: date
    value: float | None = None
    # 직전 관측 대비 변화. 단위는 상위 payload의 `unit`이 말한다.
    change: float | None = None
    # 퍼센트 변화. 금리 요인은 `None`이다 — 4.65 → 4.70을 `+1.08%`로 읽지 않게 한다.
    change_pct: float | None = None


class FlowPoint(_Payload):
    """시장 단위 수급 하루. **수량이 값이고 금액은 단위를 안 밝힌다.**"""

    date: date
    observed_at: datetime
    net_buy_qty: float | None = None
    # 제공처 원문 그대로. `market_investor_flow_snapshot`의 모델 주석이 "단위 미확정"이라
    # 여기서도 단위를 주장하지 않는다.
    net_buy_amount_raw: float | None = None


class StockPoint(_Payload):
    """종목 요인 하루. 종가와 수급을 함께 준다."""

    date: date
    close: float | None = None
    change_pct: float | None = None
    foreign_net_buy_qty: float | None = None
    institution_net_buy_qty: float | None = None
    individual_net_buy_qty: float | None = None


class FactorHistoryPayload(_Payload):
    """`factor_history` 툴의 응답.

    **`unit`을 반드시 싣는다.** 같은 모양의 표에 퍼센트와 bp가 섞여 오므로, 단위가 없으면
    모델이 둘을 같은 축으로 읽는다.
    """

    factor: Factor
    label: str
    unit: FactorUnit
    unit_note: str
    points: tuple[FactorPoint, ...] = ()
    flows: tuple[FlowPoint, ...] = ()
    stocks: tuple[StockPoint, ...] = ()


class NewsRow(_Payload):
    """평가된 기사 하나. 본문은 없다."""

    document_id: int
    title: str
    source: str
    published_at: datetime | None = None
    direction: str | None = None
    value_score: int | None = None
    reason: str | None = None
    new_facts: list[str] = []
    tickers: tuple[str, ...] = ()


class NewsPage(_Payload):
    """`recent_news` 툴의 응답. **잘린 수를 밝힌다.**

    24시간 창의 후보가 303건인데 상위 30건만 가면서 그 사실이 응답에 없었다(2026-09-03 실측).
    모델은 "하루 기사가 30건이었고 다 봤다"로 읽고, 잘린 273건 안의 사건을 "없었다"고
    단정할 수 있다. 못 본 것과 없는 것은 다르다.
    """

    total: int
    shown: int
    items: tuple[NewsRow, ...] = ()


class DisclosureRow(_Payload):
    """본문이 있는 공시 하나."""

    rcept_no: str
    stock_code: str | None = None
    company_name: str
    report_name: str
    receipt_date: date
    detected_at: datetime
    body: str
