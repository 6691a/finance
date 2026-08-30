// 테스트 fixture. **API 응답 계약과 같은 모양이다** — 응답을 바꿀 때 backend 응답
// 테스트와 이 파일을 같은 변경에서 고치는 것이 계약이다.
//
// 픽스처가 맨 객체가 아니라 타입이 붙은 상수인 이유는, 계약이 바뀌면 여기서 먼저 컴파일이
// 깨져야 하기 때문이다.

import type {
  GraphResponse,
  LlmRunDetail,
  LlmRunItem,
  LlmRunList,
  QualityResponse,
  ThesisDetail,
  ThesisList,
  ToolCallDetail,
  ToolCallSummary,
} from "./types";

export const RUN: LlmRunItem = {
  id: 9,
  kind: "forecast",
  run_date: "2026-08-26",
  run_slot: "intraday_midday",
  horizon_days: null,
  as_of_at: "2026-08-26T03:35:00Z",
  dag_run_id: "scheduled__2026-08-26T03:35:00+00:00",
  try_number: 1,
  llm_model: "grok-4.6",
  prompt_version: "7",
  started_at: "2026-08-26T03:35:00Z",
  finished_at: "2026-08-26T03:36:30Z",
  duration_ms: 90000,
  status: "succeeded",
  error: null,
  tool_rounds: 2,
  tool_call_count: 2,
  tool_result_chars: 54555,
  investigation_truncated: false,
  produced_count: 1,
  subjects_requested: 6,
  subjects_answered: 6,
  prompt_tokens: 41200,
  cached_prompt_tokens: 38000,
  completion_tokens: 5100,
  reasoning_tokens: 3900,
  url: "/api/llm-runs/9",
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
  items: [RUN, RUNNING_RUN, FAILED_RUN],
  limit: 50,
  offset: 0,
  has_more: false,
};

export const CALL: ToolCallSummary = {
  seq: 1,
  round_no: 1,
  tool_call_id: "call_1",
  tool_name: "recent_documents",
  arguments: { limit: 5 },
  validated_arguments: { limit: 5, kind: "document" },
  requested_at: "2026-08-26T03:35:10Z",
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
  tool_name: "index_daily_bars",
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
  error: "limit must be <= 20",
  url: "/api/llm-runs/9/tool-calls/3",
};

export const RUN_DETAIL: LlmRunDetail = {
  ...RUN,
  tool_calls: [CALL, SIBLING_CALL, FAILED_CALL],
  produced_theses: [
    {
      id: 1,
      run_date: "2026-08-26",
      run_slot: "intraday_midday",
      subject_kind: "index",
      subject_code: "KOSPI",
      label: "코스피",
      url: "/api/theses/1",
    },
  ],
  narrated_outcomes: [],
};

export const CALL_DETAIL: ToolCallDetail = { ...CALL, result: '{"rows": [1, 2]}' };

/** JSON이 아닌 결과. 파싱 실패는 화면 오류가 아니라 원문 표시다. */
export const PLAIN_CALL_DETAIL: ToolCallDetail = {
  ...CALL,
  seq: 4,
  result: "not json <script>alert(1)</script>",
};

export const THESIS_LIST: ThesisList = {
  items: [
    {
      id: 1,
      run_date: "2026-08-26",
      run_slot: "intraday_midday",
      as_of_at: "2026-08-26T03:35:00Z",
      subject_kind: "index",
      subject_code: "KOSPI",
      label: "코스피",
      prob_up: 0.34,
      prob_down: 0.44,
      prob_flat: 0.22,
      up_return_pct: 0.8,
      down_return_pct: 1.2,
      up_return_band_pct: 0.3,
      down_return_band_pct: 0.4,
      base_price: 3211.5,
      base_at: "2026-08-26T03:30:00Z",
      base_return_pct: 0.21,
      graded_horizons: 0,
      narrated_horizons: 0,
      mean_brier: null,
    },
  ],
  limit: 50,
  offset: 0,
  has_more: false,
};

