from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class DatasetRegistration(StrictRecord):
    dataset_id: str = Field(min_length=1, max_length=160)
    as_of: date
    source_manifest_hash: str = Field(min_length=64, max_length=64)
    manifest_sha256: str = Field(min_length=64, max_length=64)
    row_counts: dict[str, int]
    provenance: dict[str, str] = Field(default_factory=dict)


class ModelRegistration(StrictRecord):
    model_id: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=160)
    version: str = Field(min_length=1, max_length=80)
    training_dataset_id: str
    selection_hash: str = Field(min_length=64, max_length=64)
    artifact_uri: str = Field(min_length=1)
    artifact_sha256: str = Field(min_length=64, max_length=64)
    config_hash: str = Field(min_length=64, max_length=64)
    champion_family: str = Field(min_length=1, max_length=80)
    parameters: dict[str, Any] = Field(default_factory=dict)
    frozen_at: datetime

    @field_validator("frozen_at")
    @classmethod
    def require_aware_frozen_at(cls, value: datetime) -> datetime:
        return _aware_utc(value, "frozen_at")


class RunRegistration(StrictRecord):
    run_type: Literal["forecast", "settlement", "backfill", "verification"]
    as_of: datetime
    code_revision: str = Field(min_length=1, max_length=80)
    config_hash: str = Field(min_length=64, max_length=64)
    dataset_id: str | None = None
    model_id: str | None = None
    parent_run_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("as_of")
    @classmethod
    def require_aware_as_of(cls, value: datetime) -> datetime:
        return _aware_utc(value, "as_of")


class ForecastDraft(StrictRecord):
    event_id: str = Field(min_length=1, max_length=200)
    accession_number: str = Field(min_length=1, max_length=32)
    security_id: str = Field(min_length=1, max_length=160)
    ticker: str = Field(min_length=1, max_length=32)
    company_name: str = Field(min_length=1, max_length=240)
    form: str = Field(min_length=1, max_length=16)
    accepted_at: datetime
    entry_at: datetime
    entry_date: date
    horizon_at: datetime
    industry_code: str = Field(max_length=32)
    score: float
    rank: float = Field(ge=0.0, le=1.0)
    fundamental_score: float | None = None
    expert_weights: dict[str, float] = Field(default_factory=dict)
    expert_predictions: dict[str, float] = Field(default_factory=dict)

    @field_validator("accepted_at", "entry_at", "horizon_at")
    @classmethod
    def require_aware_event_times(cls, value: datetime) -> datetime:
        return _aware_utc(value, "event timestamp")


class LabelDraft(StrictRecord):
    forecast_id: str
    source_dataset_id: str
    realized_abnormal_return: float
    matured_at: datetime

    @field_validator("matured_at")
    @classmethod
    def require_aware_maturity(cls, value: datetime) -> datetime:
        return _aware_utc(value, "matured_at")


class QualityCheckDraft(StrictRecord):
    name: str = Field(min_length=1, max_length=160)
    status: Literal["passed", "warning", "failed"]
    observed_value: float | None = None
    threshold: float | None = None
    details: dict[str, Any] = Field(default_factory=dict)


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)
