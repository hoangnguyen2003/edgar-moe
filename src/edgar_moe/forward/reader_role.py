"""Shared contract for the hosted API/evidence-auditor reader role."""

from __future__ import annotations

READER_TABLES: tuple[str, ...] = (
    "forward_datasets",
    "forward_models",
    "forward_runs",
    "forward_forecasts",
    "forward_labels",
    "forward_artifacts",
    "forward_data_quality_checks",
    "forward_audit_events",
)
FORBIDDEN_TABLE_PRIVILEGES: tuple[str, ...] = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)
