from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SnapshotMetadata(BaseModel):
    project: str
    version: str
    generated_at: datetime
    as_of: date
    data_mode: str
    research_only: bool
    disclaimer: str
    selection_hash: str | None = None
    locked_test_hash: str | None = None
    opening_attempt: int | None = None
    recovery_note: str | None = None


class ResearchSummary(BaseModel):
    title: str
    thesis: str
    universe: str
    horizon_sessions: int
    events: int
    issuers: int
    development_events: int
    validation_events: int
    test_events: int
    latest_signal_count: int


class SummaryResponse(BaseModel):
    metadata: SnapshotMetadata
    summary: ResearchSummary
    predictive_metrics: dict[str, dict[str, float | None]]
    portfolio_scenarios: list[dict[str, float | None]]


class RankICInterval(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    low: float
    high: float
    method: Literal["two_calendar_month_moving_block"]
    calendar_months: int = Field(ge=1)
    resamples: int = Field(ge=100)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class FrozenUniverseSelectionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["retrospective_test_period_screen"]
    screen_as_of: date
    first_validation_start: date
    locked_test_start: date
    interpretation: str


class FrozenResearchEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    status: Literal["frozen_locked_test"]
    dataset_id: str
    as_of: date
    selection_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    locked_test_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    total_events: int = Field(ge=1)
    validation_events: int = Field(ge=0)
    locked_test_events: int = Field(ge=1)
    locked_rank_ic: float
    locked_rank_ic_interval_95: RankICInterval
    portfolio_10bps_sharpe: float
    candidate_universe: FrozenUniverseSelectionEvidence
    interpretation: str


class V2PendingEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["pending_review"]
    reason: str


class V2ComparisonEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    baseline: str
    rank_ic_delta: float
    interval_status: Literal["ready", "insufficient_calendar_months", "degenerate_resamples"]
    interval_low: float | None
    interval_high: float | None


class V2CostScenario(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    model: str
    cost_bps: Literal[10, 25, 50]
    sharpe: float | None
    annualized_return: float | None


class V2ReviewedEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    status: Literal["reviewed_pretest"]
    dataset_id: str
    source_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selection_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_universe_status: Literal["historical_membership_unverified"]
    oof_events: int = Field(ge=1)
    champion_name: str
    champion_weighted_rank_ic: float
    uncertainty_method: Literal["paired_calendar_month_moving_block_within_fold"]
    block_months: int = Field(ge=1)
    bootstrap_resamples: int = Field(ge=100)
    comparisons: list[V2ComparisonEvidence]
    portfolio_status: Literal[
        "development_only",
        "unavailable_no_prelocked_signals",
        "unavailable_return_calendar",
        "unavailable_incomplete_return_coverage",
    ]
    cost_scenarios: list[V2CostScenario]
    cost_definition: str | None
    approval_reference: str
    interpretation: str


class ResearchEvidenceResponse(BaseModel):
    schema_version: Literal[1]
    catalog_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    frozen_v1: FrozenResearchEvidence
    duration_aware_v2: V2PendingEvidence | V2ReviewedEvidence


class ExperimentRecord(BaseModel):
    name: str
    family: str
    validation_rmse: float
    validation_rank_ic: float | None = None
    selected: bool
    best_epoch: int | None = None


class ExpertWeights(BaseModel):
    text: float
    fundamental: float
    market: float


class Attribution(BaseModel):
    feature: str
    contribution: float


class EventRecord(BaseModel):
    accession_number: str
    event_id: str
    security_id: str
    ticker: str
    company_name: str
    form: Literal["10-K", "10-Q"]
    accepted_at: datetime
    entry_date: date
    horizon_date: date
    industry_code: str
    score: float
    rank: float = Field(ge=0, le=1)
    direction: Literal["long", "short", "neutral"]
    expert_weights: ExpertWeights
    top_attributions: list[Attribution]
    realized_abnormal_return: float | None
    filing_url: str


class EventPage(BaseModel):
    items: list[EventRecord]
    next_cursor: str | None
    total: int


class EquityPoint(BaseModel):
    date: date
    equity: float
    drawdown: float
    turnover: float


class EquityCurveResponse(BaseModel):
    cost_bps: int
    points: list[EquityPoint]
    metrics: dict[str, float | None]


class MethodologyResponse(BaseModel):
    target: str
    split: str
    model: str
    portfolio: str
    costs: str
    limitations: list[str]


class FreshnessResponse(BaseModel):
    status: str
    last_successful_update: datetime
    next_scheduled_update: datetime | None
    message: str


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    snapshot_loaded: bool
    data_mode: str | None
    as_of: date | None
    commit_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")


class FrozenSnapshotIdentity(BaseModel):
    path: Literal["data/demo/snapshot.json"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    data_mode: Literal["authenticated_locked_test"]
    as_of: date
    selection_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    locked_test_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    research_only: Literal[True]


class PublicDataBoundary(BaseModel):
    raw_sources_public: Literal[False]
    derived_output_public: Literal[True]
    redistribution_status: Literal["operator_review_required"]


class GovernanceControl(BaseModel):
    key: str
    status: Literal["enforced", "pending_operator_evidence"]
    owner: Literal["repository", "operator"]
    summary: str


class ForwardStatusResponse(BaseModel):
    configured: bool
    available: bool
    model_count: int = 0
    run_count: int = 0
    forecast_count: int = 0
    matured_count: int = 0
    pending_count: int = 0
    latest_successful_run_at: datetime | None = None
    health_status: Literal["ok", "warning", "degraded"] = "degraded"
    health_message: str | None = None
    latest_run_at: datetime | None = None
    latest_run_status: Literal["running", "succeeded", "failed"] | None = None
    latest_cycle_forecast_status: Literal["running", "succeeded", "failed"] | None = None
    latest_failed_run_at: datetime | None = None
    age_seconds: int | None = Field(default=None, ge=0)
    stale_after_seconds: int = Field(default=96 * 60 * 60, ge=0)
    running_run_count: int = Field(default=0, ge=0)
    latest_quality_warnings: int = Field(default=0, ge=0)
    #: Names of the checks that warned on the latest run, so a reader can tell
    #: an expected condition from a new problem.
    latest_quality_warning_names: list[str] = Field(default_factory=list)
    latest_quality_failures: int = Field(default=0, ge=0)
    message: str


class GovernanceResponse(BaseModel):
    schema_version: Literal[1]
    frozen_v1: FrozenSnapshotIdentity
    public_data: PublicDataBoundary
    controls: list[GovernanceControl]
    forward_status: ForwardStatusResponse


class ForwardRunRecord(BaseModel):
    run_id: str
    run_type: Literal["forecast", "settlement", "backfill", "verification"]
    status: Literal["running", "succeeded", "failed"]
    dataset_id: str | None
    model_id: str | None
    as_of: datetime
    code_revision: str
    result_counts: dict[str, int]
    error_message: str | None
    started_at: datetime
    finished_at: datetime | None


class ForwardForecastRecord(BaseModel):
    forecast_id: str
    run_id: str
    model_id: str
    event_id: str
    accession_number: str
    security_id: str
    ticker: str
    company_name: str
    form: str
    accepted_at: datetime
    entry_at: datetime
    entry_date: date
    horizon_at: datetime
    forecast_as_of: datetime
    score: float
    rank: float = Field(ge=0, le=1)
    # Forecasts recorded in the same run; ``rank`` is a percentile within this cohort.
    cohort_size: int = Field(ge=1)
    fundamental_score: float | None
    expert_weights: dict[str, float]
    realized_abnormal_return: float | None
    label_recorded_at: datetime | None


class ForwardForecastPage(BaseModel):
    items: list[ForwardForecastRecord]
    total: int
    offset: int
    limit: int


class ForwardPerformanceResponse(BaseModel):
    model_id: str | None
    forecast_count: int
    matured_count: int
    pending_count: int
    coverage: float = Field(ge=0, le=1)
    rank_ic: float | None
    #: Conditional 95% calendar-month moving-block interval. Null until enough
    #: settled outcomes and distinct months exist, or when the computation is
    #: undefined or outside its reviewed capacity envelope.
    rank_ic_low: float | None = Field(default=None, ge=-1, le=1)
    rank_ic_high: float | None = Field(default=None, ge=-1, le=1)
    rank_ic_interval_method: Literal["calendar_month_moving_block"] | None = None
    rank_ic_interval_status: (
        Literal[
            "ready",
            "insufficient_pairs",
            "insufficient_months",
            "undefined_rank_ic",
            "degenerate_resamples",
            "capacity_review_required",
        ]
        | None
    ) = None
    rank_ic_calendar_months: int = Field(default=0, ge=0)
    rank_ic_block_months: int | None = Field(default=None, ge=1)
    rank_ic_bootstrap_samples: int | None = Field(default=None, ge=1)
    rmse: float | None
    mae: float | None
    directional_accuracy: float | None


class ForwardQualityRecord(BaseModel):
    check_id: str
    run_id: str
    name: str
    status: Literal["passed", "warning", "failed"]
    observed_value: float | None
    threshold: float | None
    details: dict[str, Any]
    created_at: datetime


class ErrorResponse(BaseModel):
    detail: str
    context: dict[str, Any] | None = None
