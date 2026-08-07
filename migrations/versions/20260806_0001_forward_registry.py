"""Create the append-only forward-testing registry.

Revision ID: 20260806_0001
Revises:
Create Date: 2026-08-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260806_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "forward_datasets",
        sa.Column("dataset_id", sa.String(length=160), primary_key=True),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("source_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False, unique=True),
        sa.Column("row_counts", sa.JSON(), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_forward_datasets_as_of", "forward_datasets", ["as_of"])

    op.create_table(
        "forward_models",
        sa.Column("model_id", sa.String(length=160), primary_key=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("version", sa.String(length=80), nullable=False),
        sa.Column("training_dataset_id", sa.String(length=160), nullable=False),
        sa.Column("selection_hash", sa.String(length=64), nullable=False),
        sa.Column("artifact_uri", sa.Text(), nullable=False),
        sa.Column("artifact_sha256", sa.String(length=64), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("champion_family", sa.String(length=80), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["training_dataset_id"], ["forward_datasets.dataset_id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("selection_hash", "artifact_sha256", name="uq_forward_model_identity"),
    )

    op.create_table(
        "forward_runs",
        sa.Column("run_id", sa.String(length=160), primary_key=True),
        sa.Column("run_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("dataset_id", sa.String(length=160), nullable=True),
        sa.Column("model_id", sa.String(length=160), nullable=True),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("code_revision", sa.String(length=80), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("parent_run_id", sa.String(length=160), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("result_counts", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["dataset_id"], ["forward_datasets.dataset_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["model_id"], ["forward_models.model_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["parent_run_id"], ["forward_runs.run_id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_forward_runs_run_type", "forward_runs", ["run_type"])
    op.create_index("ix_forward_runs_status", "forward_runs", ["status"])
    op.create_index("ix_forward_runs_as_of", "forward_runs", ["as_of"])
    op.create_index("ix_forward_runs_model_as_of", "forward_runs", ["model_id", "as_of"])

    op.create_table(
        "forward_forecasts",
        sa.Column("forecast_id", sa.String(length=160), primary_key=True),
        sa.Column("run_id", sa.String(length=160), nullable=False),
        sa.Column("model_id", sa.String(length=160), nullable=False),
        sa.Column("dataset_id", sa.String(length=160), nullable=False),
        sa.Column("event_id", sa.String(length=200), nullable=False),
        sa.Column("accession_number", sa.String(length=32), nullable=False),
        sa.Column("security_id", sa.String(length=160), nullable=False),
        sa.Column("ticker", sa.String(length=32), nullable=False),
        sa.Column("company_name", sa.String(length=240), nullable=False),
        sa.Column("form", sa.String(length=16), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("horizon_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("industry_code", sa.String(length=32), nullable=False),
        sa.Column("forecast_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("rank", sa.Float(), nullable=False),
        sa.Column("fundamental_score", sa.Float(), nullable=True),
        sa.Column("expert_weights", sa.JSON(), nullable=False),
        sa.Column("expert_predictions", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["forward_runs.run_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["model_id"], ["forward_models.model_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["dataset_id"], ["forward_datasets.dataset_id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "model_id", "event_id", "forecast_as_of", name="uq_forward_forecast_observation"
        ),
    )
    op.create_index("ix_forward_forecasts_run_id", "forward_forecasts", ["run_id"])
    op.create_index("ix_forward_forecasts_model_id", "forward_forecasts", ["model_id"])
    op.create_index("ix_forward_forecasts_ticker", "forward_forecasts", ["ticker"])
    op.create_index("ix_forward_forecasts_horizon_at", "forward_forecasts", ["horizon_at"])
    op.create_index("ix_forward_forecasts_forecast_as_of", "forward_forecasts", ["forecast_as_of"])
    op.create_index(
        "ix_forward_forecasts_as_of_ticker", "forward_forecasts", ["forecast_as_of", "ticker"]
    )

    op.create_table(
        "forward_labels",
        sa.Column("label_id", sa.String(length=160), primary_key=True),
        sa.Column("forecast_id", sa.String(length=160), nullable=False, unique=True),
        sa.Column("settlement_run_id", sa.String(length=160), nullable=False),
        sa.Column("source_dataset_id", sa.String(length=160), nullable=False),
        sa.Column("realized_abnormal_return", sa.Float(), nullable=False),
        sa.Column("matured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["forecast_id"], ["forward_forecasts.forecast_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["settlement_run_id"], ["forward_runs.run_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_dataset_id"], ["forward_datasets.dataset_id"], ondelete="RESTRICT"
        ),
    )
    op.create_index("ix_forward_labels_settlement_run_id", "forward_labels", ["settlement_run_id"])

    op.create_table(
        "forward_artifacts",
        sa.Column("artifact_id", sa.String(length=160), primary_key=True),
        sa.Column("run_id", sa.String(length=160), nullable=False),
        sa.Column("kind", sa.String(length=80), nullable=False),
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["forward_runs.run_id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("run_id", "kind", "sha256", name="uq_forward_run_artifact"),
    )
    op.create_index("ix_forward_artifacts_run_id", "forward_artifacts", ["run_id"])

    op.create_table(
        "forward_data_quality_checks",
        sa.Column("check_id", sa.String(length=160), primary_key=True),
        sa.Column("run_id", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("observed_value", sa.Float(), nullable=True),
        sa.Column("threshold", sa.Float(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["forward_runs.run_id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("run_id", "name", name="uq_forward_run_check"),
    )
    op.create_index(
        "ix_forward_data_quality_checks_run_id", "forward_data_quality_checks", ["run_id"]
    )
    op.create_index(
        "ix_forward_data_quality_checks_status", "forward_data_quality_checks", ["status"]
    )

    op.create_table(
        "forward_audit_events",
        sa.Column("audit_id", sa.String(length=160), primary_key=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("entity_type", sa.String(length=80), nullable=False),
        sa.Column("entity_id", sa.String(length=200), nullable=False),
        sa.Column("actor", sa.String(length=160), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_forward_audit_events_action", "forward_audit_events", ["action"])
    op.create_index("ix_forward_audit_events_entity_id", "forward_audit_events", ["entity_id"])


def downgrade() -> None:
    op.drop_table("forward_audit_events")
    op.drop_table("forward_data_quality_checks")
    op.drop_table("forward_artifacts")
    op.drop_table("forward_labels")
    op.drop_table("forward_forecasts")
    op.drop_table("forward_runs")
    op.drop_table("forward_models")
    op.drop_table("forward_datasets")
