export interface SnapshotMetadata {
  project: string;
  version: string;
  generated_at: string;
  as_of: string;
  data_mode: string;
  research_only: boolean;
  disclaimer: string;
  selection_hash?: string | null;
  locked_test_hash?: string | null;
  opening_attempt?: number | null;
  recovery_note?: string | null;
}

export interface ResearchSummary {
  title: string;
  thesis: string;
  universe: string;
  horizon_sessions: number;
  events: number;
  issuers: number;
  development_events: number;
  validation_events: number;
  test_events: number;
  latest_signal_count: number;
}

export interface SummaryResponse {
  metadata: SnapshotMetadata;
  summary: ResearchSummary;
  predictive_metrics: Record<string, Record<string, number | null>>;
  portfolio_scenarios: Array<Record<string, number | null>>;
}

export interface ExperimentRecord {
  name: string;
  family: string;
  validation_rmse: number;
  validation_rank_ic?: number | null;
  selected: boolean;
  best_epoch?: number | null;
}

export interface EventRecord {
  accession_number: string;
  event_id: string;
  security_id: string;
  ticker: string;
  company_name: string;
  form: "10-K" | "10-Q";
  accepted_at: string;
  entry_date: string;
  horizon_date: string;
  industry_code: string;
  score: number;
  rank: number;
  direction: "long" | "short" | "neutral";
  expert_weights: { text: number; fundamental: number; market: number };
  top_attributions: Array<{ feature: string; contribution: number }>;
  realized_abnormal_return: number | null;
  filing_url: string;
}

export interface EventPage {
  items: EventRecord[];
  next_cursor: string | null;
  total: number;
}

export interface EquityPoint {
  date: string;
  equity: number;
  drawdown: number;
  turnover: number;
}

export interface EquityCurveResponse {
  cost_bps: number;
  points: EquityPoint[];
  metrics: Record<string, number | null>;
}

export interface MethodologyResponse {
  target: string;
  split: string;
  model: string;
  portfolio: string;
  costs: string;
  limitations: string[];
}

export interface FreshnessResponse {
  status: string;
  last_successful_update: string;
  next_scheduled_update: string | null;
  message: string;
}

export interface ForwardStatusResponse {
  configured: boolean;
  available: boolean;
  model_count: number;
  run_count: number;
  forecast_count: number;
  matured_count: number;
  pending_count: number;
  latest_successful_run_at: string | null;
  message: string;
}

export interface ForwardRunRecord {
  run_id: string;
  run_type: "forecast" | "settlement" | "backfill" | "verification";
  status: "running" | "succeeded" | "failed";
  dataset_id: string | null;
  model_id: string | null;
  as_of: string;
  code_revision: string;
  result_counts: Record<string, number>;
  error_message: string | null;
  started_at: string;
  finished_at: string | null;
}

export interface ForwardForecastRecord {
  forecast_id: string;
  run_id: string;
  model_id: string;
  event_id: string;
  accession_number: string;
  ticker: string;
  company_name: string;
  form: string;
  accepted_at: string;
  entry_at: string;
  entry_date: string;
  horizon_at: string;
  forecast_as_of: string;
  score: number;
  rank: number;
  fundamental_score: number | null;
  expert_weights: Record<string, number>;
  realized_abnormal_return: number | null;
  label_recorded_at: string | null;
}

export interface ForwardForecastPage {
  items: ForwardForecastRecord[];
  total: number;
  offset: number;
  limit: number;
}

export interface ForwardPerformanceResponse {
  model_id: string | null;
  forecast_count: number;
  matured_count: number;
  pending_count: number;
  coverage: number;
  rank_ic: number | null;
  rmse: number | null;
  mae: number | null;
  directional_accuracy: number | null;
}

export interface ForwardQualityRecord {
  check_id: string;
  run_id: string;
  name: string;
  status: "passed" | "warning" | "failed";
  observed_value: number | null;
  threshold: number | null;
  details: Record<string, unknown>;
  created_at: string;
}
