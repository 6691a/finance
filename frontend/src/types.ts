// API 응답 계약. **`apps/api/schemas/`를 손으로 한 번 옮겨 적은 것이다.**
//
// OpenAPI client generator를 넣지 않는다. 응답을 바꿀 때 backend 응답 테스트와 여기
// 그리고 프런트 fixture를 같은 변경에서 고치는 것이 계약이다.
//
// component props와 섞지 않는다 — 이 파일에 있는 것은 서버가 주는 모양뿐이다.

/** UTC ISO 8601 `Z` 문자열. 시간대 변환은 화면이 한다. */
export type UtcText = string;

/** `2026-08-26` KST 세션 날짜. */
export type DayText = string;

export interface ThesisSummary {
  id: number;
  run_date: DayText;
  run_slot: string;
  as_of_at: UtcText;
  subject_kind: string;
  subject_code: string;
  label: string;
  prob_up: number;
  prob_down: number;
  prob_flat: number;
  up_return_pct: number | null;
  down_return_pct: number | null;
  /** `up_return_pct`의 ± 폭(퍼센트포인트). **상한이 아니라 구간의 반이다.** */
  up_return_band_pct: number | null;
  down_return_band_pct: number | null;
  /** **확률 셋과 등락률 둘의 분모.** 이 가격에서 그 세션 마감까지가 채점 창이다. */
  base_price: number | null;
  base_at: UtcText | null;
  /** 직전 세션 종가에서 `base_price`까지 **이미 온** 등락률. 예측과 축이 다르다. */
  base_return_pct: number | null;
  graded_horizons: number;
  narrated_horizons: number;
  mean_brier: number | null;
}

