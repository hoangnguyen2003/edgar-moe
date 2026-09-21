from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import orjson
import pandas as pd

from edgar_moe.features.dataset import RECENT_DOWNLOAD_FAILURE_DAYS, ResearchDataset
from edgar_moe.forward.artifacts import ArtifactStore, ArtifactWriteError
from edgar_moe.forward.config import FrozenModelSpec
from edgar_moe.forward.domain import (
    DatasetRegistration,
    LabelDraft,
    ModelRegistration,
    QualityCheckDraft,
    RunRegistration,
)
from edgar_moe.forward.failure_context import safe_exception_message
from edgar_moe.forward.inference import ForecastBatch, ForecastQualityError, FrozenPredictor
from edgar_moe.forward.models import utc_now
from edgar_moe.forward.registry import ForwardRegistry
from edgar_moe.utils.hashing import sha256_file


class Predictor(Protocol):
    def forecast(
        self,
        dataset: ResearchDataset,
        *,
        as_of: datetime,
        device: str = "cpu",
    ) -> ForecastBatch: ...


@dataclass(frozen=True)
class WorkflowResult:
    run_id: str
    status: str
    counts: dict[str, int]
    artifact_uri: str


class ForwardWorkflow:
    """Orchestrates hash verification, inference, evidence writes, and settlement."""

    def __init__(
        self,
        registry: ForwardRegistry,
        artifact_store: ArtifactStore,
        model_spec: FrozenModelSpec,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.registry = registry
        self.artifact_store = artifact_store
        self.model_spec = model_spec
        self.clock = clock

    def forecast(
        self,
        dataset_dir: str | Path,
        *,
        as_of: datetime,
        code_revision: str,
        device: str = "cpu",
        predictor: Predictor | None = None,
    ) -> WorkflowResult:
        forecast_as_of = _aware_utc(as_of)
        _require_near_now(forecast_as_of, now=self.clock())
        dataset = ResearchDataset.load(dataset_dir)
        _require_dataset_not_future(dataset, as_of=forecast_as_of)
        locked = self._ensure_registrations(dataset, dataset_dir=dataset_dir)
        # Read before this run starts, so it is the previous opportunity to score.
        previous_forecast_as_of = self.registry.latest_forecast_as_of(
            model_id=self.model_spec.model_id
        )
        run = self.registry.start_run(
            RunRegistration(
                run_type="forecast",
                as_of=forecast_as_of,
                code_revision=code_revision,
                config_hash=self.model_spec.config_hash,
                dataset_id=dataset.dataset_id,
                model_id=self.model_spec.model_id,
                details={
                    "dataset_as_of": dataset.as_of.isoformat(),
                    "selection_hash": self.model_spec.selection_hash,
                    "locked_test_hash": self.model_spec.locked_test_hash,
                    "integrity_rule": "forecast_as_of precedes entry_at and horizon_at",
                },
            )
        )
        try:
            scorer = predictor or FrozenPredictor.load(
                self.model_spec.model_path,
                expected_sha256=self.model_spec.artifact_sha256,
                expected_selection_hash=self.model_spec.selection_hash,
                device=device,
            )
            batch = scorer.forecast(dataset, as_of=forecast_as_of, device=device)
            freshness_days = max((forecast_as_of.date() - dataset.as_of).days, 0)
            freshness_check = QualityCheckDraft(
                name="dataset_freshness_days",
                status="passed" if freshness_days <= 4 else "warning",
                observed_value=float(freshness_days),
                threshold=4.0,
                details={"dataset_as_of": dataset.as_of.isoformat()},
            )
            all_checks = [
                *batch.checks,
                freshness_check,
                *_source_coverage_checks(
                    dataset,
                    forecast_as_of=forecast_as_of,
                    previous_forecast_as_of=previous_forecast_as_of,
                ),
            ]
            quality_counts = self.registry.add_quality_checks(
                run.run_id, all_checks
            )
            forecast_counts = self.registry.append_forecasts(run.run_id, batch.forecasts)
            evidence = {
                "schema_version": 1,
                "run_id": run.run_id,
                "model_id": self.model_spec.model_id,
                "dataset_id": dataset.dataset_id,
                "forecast_as_of": forecast_as_of,
                "selection_hash": self.model_spec.selection_hash,
                "model_sha256": self.model_spec.artifact_sha256,
                "locked_test_hash": str(locked["locked_test_hash"]),
                "quality_checks": [item.model_dump(mode="json") for item in all_checks],
                "forecasts": [item.model_dump(mode="json") for item in batch.forecasts],
            }
            reference = self.artifact_store.put_bytes(
                _json_bytes(evidence), logical_name="forecast-batch.json"
            )
            self.registry.register_artifact(run.run_id, kind="forecast_batch", reference=reference)
            counts = {
                "forecasts_inserted": forecast_counts["inserted"],
                "forecasts_idempotent": forecast_counts["idempotent_skips"],
                "quality_checks_inserted": quality_counts["inserted"],
            }
            self.registry.complete_run(run.run_id, result_counts=counts)
            return WorkflowResult(
                run_id=run.run_id,
                status="succeeded",
                counts=counts,
                artifact_uri=reference.uri,
            )
        except ArtifactWriteError as error:
            self._record_partial_artifact(
                run.run_id,
                kind="forecast_batch",
                error=error,
            )
            raise
        except ForecastQualityError as error:
            self._record_failed_checks(run.run_id, error.checks)
            self.registry.fail_run(run.run_id, error_message=safe_exception_message(error))
            raise
        except Exception as error:
            self.registry.fail_run(run.run_id, error_message=safe_exception_message(error))
            raise

    def _record_failed_checks(self, run_id: str, checks: list[QualityCheckDraft]) -> None:
        """Keep the failed gate's checks with the failed run, without masking the failure."""
        try:
            self.registry.add_quality_checks(run_id, checks)
        except Exception:  # noqa: BLE001 - the quality failure is the error to report
            return

    def settle(
        self,
        dataset_dir: str | Path,
        *,
        as_of: datetime,
        code_revision: str,
    ) -> WorkflowResult:
        settlement_as_of = _aware_utc(as_of)
        _require_not_future(settlement_as_of, now=self.clock())
        dataset = ResearchDataset.load(dataset_dir)
        _require_dataset_not_future(dataset, as_of=settlement_as_of)
        self._ensure_registrations(dataset, dataset_dir=dataset_dir)
        run = self.registry.start_run(
            RunRegistration(
                run_type="settlement",
                as_of=settlement_as_of,
                code_revision=code_revision,
                config_hash=self.model_spec.config_hash,
                dataset_id=dataset.dataset_id,
                model_id=self.model_spec.model_id,
                details={"dataset_as_of": dataset.as_of.isoformat()},
            )
        )
        try:
            pending = self.registry.pending_forecasts(
                as_of=settlement_as_of, model_id=self.model_spec.model_id
            )
            event_positions = {
                str(event_id): position
                for position, event_id in enumerate(dataset.events["event_id"].astype(str))
            }
            labels: list[LabelDraft] = []
            unmatched: list[str] = []
            for item in pending:
                position = event_positions.get(str(item["event_id"]))
                if position is None or not math.isfinite(float(dataset.target[position])):
                    unmatched.append(str(item["forecast_id"]))
                    continue
                horizon_at = _timestamp(dataset.events.iloc[position]["horizon_at"])
                if horizon_at > settlement_as_of:
                    continue
                labels.append(
                    LabelDraft(
                        forecast_id=str(item["forecast_id"]),
                        source_dataset_id=dataset.dataset_id,
                        realized_abnormal_return=float(dataset.target[position]),
                        matured_at=horizon_at,
                    )
                )
            checks = [
                QualityCheckDraft(
                    name="settlement_match_rate",
                    status="passed" if not unmatched else "warning",
                    observed_value=(len(labels) / len(pending) if pending else 1.0),
                    threshold=1.0,
                    details={"due": len(pending), "unmatched_forecast_ids": unmatched[:20]},
                )
            ]
            quality_counts = self.registry.add_quality_checks(run.run_id, checks)
            label_counts = self.registry.settle_labels(run.run_id, labels)
            evidence = {
                "schema_version": 1,
                "run_id": run.run_id,
                "model_id": self.model_spec.model_id,
                "source_dataset_id": dataset.dataset_id,
                "settlement_as_of": settlement_as_of,
                "labels": [item.model_dump(mode="json") for item in labels],
                "unmatched_forecast_ids": unmatched,
            }
            reference = self.artifact_store.put_bytes(
                _json_bytes(evidence), logical_name="settlement-batch.json"
            )
            self.registry.register_artifact(
                run.run_id, kind="settlement_batch", reference=reference
            )
            counts = {
                "labels_inserted": label_counts["inserted"],
                "labels_idempotent": label_counts["idempotent_skips"],
                "unmatched": len(unmatched),
                "quality_checks_inserted": quality_counts["inserted"],
            }
            self.registry.complete_run(run.run_id, result_counts=counts)
            return WorkflowResult(
                run_id=run.run_id,
                status="succeeded",
                counts=counts,
                artifact_uri=reference.uri,
            )
        except ArtifactWriteError as error:
            self._record_partial_artifact(
                run.run_id,
                kind="settlement_batch",
                error=error,
            )
            raise
        except Exception as error:
            self.registry.fail_run(run.run_id, error_message=safe_exception_message(error))
            raise

    def _record_partial_artifact(
        self,
        run_id: str,
        *,
        kind: str,
        error: ArtifactWriteError,
    ) -> None:
        """Retain a verified primary after a mirror failure for safe repair.

        The registry row is written before the run is marked failed, so the
        read-only reconciler can discover and repair the primary bytes. If the
        registry write itself fails, the run still records a provider-neutral
        failure classification rather than pretending the evidence is durable.
        """

        try:
            self.registry.register_artifact(
                run_id,
                kind=kind,
                reference=error.primary_reference,
            )
        except Exception as registration_error:  # noqa: BLE001 - preserve original failure
            self.registry.fail_run(
                run_id,
                error_message=(
                    "ArtifactWriteError: mirror write failed and primary artifact "
                    "registration failed "
                    f"({type(registration_error).__name__})"
                ),
            )
            return
        self.registry.fail_run(
            run_id,
            error_message=(
                "ArtifactWriteError: mirror write failed; primary artifact was "
                "recorded for reconciliation "
                f"(cause={error.cause_type})"
            ),
        )

    def _ensure_registrations(
        self,
        dataset: ResearchDataset,
        *,
        dataset_dir: str | Path,
    ) -> dict[str, Any]:
        locked = self.model_spec.verify_locked_evidence()
        if not self.registry.has_dataset(self.model_spec.training_dataset_id):
            if not self.model_spec.training_dataset_dir.is_dir():
                raise FileNotFoundError(
                    "The frozen training dataset is not registered and its local bundle is "
                    f"unavailable: {self.model_spec.training_dataset_dir}"
                )
            training_dataset = ResearchDataset.load(self.model_spec.training_dataset_dir)
            self.registry.register_dataset(
                dataset_registration(training_dataset, self.model_spec.training_dataset_dir)
            )
        self.registry.register_dataset(dataset_registration(dataset, dataset_dir))
        reference = self.artifact_store.put_file(
            self.model_spec.model_path, logical_name="frozen-model.pt"
        )
        if reference.sha256 != self.model_spec.artifact_sha256:
            raise ValueError("Artifact store returned an unexpected frozen-model hash")
        champion_parameters = locked.get("champion_parameters", {})
        if not isinstance(champion_parameters, dict):
            raise ValueError("Locked result champion parameters must be a mapping")
        self.registry.register_model(
            ModelRegistration(
                model_id=self.model_spec.model_id,
                name=self.model_spec.name,
                version=self.model_spec.version,
                training_dataset_id=self.model_spec.training_dataset_id,
                selection_hash=self.model_spec.selection_hash,
                artifact_uri=reference.uri,
                artifact_sha256=reference.sha256,
                config_hash=self.model_spec.config_hash,
                champion_family=str(locked["champion_family"]),
                parameters=champion_parameters,
                frozen_at=self.model_spec.frozen_at,
            )
        )
        return locked


def dataset_registration(
    dataset: ResearchDataset,
    dataset_dir: str | Path,
) -> DatasetRegistration:
    root = Path(dataset_dir)
    manifest_path = root / "manifest.json"
    manifest = orjson.loads(manifest_path.read_bytes())
    raw_counts = manifest.get("row_counts", {})
    raw_provenance = manifest.get("provenance", {})
    return DatasetRegistration(
        dataset_id=dataset.dataset_id,
        as_of=dataset.as_of,
        source_manifest_hash=dataset.source_manifest_hash,
        manifest_sha256=sha256_file(manifest_path),
        row_counts={str(name): int(value) for name, value in raw_counts.items()},
        provenance={str(name): str(value) for name, value in raw_provenance.items()},
    )


def _source_coverage_checks(
    dataset: ResearchDataset,
    *,
    forecast_as_of: datetime,
    previous_forecast_as_of: datetime | None,
) -> list[QualityCheckDraft]:
    """Report forecastable filings the run could not score."""
    checks: list[QualityCheckDraft] = []
    recent_failures = dataset.attrition.get("recent_download_failures")
    if recent_failures is not None:
        checks.append(
            QualityCheckDraft(
                name="recent_filing_download_failures",
                status="warning" if recent_failures else "passed",
                observed_value=float(recent_failures),
                threshold=0.0,
                details={
                    "window_days": RECENT_DOWNLOAD_FAILURE_DAYS,
                    "effect": "filings that failed to download cannot be forecast",
                },
            )
        )
    events = dataset.events
    if previous_forecast_as_of is not None and {"accepted_at", "entry_at"}.issubset(
        events.columns
    ):
        accepted = pd.to_datetime(events["accepted_at"], utc=True)
        entry = pd.to_datetime(events["entry_at"], utc=True)
        missed = int(((accepted > previous_forecast_as_of) & (entry <= forecast_as_of)).sum())
        checks.append(
            QualityCheckDraft(
                # Informational: a once-daily pre-dawn run cannot score filings
                # accepted before the same morning's open (see docs/forward-testing.md).
                name="missed_before_entry",
                status="passed",
                observed_value=float(missed),
                details={
                    "rule": (
                        "accepted after the previous successful forecast run and "
                        "entered before this run"
                    ),
                    "previous_forecast_as_of": previous_forecast_as_of.isoformat(),
                },
            )
        )
    return checks


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return orjson.dumps(
        payload,
        option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS | orjson.OPT_UTC_Z,
    )


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Workflow as-of must be timezone-aware")
    return value.astimezone(UTC)


def _require_near_now(
    value: datetime,
    *,
    now: datetime,
    tolerance_seconds: int = 900,
) -> None:
    drift = abs((_aware_utc(now) - value).total_seconds())
    if drift > tolerance_seconds:
        raise ValueError(
            "Prospective forecast as-of must be within 15 minutes of the recording clock; "
            "backdated forecast runs are forbidden"
        )


def _require_not_future(
    value: datetime,
    *,
    now: datetime,
    tolerance_seconds: int = 900,
) -> None:
    if (value - _aware_utc(now)).total_seconds() > tolerance_seconds:
        raise ValueError("Settlement as-of cannot be in the future")


def _require_dataset_not_future(dataset: ResearchDataset, *, as_of: datetime) -> None:
    if dataset.as_of > as_of.date():
        raise ValueError("Dataset vintage cannot be later than the workflow as-of")


def _timestamp(value: Any) -> datetime:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    converted = timestamp.tz_convert("UTC").to_pydatetime()
    if not isinstance(converted, datetime):
        raise TypeError("Expected a scalar event timestamp")
    return converted
