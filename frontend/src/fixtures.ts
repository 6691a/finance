// 테스트 fixture. **API 응답 계약과 같은 모양이다** — 응답을 바꿀 때 backend 응답
// 테스트와 이 파일을 같은 변경에서 고치는 것이 계약이다.
//
// 픽스처가 맨 객체가 아니라 타입이 붙은 상수인 이유는, 계약이 바뀌면 여기서 먼저 컴파일이
// 깨져야 하기 때문이다.

import type {
  ForecastAccuracy,
  ForecastDetail,
  ForecastItem,
  ForecastList,
  LlmRunDetail,
  LlmRunItem,
  LlmRunList,
  MemoryList,
  ObservationList,
  QualityResponse,
  RelationGraph,
  RelationList,
  ToolCallDetail,
  ToolCallSummary,
} from "./types";

export const RUN: LlmRunItem = {
  id: 9,
  kind: "forecast",
  run_date: "2026-09-03",
  slot: "midday",
  as_of_at: "2026-09-03T02:35:00Z",
  dag_run_id: "scheduled__2026-09-03T02:35:00+00:00",
  try_number: 1,
  llm_model: "grok-4.6",
  prompt_version: "3",
  started_at: "2026-09-03T02:35:00Z",
  finished_at: "2026-09-03T02:36:30Z",
  duration_ms: 90000,
  status: "succeeded",
  error: null,
  tool_rounds: 2,
  tool_call_count: 2,
  tool_result_chars: 54555,
  truncated: false,
  rejected: 0,
  observations_written: null,
  // **전망 대화는 메모 칸이 전부 null이다** — 0으로 채우면 "해당 없음"이 "0건"이 된다.
  memories: {
    written: null,
    rejected: null,
    kept: null,
    dropped: null,
    unreviewed: null,
    expired: null,
  },
  produced_count: 1,
  prompt_tokens: 41200,
  cached_prompt_tokens: 38000,
  completion_tokens: 5100,
  reasoning_tokens: 3900,
  url: "/api/llm-runs/9",
};

export const REVIEW_RUN: LlmRunItem = {
  ...RUN,
  id: 13,
  kind: "review",
  slot: null,
  prompt_version: "1",
  observations_written: 4,
  memories: { written: 1, rejected: 0, kept: 2, dropped: 1, unreviewed: 0, expired: 0 },
  produced_count: 0,
  url: "/api/llm-runs/13",
};

export const RUNNING_RUN: LlmRunItem = {
  ...RUN,
  id: 11,
  status: "running",
  finished_at: null,
  duration_ms: null,
  produced_count: 0,
};

export const FAILED_RUN: LlmRunItem = {
  ...RUN,
  id: 12,
  status: "failed",
  error: "모델이 붙지 않았다",
  produced_count: 0,
};

export const RUN_LIST: LlmRunList = {
  items: [RUN, REVIEW_RUN, RUNNING_RUN, FAILED_RUN],
  limit: 50,
  offset: 0,
  has_more: false,
};

export const CALL: ToolCallSummary = {
  seq: 1,
  round_no: 1,
  tool_call_id: "call_1",
  tool_name: "factor_history",
  arguments: { factor: "US10Y", days: 10 },
  validated_arguments: { factor: "US10Y", days: 10 },
  requested_at: "2026-09-03T02:35:10Z",
  duration_ms: 42,
  result_chars: 18,
  delivered: true,
  error_kind: null,
  error: null,
  url: "/api/llm-runs/9/tool-calls/1",
};

/** 같은 라운드의 sibling. 병렬일 수 있어 인과 화살표를 그리지 않는다. */
export const SIBLING_CALL: ToolCallSummary = {
  ...CALL,
  seq: 2,
  tool_call_id: "call_2",
  tool_name: "recent_news",
  delivered: false,
  url: "/api/llm-runs/9/tool-calls/2",
};

export const FAILED_CALL: ToolCallSummary = {
  ...CALL,
  seq: 3,
  round_no: 2,
  tool_call_id: "call_3",
  validated_arguments: null,
  duration_ms: null,
  result_chars: 0,
  error_kind: "validation",
  error: "days must be <= 30",
  url: "/api/llm-runs/9/tool-calls/3",
};

export const RUN_DETAIL: LlmRunDetail = {
  ...RUN,
  tool_calls: [CALL, SIBLING_CALL, FAILED_CALL],
  produced_forecasts: [
    {
      run_date: "2026-09-03",
      slot: "midday",
      direction: "up",
      expected_change_pct: 0.6,
      band_pct: 1.4,
      hit: null,
      url: "/api/forecasts/2026-09-03/midday",
    },
  ],
};

export const CALL_DETAIL: ToolCallDetail = { ...CALL, result: '{"rows": [1, 2]}' };

