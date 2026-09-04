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

// --- 코스피 일일 전망 ---------------------------------------------------------

export interface ForecastReason {
  /** 인용한 요인 코드. 관계 화면으로 링크한다. */
  factor: string | null;
  /** 인용한 메모 id. */
  memory_id: number | null;
  /** 인용한 같은 날 앞 슬롯. */
  slot_ref: string | null;
  direction: string | null;
  statement: string;
}

export interface ForecastItem {
  run_date: DayText;
  /** `pre_open`·`midday`·`pre_close`. **슬롯이 기준가의 뜻을 정한다.** */
  slot: string;
  as_of_at: UtcText;
  base_price: number;
  base_at: UtcText;
  /** 전일 종가 대비 현재가 등락률. **장전은 null이다** — 아직 안 열렸다. */
  so_far_pct: number | null;
  direction: string;
  expected_change_pct: number;
  band_pct: number;
  reason_count: number;
  /** 이유 0건으로 저장된 약한 답. 화면이 머리표를 붙인다. */
  weak: boolean;
  rejected_reasons: number;
  actual_change_pct: number | null;
  hit: boolean | null;
  within_band: boolean | null;
  graded_at: UtcText | null;
  prompt_version: string;
  llm_model: string;
  llm_run_id: number | null;
  llm_run_url: string | null;
  url: string;
}

export interface ForecastList extends Paged<ForecastItem> {}

export interface ForecastDetail extends ForecastItem {
  /** 저장된 순서 그대로. **그 순서가 중요도다.** */
  reasons: ForecastReason[];
  /** 모델이 본 관측 상태 전부. 모양이 판마다 바뀌므로 표로 그리지 않는다. */
  input_state: Record<string, unknown>;
  dag_run_id: string;
}

export interface ForecastAccuracyRow {
  /** 슬롯 하나이거나 `all`(합계). */
  slot: string;
  /** **비율의 분모이고 반드시 함께 보인다.** */
  graded: number;
  hits: number;
  within_band: number;
  /** 채점 0건이면 null이다 — 0.0이 아니다. */
  hit_rate: number | null;
  band_rate: number | null;
  mean_abs_error: number | null;
  pending: number;
}

export interface ForecastAccuracy {
  since: DayText;
  until: DayText;
  rows: ForecastAccuracyRow[];
}

// --- 요인 관계와 메모 (Neo4j) --------------------------------------------------

export interface RelationItem {
  factor: string;
  label: string;
  /** -1~1. 최근 관측에 기울어 있다(반감기 5일). */
  weight: number;
  /** **0이면 weight를 읽지 않는다** — 0은 "모른다"다. */
  n_obs: number;
  last_date: DayText | null;
  last_note: string;
  /** 감쇠 없는 최근 부호 셋. 가중치와 어긋나면 관계가 바뀌는 중이다. */
  recent_signs: string[];
  url: string;
}

export interface RelationList extends Paged<RelationItem> {}

export interface ObservationItem {
  factor: string;
  observed_on: DayText;
  sign: string;
  strength: number;
  note: string;
  /** 오늘 기준 이 관측 하나의 무게(0~1). */
  weight: number;
  llm_run_id: number | null;
  llm_run_url: string | null;
}

export interface ObservationList extends Paged<ObservationItem> {}

export interface RelationNode {
  id: string;
  kind: string;
  label: string;
  n_obs: number;
}

export interface RelationEdge {
  source: string;
  target: string;
  weight: number;
  n_obs: number;
}

export interface RelationGraph {
  as_of_date: DayText;
  nodes: RelationNode[];
  edges: RelationEdge[];
}

export interface MemoryItem {
  id: number;
  created_on: DayText;
  text: string;
  factor: string | null;
  verify_count: number;
  unreviewed_count: number;
  last_verified_on: DayText | null;
  /** null이면 활성이다. */
  retired_on: DayText | null;
  retire_reason: string | null;
  llm_run_id: number | null;
  llm_run_url: string | null;
}

export interface MemoryList extends Paged<MemoryItem> {}

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

export interface ProducedForecast {
  run_date: DayText;
  slot: string;
  direction: string;
  expected_change_pct: number;
  band_pct: number;
  hit: boolean | null;
  url: string;
}

/** 관찰 대화가 메모에 한 일. **`review`가 아니면 전부 null이다.** */
export interface MemoryLedger {
  written: number | null;
  rejected: number | null;
  kept: number | null;
  dropped: number | null;
  unreviewed: number | null;
  expired: number | null;
}

export interface LlmRunItem {
  id: number;
  /** `forecast`는 전망, `review`는 장후 관찰이다. */
  kind: string;
  run_date: DayText;
  /** **관찰 대화는 null이다** — 그 축은 슬롯이 아니라 하루다. */
  slot: string | null;
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
  tool_rounds: number | null;
  tool_call_count: number | null;
  tool_result_chars: number | null;
  truncated: boolean | null;
  /** **0이 아니면 모델이 조회하지 않은 것을 인용했다.** */
  rejected: number | null;
  observations_written: number | null;
  memories: MemoryLedger;
  produced_count: number;
  /** 캐시분을 포함한 입력 토큰. */
  prompt_tokens: number | null;
  /** `prompt_tokens`에 포함된다 — 이 부분이 훨씬 싸다. */
  cached_prompt_tokens: number | null;
  /** `reasoning_tokens`를 포함한다. */
  completion_tokens: number | null;
  reasoning_tokens: number | null;
  url: string;
}

