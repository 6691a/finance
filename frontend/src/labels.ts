// 저장 값의 한국어 이름. **저장은 영문 enum이고 화면만 한국어다.**
//
// 값 자체를 한국어로 저장하지 않는 이유는 그 값이 DB CHECK·프롬프트·수집기 Enum과 글자
// 그대로 묶여 있기 때문이다. 표기를 바꾸려고 그 사슬을 건드리면 안 된다.
//
// **모르는 값은 감추지 않고 그대로 보인다.** 수집이 종류를 늘렸는데 화면에 표가 없으면
// 빈 칸이 되는데, 빈 칸은 "값이 없다"로 읽혀 거짓이 된다.

export type Labels = Record<string, string>;

export function labelOf(labels: Labels, value: string | null): string {
  if (value === null) return "—";
  return labels[value] ?? value;
}

/** `stock_event_claim.event_type`·`stock_event_outcome.event_type`. */
export const EVENT_TYPES: Labels = {
  shareholder_return: "주주환원",
  earnings: "실적",
  guidance: "가이던스",
};

/** `stock_event_claim.claim_kind`. 기대인가 실제 발표값인가. */
export const CLAIM_KINDS: Labels = {
  expectation: "기대",
  actual: "실제",
};

/** 이벤트 지표. **단위는 지표가 정하고 전부 원이다** — 이름에 단위를 넣지 않는다. */
export const EVENT_METRICS: Labels = {
  total_return_amount: "총 주주환원액",
  buyback_amount: "자사주 매입액",
  dividend_total: "배당 총액",
  dividend_per_share: "주당 배당금",
  revenue: "매출액",
  operating_profit: "영업이익",
  net_income: "순이익",
};

/** 기대 대비 실제의 판정. **숫자 비교가 낳는 셋이고 LLM이 만들지 않는다.** */
export const VERDICTS: Labels = {
  beat: "상회",
  meet: "부합",
  miss: "하회",
};

/** 기술적 신호의 종류. 셋이 서로 다른 렌즈다 — 추세추종·모멘텀·역추세. */
export const SIGNAL_KINDS: Labels = {
  sma_cross: "이동평균 교차",
  macd_cross: "MACD 교차",
  rsi_reversal: "RSI 반전",
};

/** 방향. 추론·신호가 같은 값을 쓴다. */
export const DIRECTIONS: Labels = {
  up: "상승",
  down: "하락",
  flat: "보합",
};

/** 인과 경로가 대상을 민 방향. 값은 방향과 같지만 뜻이 "밀었다"라 말이 다르다. */
export const CAUSAL_SIGNS: Labels = {
  up: "위로",
  down: "아래로",
};

/** 인과 주장의 성격. **둘 다 인과의 증명이 아니다.** */
export const CAUSAL_CONFIDENCES: Labels = {
  observed: "함께 관찰",
  plausible: "해석",
};

/** 인과 대상이 어느 마스터에서 오는지. 값의 성격이 아니라 저장소를 가른다. */
export const CAUSAL_TARGET_KINDS: Labels = {
  instrument: "종목",
  index: "국내 지수",
  quote: "해외 지수·환율·선물",
  indicator: "지표",
};

/** 인과 경로가 인용한 근거의 종류. 셋에 흩어져 있어 `ref`가 `<kind>:<id>`다. */
export const CAUSAL_EVIDENCE_KINDS: Labels = {
  document: "문서",
  disclosure: "공시",
  technical_signal: "기술적 신호",
};