/** JSON이 아닌 결과. 파싱 실패는 화면 오류가 아니라 원문 표시다. */
export const PLAIN_CALL_DETAIL: ToolCallDetail = {
  ...CALL,
  seq: 4,
  result: "not json <script>alert(1)</script>",
};

export const FORECAST: ForecastItem = {
  run_date: "2026-09-03",
  slot: "midday",
  as_of_at: "2026-09-03T02:35:00Z",
  base_price: 6652.75,
  base_at: "2026-09-03T02:34:00Z",
  so_far_pct: 1.37,
  direction: "up",
  expected_change_pct: 0.6,
  band_pct: 1.4,
  reason_count: 2,
  weak: false,
  rejected_reasons: 0,
  actual_change_pct: null,
  hit: null,
  within_band: null,
  graded_at: null,
  prompt_version: "3",
  llm_model: "grok-4.6",
  llm_run_id: 9,
  llm_run_url: "/api/llm-runs/9",
  url: "/api/forecasts/2026-09-03/midday",
};

/** 장전은 `so_far_pct`가 없다 — 아직 안 열렸다. */
export const PRE_OPEN_FORECAST: ForecastItem = {
  ...FORECAST,
  slot: "pre_open",
  as_of_at: "2026-09-02T23:35:00Z",
  so_far_pct: null,
  base_price: 6562.72,
  expected_change_pct: 1.2,
  band_pct: 1.8,
  url: "/api/forecasts/2026-09-03/pre_open",
};

/** 채점된 행. 넷이 함께 채워진다. */
export const GRADED_FORECAST: ForecastItem = {
  ...FORECAST,
  slot: "pre_close",
  direction: "down",
  expected_change_pct: -0.4,
  band_pct: 0.9,
  so_far_pct: -1.12,
  actual_change_pct: -2.31,
  hit: true,
  within_band: false,
  graded_at: "2026-09-03T10:00:00Z",
  url: "/api/forecasts/2026-09-03/pre_close",
};

export const FORECAST_LIST: ForecastList = {
  items: [PRE_OPEN_FORECAST, FORECAST, GRADED_FORECAST],
  limit: 50,
  offset: 0,
  has_more: false,
};

export const FORECAST_DETAIL: ForecastDetail = {
  ...FORECAST,
  reasons: [
    {
      factor: "FOREIGN_NET_BUY",
      memory_id: null,
      slot_ref: null,
      direction: "up",
      statement: "외국인이 3일 연속 순매수다 <img src=x onerror=alert(1)>",
    },
    {
      factor: null,
      memory_id: 17,
      slot_ref: null,
      direction: "down",
      statement: "목요일 CPI 앞두고 관망이 이어질 수 있다",
    },
    {
      factor: null,
      memory_id: null,
      slot_ref: "pre_open",
      direction: "up",
      statement: "장전 판단 유지 — 개장 뒤 반박할 새 정보가 없다",
    },
    // 셋 다 없는 이유. 관측 상태에서 직접 읽은 것이라 링크가 없다.
    {
      factor: null,
      memory_id: null,
      slot_ref: null,
      direction: "down",
      statement: "시가에서 고점까지 벌린 뒤 되밀렸다",
    },
  ],
  input_state: { run_date: "2026-09-03", bars: [] },
  dag_run_id: "scheduled__2026-09-03T02:35:00+00:00",
};

export const ACCURACY: ForecastAccuracy = {
  since: "2026-08-07",
  until: "2026-09-03",
  rows: [
    {
      slot: "pre_open",
      graded: 4,
      hits: 3,
      within_band: 2,
      hit_rate: 0.75,
      band_rate: 0.5,
      mean_abs_error: 1.5,
      pending: 1,
    },
    // 아직 채점이 없는 슬롯. **비율이 `null`이지 0이 아니다.**
    {
      slot: "midday",
      graded: 0,
      hits: 0,
      within_band: 0,
      hit_rate: null,
      band_rate: null,
      mean_abs_error: null,
      pending: 2,
    },
    {
      slot: "pre_close",
      graded: 1,
      hits: 0,
      within_band: 0,
      hit_rate: 0,
      band_rate: 0,
      mean_abs_error: 1.91,
      pending: 0,
    },
    {
      slot: "all",
      graded: 5,
      hits: 3,
      within_band: 2,
      hit_rate: 0.6,
      band_rate: 0.4,
      mean_abs_error: 1.582,
      pending: 3,
    },
  ],
};

