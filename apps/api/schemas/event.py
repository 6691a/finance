"""사건의 기대·실제와 그 판정, 그리고 추출 원장의 응답 계약.

**단위 칸이 없다.** 단위는 `metric`이 정하고 전부 원(KRW)이다 — 원문의 조·억 표기는 수집
단계에서 정규화하고 모르는 표기는 그 주장을 버린다.

**판정은 첫 성공본 불변이다.** 발표 뒤 기대 행이 늦게 추출돼도 다시 내지 않는다. 화면이
"다시 판정" 같은 손잡이를 주지 않는 이유가 그것이다.
"""

from datetime import date

from pydantic import Field

from apps.api.schemas.common import ApiModel, Page, UtcDatetime


class EventClaimRow(ApiModel):
    """문서에서 뽑은 주장 하나. 기대일 수도 실제일 수도 있다."""

    id: int = Field(description="주장 id.")
    stock_code: str = Field(description="6자리 종목코드.")
    event_type: str = Field(description="사건 종류(earnings·shareholder_return 등).")
    period_key: str = Field(
        description="대상 기간. **`2026`·`2026Q2`·`2026H1` 셋만 허용하고 DB CHECK가 강제한다.**"
    )
    metric: str = Field(description="지표. 실적은 `earnings_fact.metric`과 글자 그대로 같다.")
    claim_kind: str = Field(description="기대(expectation)인가 실제(actual)인가.")
    value: float | None = Field(default=None, description="대표값(원).")
    value_low: float | None = Field(default=None, description="구간 하단(원).")
    value_high: float | None = Field(default=None, description="구간 상단(원).")
    stated_at: UtcDatetime = Field(
        description="이 주장이 나온 시각. **발표 전 기대만 판정에 쓴다**(`stated_at < announced_at`)."
    )
    broker: str | None = Field(default=None, description="증권사. 리포트에서 뽑은 주장에만 있다.")
    document_id: int | None = Field(default=None, description="근거 문서 id.")


EventClaimList = Page[EventClaimRow]


class EventOutcomeRow(ApiModel):
    """기대 대비 실제의 판정 하나. **불변이다.**"""

    stock_code: str = Field(description="6자리 종목코드.")
    event_type: str = Field(description="사건 종류.")
    period_key: str = Field(description="대상 기간.")
    metric: str = Field(description="지표.")
    expected_value: float | None = Field(default=None, description="대표 기대치(원). 순수 함수가 집계한다.")
    expectation_count: int = Field(default=0, description="집계에 들어간 기대 주장 수.")
    actual_value: float | None = Field(default=None, description="실제값(원). 실적은 `earnings_fact`가 원본이다.")
    surprise_pct: float | None = Field(default=None, description="기대 대비 괴리(%).")
    verdict: str | None = Field(
        default=None, description="beat·meet·miss. **LLM이 아니라 순수 함수가 가른다.**"
    )
    announced_at: UtcDatetime = Field(description="발표 시각(UTC).")
    actual_ref: str | None = Field(default=None, description="실제값의 근거 식별자.")


EventOutcomeList = Page[EventOutcomeRow]


class EventExtractionRow(ApiModel):
    """문서별 추출 이력. **주장 0건도 남는다** — "뽑았는데 없었다"와 "아직 안 뽑았다"를 가른다."""

    document_id: int = Field(description="문서 id.")
    extracted_at: UtcDatetime = Field(description="추출한 시각(UTC).")
    claim_count: int = Field(default=0, description="뽑힌 주장 수. 0도 정상이다.")
    llm_model: str = Field(description="추출을 만든 모델.")
    prompt_version: str = Field(description="추출 프롬프트 판.")
    extracted_content_hash: str = Field(description="추출 당시의 본문 해시.")


EventExtractionList = Page[EventExtractionRow]


class SignalRow(ApiModel):
    """확정 일봉에서 검출한 기술적 매매 신호 하나."""

    symbol: str = Field(description="대상 심볼.")
    signal_date: date = Field(description="신호가 난 거래일.")
    kind: str = Field(description="신호 종류(golden_cross·rsi_oversold 등).")
    direction: str = Field(description="방향(up/down).")
    close: float | None = Field(default=None, description="그날 종가.")
    sma20: float | None = Field(default=None, description="20일 이동평균.")
    sma60: float | None = Field(default=None, description="60일 이동평균.")
    rsi14: float | None = Field(default=None, description="14일 RSI.")
    macd: float | None = Field(default=None, description="MACD.")
    macd_signal: float | None = Field(default=None, description="MACD 시그널.")
    volume_ratio20: float | None = Field(default=None, description="20일 평균 대비 거래량 배수.")
    rule_version: str = Field(description="검출 규칙 판. 판이 바뀌면 신호도 갈린다.")


SignalList = Page[SignalRow]


class AnalystOpinionRow(ApiModel):
    """증권사 투자의견·목표주가 하나. **구조화된 숫자는 문서가 아니라 여기 있다.**"""

    stock_code: str = Field(description="6자리 종목코드.")
    business_date: date = Field(description="발표일.")
    broker_name: str = Field(description="증권사.")
    opinion: str | None = Field(default=None, description="투자의견 표기.")
    previous_opinion: str | None = Field(default=None, description="직전 투자의견. 바뀌었는지가 신호다.")
    target_price: float | None = Field(default=None, description="목표주가(원).")
    previous_close: float | None = Field(default=None, description="발표 시점 전일 종가(원).")
    gap_amount: float | None = Field(default=None, description="목표주가와 현재가의 차이(원).")
    gap_rate: float | None = Field(default=None, description="괴리율(%).")


AnalystOpinionList = Page[AnalystOpinionRow]
