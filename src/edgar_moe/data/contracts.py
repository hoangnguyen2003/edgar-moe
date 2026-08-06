from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from edgar_moe.utils.time import ensure_utc


class FilingForm(StrEnum):
    TEN_K = "10-K"
    TEN_Q = "10-Q"


class MappingStatus(StrEnum):
    CONFIDENT = "confident"
    REVIEW = "review"
    EXCLUDED = "excluded"


class SecurityMapping(BaseModel):
    model_config = ConfigDict(frozen=True)

    security_id: str
    cik: str = Field(pattern=r"^\d{10}$")
    symbol: str
    company_name: str
    exchange: str
    valid_from: date | None = None
    valid_to: date | None = None
    active: bool = True
    confidence: float = Field(ge=0, le=1)
    status: MappingStatus
    evidence: list[str] = Field(default_factory=list)


class FilingEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    accession_number: str
    cik: str = Field(pattern=r"^\d{10}$")
    security_id: str
    ticker_at_event: str
    company_name: str
    form: FilingForm
    accepted_at: datetime
    report_period: date
    filing_url: str
    entry_at: datetime
    horizon_at: datetime
    industry_code: str

    @model_validator(mode="after")
    def validate_timing(self) -> FilingEvent:
        accepted = ensure_utc(self.accepted_at)
        entry = ensure_utc(self.entry_at)
        horizon = ensure_utc(self.horizon_at)
        if not accepted < entry < horizon:
            raise ValueError("Expected accepted_at < entry_at < horizon_at")
        return self


class FeatureValue(BaseModel):
    name: str
    value: float | None
    available_at: datetime
    source: str


class PointInTimeFeatureRow(BaseModel):
    event: FilingEvent
    numeric: dict[str, FeatureValue]
    text_embedding: list[float]
    text_available_at: datetime
    missing_modalities: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def reject_future_information(self) -> PointInTimeFeatureRow:
        cutoff = ensure_utc(self.event.accepted_at)
        violations = [
            name for name, item in self.numeric.items() if ensure_utc(item.available_at) > cutoff
        ]
        if ensure_utc(self.text_available_at) > cutoff:
            violations.append("text_embedding")
        if violations:
            raise ValueError(f"Features available after prediction cutoff: {violations}")
        return self


class PredictionRecord(BaseModel):
    accession_number: str
    security_id: str
    ticker: str
    prediction_at: datetime
    horizon_at: datetime
    score: float
    rank: float = Field(ge=0, le=1)
    expert_weights: dict[str, float]
    top_attributions: list[dict[str, float | str]] = Field(default_factory=list)
    realized_abnormal_return: float | None = None


class PortfolioPosition(BaseModel):
    date: date
    security_id: str
    ticker: str
    weight: float
    score: float
    beta: float
    industry_code: str


class DatasetManifest(BaseModel):
    dataset_id: str
    created_at: datetime
    source_versions: dict[str, str]
    row_counts: dict[str, int]
    hashes: dict[str, str]
    configuration: dict[str, Any]