export const RELATION_LIST: RelationList = {
  items: [
    {
      factor: "FOREIGN_NET_BUY",
      label: "외국인 순매수",
      weight: 0.8,
      n_obs: 12,
      last_date: "2026-09-01",
      last_note: "외국인 1.2조 순매수가 반도체 중심 상승을 주도",
      recent_signs: ["same", "same", "same"],
      url: "/api/relations/FOREIGN_NET_BUY",
    },
    {
      factor: "US10Y",
      label: "미국 10년물",
      weight: -0.15,
      n_obs: 7,
      last_date: "2026-08-29",
      last_note: "금리 +8bp에도 반도체가 올랐다",
      recent_signs: ["same", "inverse", "inverse"],
      url: "/api/relations/US10Y",
    },
    // 관측이 없는 요인. **0은 "관계 없음"이 아니라 "아직 모른다"다.**
    {
      factor: "SOX",
      label: "필라델피아 반도체",
      weight: 0,
      n_obs: 0,
      last_date: null,
      last_note: "",
      recent_signs: [],
      url: "/api/relations/SOX",
    },
  ],
  limit: 200,
  offset: 0,
  has_more: false,
};

export const RELATION_GRAPH: RelationGraph = {
  as_of_date: "2026-09-03",
  nodes: [
    { id: "index:KOSPI", kind: "index", label: "코스피", n_obs: 0 },
    { id: "factor:FOREIGN_NET_BUY", kind: "factor", label: "외국인 순매수", n_obs: 12 },
    { id: "factor:US10Y", kind: "factor", label: "미국 10년물", n_obs: 7 },
    { id: "factor:SOX", kind: "factor", label: "필라델피아 반도체", n_obs: 0 },
  ],
  edges: [
    { source: "factor:FOREIGN_NET_BUY", target: "index:KOSPI", weight: 0.8, n_obs: 12 },
    { source: "factor:US10Y", target: "index:KOSPI", weight: -0.15, n_obs: 7 },
  ],
};

export const OBSERVATION_LIST: ObservationList = {
  items: [
    {
      factor: "US10Y",
      observed_on: "2026-08-29",
      sign: "inverse",
      strength: 1,
      note: "금리 +8bp에도 반도체가 올랐다",
      weight: 0.5,
      llm_run_id: 13,
      llm_run_url: "/api/llm-runs/13",
    },
  ],
  limit: 50,
  offset: 0,
  has_more: false,
};

export const MEMORY_LIST: MemoryList = {
  items: [
    {
      id: 17,
      created_on: "2026-09-01",
      text: "목요일(09-04) 밤 미국 8월 CPI 발표",
      factor: "US10Y",
      verify_count: 2,
      unreviewed_count: 0,
      last_verified_on: "2026-09-02",
      retired_on: null,
      retire_reason: null,
      llm_run_id: 13,
      llm_run_url: "/api/llm-runs/13",
    },
    // 내린 메모. **노드를 지우지 않는다** — 왜 지웠는지가 남는다.
    {
      id: 12,
      created_on: "2026-08-20",
      text: "삼성전자 자사주 매입 공시 뒤 3일째 지지선 유지",
      factor: null,
      verify_count: 3,
      unreviewed_count: 0,
      last_verified_on: "2026-08-30",
      retired_on: "2026-09-01",
      retire_reason: "dropped",
      llm_run_id: 12,
      llm_run_url: "/api/llm-runs/12",
    },
  ],
  limit: 50,
  offset: 0,
  has_more: false,
};

export const QUALITY: QualityResponse = {
  forecast: [
    {
      week_start: "2026-08-31",
      slot: "pre_open",
      llm_model: "grok-4.6",
      prompt_version: "3",
      graded: 4,
      pending: 1,
      hits: 3,
      hit_rate: 0.75,
      beats_coin_flip: true,
      within_band: 2,
      band_rate: 0.5,
      mean_abs_error: 1.5,
      mean_band_pct: 1.4,
      mean_expected_pct: 0.6,
      weak: 0,
      rejected_reasons: 1,
    },
    // 아직 채점이 없는 판. **비율이 `null`이지 0이 아니다.**
    {
      week_start: "2026-08-31",
      slot: "midday",
      llm_model: "grok-4.6",
      prompt_version: "4",
      graded: 0,
      pending: 3,
      hits: 0,
      hit_rate: null,
      beats_coin_flip: null,
      within_band: 0,
      band_rate: null,
      mean_abs_error: null,
      mean_band_pct: null,
      mean_expected_pct: null,
      weak: 1,
      rejected_reasons: 0,
    },
  ],
  review: [
    {
      week_start: "2026-08-31",
      llm_model: "grok-4.6",
      prompt_version: "1",
      runs: 4,
      observations_written: 14,
      mean_observations: 3.5,
      memories_written: 5,
      memories_rejected: 0,
      memories_dropped: 2,
      memories_expired: 1,
      rejected: 0,
      mean_tool_calls: 15,
      truncated: 0,
    },
  ],
  coin_flip_hit_rate: 0.5,
};
