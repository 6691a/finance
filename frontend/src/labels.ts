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

/**
 * 문서 본문을 받아 봤는가, 못 받았다면 왜인가.
 *
 * **`null`은 여기 없다** — "아직 해 보지 않았다"는 사유가 아니라 큐이고, 화면이 그 둘을
 * 다른 문장으로 말한다.
 */
export const BODY_STATUSES: Labels = {
  ok: "받음",
  empty: "본문 없음",
  attachment_only: "첨부에만 있음",
  unavailable: "받을 수 없음",
};

/** 첨부의 종류. 영상은 링크만 남기고 파일은 내려받는다. */
export const ATTACHMENT_KINDS: Labels = {
  file: "파일",
  video: "영상",
};

/**
 * 전망 슬롯. **이름을 시각이 아니라 뜻으로 짓는다** — 슬롯 시각은 운영 손잡이여서
 * 30분을 옮기는 순간 `slot_1135` 같은 이름은 거짓이 된다.
 */
export const FORECAST_SLOTS: Labels = {
  pre_open: "장전",
  midday: "장중",
  pre_close: "마감전",
};

/** LLM 대화의 종류. 전망은 답을 만들고 관찰은 그래프와 메모에만 쓴다. */
export const RUN_KINDS: Labels = {
  forecast: "전망",
  review: "장후 관찰",
};

/**
 * 관측의 부호. **"같음"은 요인이 오르면 코스피도 올랐다는 뜻**이지 상관계수가 아니다.
 *
 * 셋이다 — 장후 관찰이 요인을 고르지 않고 15개 표에 줄마다 답하면서 "무관"이 생겼다.
 */
export const OBSERVATION_SIGNS: Labels = {
  same: "같음",
  inverse: "반대",
  // **"봤는데 무관"이다.** 관측 없음(`n_obs=0`)과 다르다 — 가중치 분자에 0으로 들어가
  // 안 먹히는 요인을 반감기대로 0에 내린다(2026-09-07).
  none: "무관",
};

/** 관측의 세기. **3이 "주도했다"**이고 1은 "같이 움직였지만 부차적"이다. */
export const OBSERVATION_STRENGTHS: Labels = {
  "1": "부차",
  "2": "보통",
  "3": "주도",
};

/**
 * 메모를 내린 이유. **모델이 정한 것과 코드가 정한 것을 가른다** — 뒤엣것은 판정이
 * 아니라 울타리다.
 */
export const RETIRE_REASONS: Labels = {
  dropped: "모델이 내림",
  unreviewed: "두 번 검토에서 빠짐",
  expired: "나이 상한",
};
