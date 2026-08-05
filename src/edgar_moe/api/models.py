from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class SnapshotMetadata(BaseModel):
    project: str
    version: str
    generated_at: datetime
    as_of: date
    data_mode: str
    research_only: bool
    disclaimer: str


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


class ErrorResponse(BaseModel):
    detail: str
    context: dict[str, Any] | None = None