export interface LlmRunList extends Paged<LlmRunItem> {}

export interface LlmRunDetail extends LlmRunItem {
  tool_calls: ToolCallSummary[];
  produced_forecasts: ProducedForecast[];
}

// --- 품질 집계 ---------------------------------------------------------------

export interface ForecastQualityRow {
  week_start: DayText;
  slot: string;
  llm_model: string;
  prompt_version: string;
  /** **모든 비율의 분모다.** */
  graded: number;
  pending: number;
  hits: number;
  hit_rate: number | null;
  beats_coin_flip: boolean | null;
  within_band: number;
  band_rate: number | null;
  mean_abs_error: number | null;
  /** **`mean_abs_error`보다 작으면 구조적으로 못 맞히는 폭이다.** */
  mean_band_pct: number | null;
  mean_expected_pct: number | null;
  weak: number;
  rejected_reasons: number;
}

export interface ReviewQualityRow {
  week_start: DayText;
  llm_model: string;
  prompt_version: string;
  runs: number;
  observations_written: number;
  mean_observations: number | null;
  memories_written: number;
  /** **0이 아니면 상한을 치고 있다.** */
  memories_rejected: number;
  memories_dropped: number;
  memories_expired: number;
  rejected: number;
  mean_tool_calls: number | null;
  truncated: number;
}

export interface QualityResponse {
  forecast: ForecastQualityRow[];
  review: ReviewQualityRow[];
  coin_flip_hit_rate: number;
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
  /**
   * 그 봉이 **확정**인가. 종목에만 있고 나머지 kind는 빈 배열이다. 국내 종목 분봉은
   * WebSocket 잠정 봉이 먼저 들어오고 REST가 나중에 덮는다 — **false면 고가·저가가 아직
   * 바뀔 수 있다.**
   */
  settled: boolean[];
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
  /**
   * 본문을 받아 봤는가, 못 받았다면 왜인가(ok·empty·attachment_only·unavailable).
   * **null은 아직 해 보지 않았다는 뜻이고 그 집합이 곧 수집 큐다.**
   */
  body_status: string | null;
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

/** 문서에 붙은 첨부 하나. **본문이 첨부에만 있는 출처가 있다.** */
export interface DocumentAttachmentItem {
  position: number;
  kind: string;
  url: string;
  filename: string | null;
  media_type: string | null;
  byte_size: number | null;
  /** 우리가 파일을 받아 뒀나. **저장 경로 자체는 안 온다.** */
  stored: boolean;
  fetched_at: UtcText | null;
}

export interface DocumentDetail extends DocumentSummary {
  body: string | null;
  summary: string | null;
  /**
   * LLM 응답 전체(세부 점수·주제·새 사실·판단 근거). **문자열이 아니라 객체다** —
   * 조회 조건이 굳으면 컬럼으로 빠질 값이라 모양을 고정하지 않는다.
   */
  assessment: Record<string, unknown> | null;
  detected_at: UtcText;
  content_hash: string;
  assessed_content_hash: string | null;
  attachments: DocumentAttachmentItem[];
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
  /** **순매수만으로는 거래 규모를 모른다.** 0이 "안 샀다"인지 "많이 사고 팔았다"인지 가른다. */
  foreign_sell_qty: number | null;
  foreign_buy_qty: number | null;
  institution_sell_qty: number | null;
  institution_buy_qty: number | null;
  individual_sell_qty: number | null;
  individual_buy_qty: number | null;
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
  /** 당일과 누적은 다른 값이다. 누적은 제공처가 정한 창의 합이라 더해 만들 수 없다. */
  accumulated_short_sale_quantity: number | null;
  accumulated_short_sale_volume_ratio: number | null;
  accumulated_short_sale_amount: number | null;
  accumulated_short_sale_amount_ratio: number | null;
  total_amount: number | null;
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
  price_change: number | null;
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
  /** **잔고만 보면 왜 늘었는지 모른다.** 신규·상환이 그 답이다. */
  loan_new_quantity: number | null;
  loan_repayment_quantity: number | null;
  loan_new_amount: number | null;
  loan_repayment_amount: number | null;
  loan_supply_rate: number | null;
  short_loan_new_quantity: number | null;
  short_loan_repayment_quantity: number | null;
  short_loan_new_amount: number | null;
  short_loan_repayment_amount: number | null;
  short_loan_supply_rate: number | null;
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
  short_loan_balance_growth_rate: number | null;
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
  mixed_fund_amount: number | null;
  securities_lending_amount: number | null;
  futures_margin_amount: number | null;
  index_change: number | null;
  market_capitalization: number | null;
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
  opinion_code: string | null;
  previous_opinion: string | null;
  previous_opinion_code: string | null;
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
  /** `source_metadata`가 있나. **내용은 안 온다** — 재현 정보라 목록의 일이 아니다. */
  has_metadata: boolean;
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
  kis_weekday_code: string | null;
  kis_settlement_day: boolean | null;
  /** **null이면 제공처 값을 그대로 믿고 있다.** */
  verified_at: UtcText | null;
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
