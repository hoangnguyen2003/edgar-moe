from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class DatasetRecord(Base):
    __tablename__ = "forward_datasets"

    dataset_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    as_of: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    row_counts: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class RegisteredModel(Base):
    __tablename__ = "forward_models"

    model_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[str] = mapped_column(String(80), nullable=False)
    training_dataset_id: Mapped[str] = mapped_column(
        ForeignKey("forward_datasets.dataset_id", ondelete="RESTRICT"), nullable=False
    )
    selection_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_uri: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    champion_family: Mapped[str] = mapped_column(String(80), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    frozen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (
        UniqueConstraint("selection_hash", "artifact_sha256", name="uq_forward_model_identity"),
    )


class RunRecord(Base):
    __tablename__ = "forward_runs"

    run_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    run_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    dataset_id: Mapped[str | None] = mapped_column(
        ForeignKey("forward_datasets.dataset_id", ondelete="RESTRICT"), nullable=True
    )
    model_id: Mapped[str | None] = mapped_column(
        ForeignKey("forward_models.model_id", ondelete="RESTRICT"), nullable=True
    )
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    code_revision: Mapped[str] = mapped_column(String(80), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("forward_runs.run_id", ondelete="RESTRICT"), nullable=True
    )
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    result_counts: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_forward_runs_model_as_of", "model_id", "as_of"),)


class ForecastRecord(Base):
    __tablename__ = "forward_forecasts"

    forecast_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("forward_runs.run_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    model_id: Mapped[str] = mapped_column(
        ForeignKey("forward_models.model_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    dataset_id: Mapped[str] = mapped_column(
        ForeignKey("forward_datasets.dataset_id", ondelete="RESTRICT"), nullable=False
    )
    event_id: Mapped[str] = mapped_column(String(200), nullable=False)
    accession_number: Mapped[str] = mapped_column(String(32), nullable=False)
    security_id: Mapped[str] = mapped_column(String(160), nullable=False)
    ticker: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    company_name: Mapped[str] = mapped_column(String(240), nullable=False)
    form: Mapped[str] = mapped_column(String(16), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    horizon_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    industry_code: Mapped[str] = mapped_column(String(32), nullable=False)
    forecast_as_of: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    score: Mapped[float] = mapped_column(Float, nullable=False)
    rank: Mapped[float] = mapped_column(Float, nullable=False)
    fundamental_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    expert_weights: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    expert_predictions: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (
        UniqueConstraint(
            "model_id",
            "event_id",
            "forecast_as_of",
            name="uq_forward_forecast_observation",
        ),
        Index("ix_forward_forecasts_as_of_ticker", "forecast_as_of", "ticker"),
    )


class LabelRecord(Base):
    __tablename__ = "forward_labels"

    label_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    forecast_id: Mapped[str] = mapped_column(
        ForeignKey("forward_forecasts.forecast_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    settlement_run_id: Mapped[str] = mapped_column(
        ForeignKey("forward_runs.run_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_dataset_id: Mapped[str] = mapped_column(
        ForeignKey("forward_datasets.dataset_id", ondelete="RESTRICT"), nullable=False
    )
    realized_abnormal_return: Mapped[float] = mapped_column(Float, nullable=False)
    matured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class ArtifactRecord(Base):
    __tablename__ = "forward_artifacts"

    artifact_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("forward_runs.run_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (UniqueConstraint("run_id", "kind", "sha256", name="uq_forward_run_artifact"),)


class DataQualityRecord(Base):
    __tablename__ = "forward_data_quality_checks"

    check_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("forward_runs.run_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    observed_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (UniqueConstraint("run_id", "name", name="uq_forward_run_check"),)


class AuditEvent(Base):
    __tablename__ = "forward_audit_events"

    audit_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(160), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


IMMUTABLE_RECORD_TYPES = (
    DatasetRecord,
    RegisteredModel,
    ForecastRecord,
    LabelRecord,
    ArtifactRecord,
    DataQualityRecord,
    AuditEvent,
)
