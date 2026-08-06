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