export interface ThesisList {
  items: ThesisSummary[];
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface EvidenceCitation {
  rank: number;
  kind: string;
  ref: string;
  title: string;
  url: string | null;
  direction: string | null;
  mechanism: string | null;
  detail: Record<string, unknown>;
}

export interface PrecedentRef {
  id: number;
  run_date: DayText;
  run_slot: string;
  subject_kind: string;
  subject_code: string;
  label: string;
  prob_up: number;
  prob_down: number;
  prob_flat: number;
}

/** 추론 상세가 잇는 대화 요약. **`tool_call_count`는 건수이고 배열이 아니다.** */
export interface LlmRunSummary {
  id: number;
  kind: string;
  status: string;
  llm_model: string;
  prompt_version: string;
  try_number: number;
  started_at: UtcText;
  finished_at: UtcText | null;
  tool_rounds: number;
  tool_call_count: number;
  tool_result_chars: number;
  error: string | null;
}

export interface ThesisOutcomeItem {
  horizon_days: number;
  as_of_at: UtcText;
  evaluated_at: UtcText | null;
  actual_return_pct: number | null;
  actual_outcome: string | null;
  brier_score: number | null;
  predicted_return_pct: number | null;
  return_error_pct: number | null;
  /** 실현된 방향의 ± 폭 스냅샷. **밴드 적중은 `abs(return_error_pct) <= 이 값`이다.** */
  predicted_band_pct: number | null;
  narrative: string | null;
  verdict: string | null;
  narrative_at: UtcText | null;
  llm_model: string | null;
  prompt_version: string | null;
  narration_run: LlmRunSummary | null;
  evidence: EvidenceCitation[];
}

export interface ThesisDetail extends ThesisSummary {
  up_reasoning: string;
  down_reasoning: string;
  flat_reasoning: string;
  input_state: Record<string, unknown>;
  tool_rounds: number;
  llm_model: string;
  prompt_version: string;
  dag_run_id: string;
  llm_run: LlmRunSummary | null;
  evidence: EvidenceCitation[];
  outcomes: ThesisOutcomeItem[];
  precedents: PrecedentRef[];
}

export interface GraphNode {
  id: string;
  labels: string[];
  properties: Record<string, unknown>;
}

export interface GraphEdge {
  type: string;
  start: string;
  end: string;
  properties: Record<string, unknown>;
}

export interface GraphResponse {
  center: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface ToolCallSummary {
  seq: number;
  round_no: number;
  tool_call_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  validated_arguments: Record<string, unknown> | null;
  requested_at: UtcText;
  duration_ms: number | null;
  result_chars: number;
  delivered: boolean;
  error_kind: string | null;
  error: string | null;
  url: string;
}

export interface ToolCallDetail extends ToolCallSummary {
  result: string | null;
}

export interface ProducedThesis {
  id: number;
  run_date: DayText;
  run_slot: string;
  subject_kind: string;
  subject_code: string;
  label: string;
  url: string;
}

export interface NarratedOutcome {
  thesis_id: number;
  horizon_days: number;
  subject_code: string;
  label: string;
  verdict: string | null;
  url: string;
}

export interface LlmRunItem {
  id: number;
  kind: string;
  run_date: DayText;
  /** **인과 그래프(`causal`) 실행은 null이다** — 그 대화의 축은 슬롯이 아니라 주다. */
  run_slot: string | null;
  horizon_days: number | null;
  as_of_at: UtcText;
  dag_run_id: string;
  try_number: number;
  llm_model: string;
  prompt_version: string;
  started_at: UtcText;
  finished_at: UtcText | null;
  duration_ms: number | null;
  status: string;
  error: string | null;
  tool_rounds: number;
  tool_call_count: number;
  tool_result_chars: number;
  investigation_truncated: boolean;
  produced_count: number;
  subjects_requested: number | null;
  /** 요청보다 적으면 조용히 빠진 대상이 있다. */
  subjects_answered: number | null;
  /** 캐시분을 포함한 입력 토큰. */
  prompt_tokens: number | null;
  /** `prompt_tokens`에 포함된다 — 이 부분이 훨씬 싸다. */
  cached_prompt_tokens: number | null;
  /** `reasoning_tokens`를 포함한다. */
  completion_tokens: number | null;
  reasoning_tokens: number | null;
  url: string;
}

export interface LlmRunList {
  items: LlmRunItem[];
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface LlmRunDetail extends LlmRunItem {
  tool_calls: ToolCallSummary[];
  produced_theses: ProducedThesis[];
  narrated_outcomes: NarratedOutcome[];
}

export interface ForecastQualityRow {
  week_start: DayText;
  horizon_days: number;
  run_slot: string;
  llm_model: string;
  prompt_version: string;
  mean_brier: number | null;
  brier_samples: number;
  beats_uniform: boolean | null;
  mean_return_error_pct: number | null;
  mae_return_pct: number | null;
  return_samples: number;
  mean_tool_calls: number | null;
  mean_tool_result_chars: number | null;
  run_samples: number;
}

export interface NarrativeQualityRow {
  week_start: DayText;
  horizon_days: number;
  llm_model: string;
  prompt_version: string;
  supported: number;
  contradicted: number;
  unresolved: number;
  verdict_samples: number;
}

export interface QualityResponse {
  forecast: ForecastQualityRow[];
  narrative: NarrativeQualityRow[];
  uniform_brier: number;
}

// --- 15단계: 수집 원자료 -------------------------------------------------------

export interface QuoteSymbolItem {
  kind: string;
  symbol: string;
  provider: string;
  label: string;
  country: string;
  country_name: string;
  /** `equity`에만 있다. 같은 종목이 KRX와 NXT에서 따로 체결된다. */
  exchanges: string[];
  bar_rows: number;
  bar_from: UtcText | null;
  bar_to: UtcText | null;
  daily_rows: number;
  daily_from: DayText | null;
  daily_to: DayText | null;
}

export interface QuoteSymbolList {
  items: QuoteSymbolItem[];
}

/** 컬럼 지향이다. 여섯 배열의 길이가 모두 같다. */
export interface BarSeries {
  kind: string;
  symbol: string;
  exchange: string | null;
  provider: string;
  interval: string;
  points: number;
  times: UtcText[];
  open: number[];
  high: number[];
  low: number[];
  close: number[];
  volume: (number | null)[];
}

export interface DailySeries {
  kind: string;
  symbol: string;
  exchange: string | null;
  provider: string;
  points: number;
  dates: DayText[];
  open: number[];
  high: number[];
  low: number[];
  close: number[];
  volume: (number | null)[];
  /**
   * 그 거래일의 실제 월물. **`index_future`에만 있고 나머지는 빈 배열이다.** 월물이 바뀌면
   * 가격에 갭이 생기는데, 이 값이 없으면 그 갭이 시장 급변인지 롤오버인지 구분할 수 없다.
   */
  contracts: (string | null)[];
}

export interface IndicatorSeriesItem {
  provider: string;
  series_id: string;
  kind: string;
  country: string;
  country_name: string;
  label: string;
  /** 만기 개념이 없는 지표는 null이고 0이 아니다. */
  maturity_months: number | null;
  unit: string | null;
  rows: number;
  observed_from: DayText | null;
  observed_to: DayText | null;
}

export interface IndicatorSeriesList {
  items: IndicatorSeriesItem[];
}

export interface IndicatorPoints {
  provider: string;
  series_id: string;
  kind: string;
  label: string;
  unit: string | null;
  points: number;
  dates: DayText[];
  values: number[];
}

export interface CurvePoint {
  series_id: string;
  maturity_months: number;
  label: string;
  observation_date: DayText;
  value: number;
}

export interface CurveCountry {
  provider: string;
  country: string;
  country_name: string;
  unit: string | null;
  points: CurvePoint[];
}

export interface CurveResponse {
  as_of: DayText;
  countries: CurveCountry[];
}

// --- 15단계 2~4판: 문서·수급·사건·수집 원장 ------------------------------------

export interface DocumentSourceItem {
  slug: string;
  name: string;
  source_kind: string;
  country: string | null;
  language: string | null;
  collection_mode: string;
  enabled: boolean;
  terms_url: string | null;
  terms_checked_at: UtcText | null;
  documents: number;
  latest_at: UtcText | null;
}

export interface DocumentSummary {
  id: number;
  source_slug: string;
  external_id: string;
  title: string;
  document_type: string;
  published_at: UtcText;
  language: string | null;
  content_level: string;
  canonical_url: string | null;
  /** 평가 전이면 null이고 0이 아니다. */
  value_score: number | null;
  direction: string | null;
  assessed_at: UtcText | null;
  llm_model: string | null;
  prompt_version: string | null;
  instruments: string[];
  indicators: string[];
}

export interface DocumentList {
  items: DocumentSummary[];
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface DocumentDetail extends DocumentSummary {
  body: string | null;
  summary: string | null;
  assessment: string | null;
  detected_at: UtcText;
  content_hash: string;
  assessed_content_hash: string | null;
}

export interface DisclosureItem {
  rcept_no: string;
  stock_code: string | null;
  corp_code: string;
  company_name: string;
  report_name: string;
  filer_name: string | null;
  corp_class: string | null;
  receipt_date: DayText;
  detected_at: UtcText;
  remarks: string | null;
  /** 본문을 받아 뒀나. **본문 자체는 목록에 없다** — 한 건이 만 자를 넘는다. */
  has_body: boolean;
  /** DART 원문 뷰어. **제공처가 `dart`일 때만 값이 있다.** */
  url: string | null;
}

export interface EarningsFactItem {
  stock_code: string;
  rcept_no: string;
  release_type: string;
  period_end: DayText;
  statement_scope: string;
  amount_basis: string;
  metric: string;
  current_amount: number | null;
  prior_year_amount: number | null;
  currency: string;
  source_account_name: string | null;
  /** 이 숫자가 나온 공시 원문. **제공처가 `dart`일 때만 값이 있다.** */
  url: string | null;
}

export interface InvestorFlowPoint {
  market_code: string;
  observed_at: UtcText;
  foreign_net_buy_qty: number | null;
  institution_net_buy_qty: number | null;
  individual_net_buy_qty: number | null;
  foreign_net_buy_amount: number | null;
  institution_net_buy_amount: number | null;
  individual_net_buy_amount: number | null;
  pension_fund_net_buy_qty: number | null;
  investment_trust_net_buy_qty: number | null;
}

export interface MarketMovementPoint {
  symbol: string;
  observed_at: UtcText;
  upper_limit_count: number | null;
  rising_count: number | null;
  unchanged_count: number | null;
  falling_count: number | null;
  lower_limit_count: number | null;
}

export interface StockFlowRow {
  stock_code: string;
  business_date: DayText;
  close_price: number;
  accumulated_volume: number;
  accumulated_trade_amount: number;
  foreign_net_buy_qty: number;
  foreign_registered_net_buy_qty: number;
  foreign_unregistered_net_buy_qty: number;
  /** **세부 일곱의 합이다.** 기타법인·기타단체는 여기 안 들어간다. */
  institution_net_buy_qty: number;
  individual_net_buy_qty: number;
  securities_net_buy_qty: number;
  investment_trust_net_buy_qty: number;
  private_equity_net_buy_qty: number;
  bank_net_buy_qty: number;
  insurance_net_buy_qty: number;
  merchant_bank_net_buy_qty: number;
  pension_fund_net_buy_qty: number;
  /** 기관계 **밖**이다. 세부 일곱과 더하면 기관계가 아니게 된다. */
  other_corporation_net_buy_qty: number;
  other_organization_net_buy_qty: number;
  foreign_net_buy_amount: number | null;
  institution_net_buy_amount: number | null;
  individual_net_buy_amount: number | null;
}

export interface InvestorEstimateRow {
  stock_code: string;
  business_date: DayText;
  source_time_code: string;
  foreign_net_buy_qty: number | null;
  institution_net_buy_qty: number | null;
  total_net_buy_qty: number | null;
  collected_at: UtcText;
}

export interface ShortSaleRow {
  stock_code: string;
  business_date: DayText;
  close_price: number | null;
  accumulated_volume: number | null;
  short_sale_quantity: number | null;
  short_sale_volume_ratio: number | null;
  short_sale_amount: number | null;
  short_sale_amount_ratio: number | null;
  short_sale_average_price: number | null;
}

export interface StockLendingRow {
  stock_code: string;
  business_date: DayText;
  close_price: number | null;
  new_quantity: number | null;
  repayment_quantity: number | null;
  balance_quantity: number | null;
  balance_amount: number | null;
  balance_change_quantity: number | null;
}

export interface MarketLendingRow {
  market_code: string;
  business_date: DayText;
  index_close: number | null;
  new_quantity: number | null;
  repayment_quantity: number | null;
  balance_quantity: number | null;
  balance_amount: number | null;
}

export interface LendingList {
  market: MarketLendingRow[];
  stock: StockLendingRow[];
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface CreditBalanceRow {
  stock_code: string;
  trade_date: DayText;
  settlement_date: DayText | null;
  close_price: number | null;
  loan_balance_quantity: number | null;
  loan_balance_amount: number | null;
  loan_balance_rate: number | null;
  short_loan_balance_quantity: number | null;
  short_loan_balance_amount: number | null;
  short_loan_balance_rate: number | null;
}

export interface CreditRankingRow {
  standard_date: DayText;
  comparison_date: DayText | null;
  rank: number;
  stock_code: string;
  stock_name: string | null;
  close_price: number | null;
  loan_balance_quantity: number | null;
  loan_balance_amount: number | null;
  loan_balance_rate: number | null;
  loan_balance_growth_rate: number | null;
}

export interface CreditRankingList extends Paged<CreditRankingRow> {
  standard_dates: DayText[];
}

export interface MarketFundsRow {
  business_date: DayText;
  index_close: number | null;
  customer_deposit: number | null;
  customer_deposit_change: number | null;
  credit_loan_balance: number | null;
  unsettled_amount: number | null;
  turnover_ratio: number | null;
  equity_fund_amount: number | null;
  bond_fund_amount: number | null;
  mmf_amount: number | null;
  securities_lending_amount: number | null;
}

export interface EventClaimRow {
  id: number;
  stock_code: string;
  event_type: string;
  period_key: string;
  metric: string;
  claim_kind: string;
  value: number | null;
  value_low: number | null;
  value_high: number | null;
  stated_at: UtcText;
  broker: string | null;
  document_id: number | null;
}

export interface EventOutcomeRow {
  stock_code: string;
  event_type: string;
  period_key: string;
  metric: string;
  expected_value: number | null;
  expectation_count: number;
  actual_value: number | null;
  surprise_pct: number | null;
  verdict: string | null;
  announced_at: UtcText;
  actual_ref: string | null;
}

export interface EventExtractionRow {
  document_id: number;
  extracted_at: UtcText;
  claim_count: number;
  llm_model: string;
  prompt_version: string;
  extracted_content_hash: string;
}

export interface SignalRow {
  symbol: string;
  signal_date: DayText;
  kind: string;
  direction: string;
  close: number | null;
  sma20: number | null;
  sma60: number | null;
  rsi14: number | null;
  macd: number | null;
  macd_signal: number | null;
  volume_ratio20: number | null;
  rule_version: string;
}

export interface AnalystOpinionRow {
  stock_code: string;
  business_date: DayText;
  broker_name: string;
  opinion: string | null;
  previous_opinion: string | null;
  target_price: number | null;
  previous_close: number | null;
  gap_amount: number | null;
  gap_rate: number | null;
}

export interface SourceHealth {
  source: string;
  source_type: string;
  records: number;
  succeeded: number;
  failed: number;
  running: number;
  quarantined: number;
  rows: number;
  latest_at: UtcText | null;
  latest_status: string | null;
}

export interface SourceHealthList extends Paged<SourceHealth> {
  since: UtcText;
}

export interface SourceRecordRow {
  id: number;
  source_type: string;
  source: string;
  source_key: string | null;
  started_at: UtcText;
  completed_at: UtcText | null;
  status: string;
  record_count: number | null;
  has_payload: boolean;
  payload_uri: string | null;
}

export interface InstrumentRow {
  ticker: string;
  market: string;
  name: string;
  kind: string;
  currency: string;
  source_symbol: string | null;
  is_watched: boolean;
}

export interface MarketSessionRow {
  market_code: string;
  market_name: string;
  country_code: string;
  session_date: DayText;
  kis_business_day: boolean | null;
  kis_trading_day: boolean | null;
  kis_open_day: boolean | null;
  effective_open_day: boolean | null;
  local_settlement_date: DayText | null;
  domestic_settlement_date: DayText | null;
  verified_by: string | null;
}

/**
 * 배열 하나를 읽을 때의 최소 모양. **서버는 전부 `Paged`를 준다** — 표를 그리는 쪽이
 * 쪽 칸을 안 봐도 되게 좁혀 받는 자리에만 쓴다.
 */
export interface Items<T> {
  items: T[];
}

/** 쪽이 나뉘는 목록의 공통 모양. **행을 주는 모든 응답이 이것이다.** */
export interface Paged<T> extends Items<T> {
  limit: number;
  offset: number;
  has_more: boolean;
}

/**
 * 인과 그래프의 경로 하나. 출발점 → 채널 체인 → 대상이다.
 *
 * **출발점은 사건 또는 대상이다.** `source_kind`가 어느 칸이 채워졌는지 말한다 — 대상이
 * 다시 원인이 되는 경로가 있어야 `VIX → NASDAQ100_FUT → SOX → 005930`이 이어진다.
 */
export interface CausalPathRow {
  id: number;
  week_start: DayText;
  source_kind: string;
  event_id: number | null;
  event_title: string | null;
  event_occurred_on: DayText | null;
  source_target_kind: string | null;
  /** 같은 주 다른 경로의 대상이다 — 그래서 그래프에서 노드가 이어진다. */
  source_target_code: string | null;
  source_sign: string | null;
  /** 값의 성격이 아니라 저장소를 가른다 — `US10Y`는 시세, `KTB10Y`는 지표다. */
  target_kind: string;
  target_code: string;
  /** 사건 쪽에서 대상 쪽 순서다. */
  channels: string[];
  sign: string;
  /**
   * observed는 근거 문서가 말함, endpoint_observed는 양 끝 값이 그렇게 움직임,
   * plausible은 해석. **셋 다 인과의 증명이 아니다.**
   */
  confidence: string;
  reasoning: string;
  return_week_change: number;
  return_t1_change: number;
  return_t5_change: number;
  /** percent·basis_point. **숫자만 읽으면 안 된다.** */
  return_unit: string;
  llm_run_id: number | null;
}

/** 경로가 인용한 근거 하나. `<kind>:<id>` 규약은 서버가 이미 풀어서 준다. */
export interface CausalEvidenceRow {
  path_id: number;
  ref: string;
  kind: string;
  /** 문서만 채운다. 다른 종류는 식별자를 그대로 보인다. */
  title: string | null;
  /** 문서는 화면 상세 경로, 공시는 DART 뷰어. 모르는 종류는 null이다. */
  url: string | null;
}

/** 경로 하나와 **그 사건이 그 주에 뻗은 경로 전부**. 자기 자신을 포함한다. */
export interface CausalPathDetail {
  path: CausalPathRow;
  /** **그 주의 경로 전부.** 사건이 아니라 주로 묶는다 — 대상이 다시 원인이 되기 때문이다. */
  siblings: CausalPathRow[];
  /** 그 주 경로들이 인용한 근거 전부. `path_id`로 갈라 읽는다. */
  evidence: CausalEvidenceRow[];
}

export interface CausalEventRow {
  id: number;
  title: string;
  occurred_on: DayText;
  first_seen_week: DayText;
  paths: number;
}

export interface CausalChannelRow {
  id: number;
  name: string;
  first_seen_week: DayText;
  steps: number;
}

/** 그래프 DB가 준 노드 하나. **키는 Postgres의 자연키를 편 문자열이다.** */
export interface CausalGraphNode {
  id: string;
  kind: string;
  label: string;
}

/** 엣지 하나. `path_id`와 `week_start`가 가드의 값이다. */
export interface CausalGraphEdge {
  source: string;
  target: string;
  type: string;
  path_id: number | null;
  week_start: DayText | null;
  position: number | null;
}

/**
 * 탐색용 서브그래프. **원본이 아니라 투영이다** — 실현 등락·근거는 경로 응답이 갖는다.
 *
 * 그래프 DB가 꺼져 있으면 이 응답 대신 503이 오고, 화면은 경로 목록으로 그림을 조립한다.
 */
export interface CausalGraph {
  source: string;
  week_start: DayText | null;
  nodes: CausalGraphNode[];
  edges: CausalGraphEdge[];
}