export const THESIS_DETAIL: ThesisDetail = {
  ...THESIS_LIST.items[0]!,
  up_reasoning: "수출이 늘었다 <img src=x onerror=alert(1)>",
  down_reasoning: "금리가 올랐다",
  flat_reasoning: "둘이 상쇄된다",
  input_state: { session: "2026-08-25" },
  tool_rounds: 2,
  llm_model: "grok-4.6",
  prompt_version: "7",
  dag_run_id: "scheduled__x",
  llm_run: {
    id: 9,
    kind: "forecast",
    status: "succeeded",
    llm_model: "grok-4.6",
    prompt_version: "7",
    try_number: 1,
    started_at: "2026-08-26T03:35:00Z",
    finished_at: "2026-08-26T03:36:30Z",
    tool_rounds: 2,
    tool_call_count: 2,
    tool_result_chars: 54555,
    error: null,
  },
  evidence: [
    {
      rank: 1,
      kind: "document",
      ref: "document:4471",
      title: "<script>alert(1)</script> 반도체 수출 증가",
      url: "https://example.test/a",
      direction: "up",
      mechanism: "수급이 붙는다",
      detail: { value_score: 7 },
    },
    {
      rank: 2,
      kind: "document",
      ref: "document:4472",
      title: "링크가 스크립트인 기사",
      // eslint 대신 우리가 막는다. anchor가 되면 클릭이 스크립트 실행이다.
      url: "javascript:alert(1)",
      direction: "down",
      mechanism: null,
      detail: {},
    },
  ],
  outcomes: [],
  precedents: [],
};

export const GRAPH: GraphResponse = {
  center: "thesis:1",
  nodes: [
    {
      id: "thesis:1",
      labels: ["Thesis"],
      properties: {
        id: 1,
        run_date: "2026-08-26",
        run_slot: "intraday_midday",
        subject_code: "KOSPI",
        label: "코스피",
        brier_score: 0.51,
      },
    },
    {
      id: "thesis:2",
      labels: ["Thesis"],
      properties: {
        id: 2,
        run_date: "2026-08-25",
        run_slot: "pre_open",
        subject_code: "KOSPI",
        label: "코스피",
      },
    },
    {
      id: "document:4471",
      labels: ["Evidence"],
      properties: { kind: "document", ref: "document:4471", title: "반도체 수출 증가", url: null },
    },
    // 라벨을 모르는 노드. 회색 기본 모양으로 떨어지고 화면은 죽지 않는다.
    { id: "mystery:1", labels: ["Rumour"], properties: {} },
  ],
  edges: [
    {
      type: "CITES",
      start: "thesis:1",
      end: "document:4471",
      properties: { rank: 1, direction: "up" },
    },
    { type: "INFORMED_BY", start: "thesis:1", end: "thesis:2", properties: {} },
    { type: "WHISPERS", start: "thesis:1", end: "mystery:1", properties: {} },
  ],
};

export const QUALITY: QualityResponse = {
  forecast: [
    {
      week_start: "2026-08-24",
      horizon_days: 0,
      run_slot: "pre_open",
      llm_model: "grok-4.6",
      prompt_version: "7",
      mean_brier: 0.51,
      brier_samples: 12,
      beats_uniform: true,
      mean_return_error_pct: 0.3,
      mae_return_pct: 0.4,
      return_samples: 4,
      mean_tool_calls: 11,
      mean_tool_result_chars: 54555,
      run_samples: 3,
    },
    {
      week_start: "2026-08-24",
      horizon_days: 0,
      run_slot: "pre_open",
      llm_model: "grok-4.6",
      prompt_version: "8",
      mean_brier: null,
      brier_samples: 0,
      beats_uniform: null,
      mean_return_error_pct: null,
      mae_return_pct: null,
      return_samples: 0,
      mean_tool_calls: null,
      mean_tool_result_chars: null,
      run_samples: 0,
    },
  ],
  narrative: [
    {
      week_start: "2026-08-24",
      horizon_days: 1,
      llm_model: "grok-4.6",
      prompt_version: "2/informed",
      supported: 4,
      contradicted: 1,
      unresolved: 2,
      verdict_samples: 7,
    },
  ],
  uniform_brier: 0.6666666666666666,
};
