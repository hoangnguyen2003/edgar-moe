from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import orjson
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from edgar_moe.forward.artifacts import ArtifactReference
from edgar_moe.forward.database import RegistryDatabase
from edgar_moe.forward.domain import (
    DatasetRegistration,
    ForecastDraft,
    LabelDraft,
    ModelRegistration,
    QualityCheckDraft,
    RunRegistration,
)
from edgar_moe.forward.failure_context import redact_sensitive_text
from edgar_moe.forward.metrics import forward_metrics
from edgar_moe.forward.models import (
    ArtifactRecord,
    AuditEvent,
    DataQualityRecord,
    DatasetRecord,
    ForecastRecord,
    LabelRecord,
    RegisteredModel,
    RunRecord,
    utc_now,
)
from edgar_moe.forward.uncertainty import clustered_rank_ic_interval

_MAX_PAGE_OFFSET = 1_000_000


class RegistryConflictError(RuntimeError):
    """Raised when an idempotency key is reused with different evidence."""


class RegistryStateError(RuntimeError):
    """Raised when a registry operation violates the run state machine."""


class ForwardRegistry:
    """Transactional service for prospective forecasts and immutable outcomes."""

    # The scheduled runner is intentionally paused on Sunday and Monday. Four days
    # therefore leaves room for the weekend gap while still flagging a missed cycle.
    DEFAULT_STALE_AFTER = timedelta(hours=96)

    def __init__(self, database: RegistryDatabase, *, actor: str = "edgar-moe") -> None:
        self.database = database
        self.actor = actor

    def register_dataset(self, registration: DatasetRegistration) -> DatasetRecord:
        payload = registration.model_dump(mode="json")
        payload_hash = _payload_hash(payload)
        with self.database.session() as session:
            existing = session.get(DatasetRecord, registration.dataset_id)
            if existing is not None:
                _assert_same(
                    existing.payload_hash, payload_hash, "dataset", registration.dataset_id
                )
                return existing
            record = DatasetRecord(
                **registration.model_dump(),
                payload_hash=payload_hash,
            )
            session.add(record)
            _audit(
                session,
                actor=self.actor,
                action="dataset.registered",
                entity_type="dataset",
                entity_id=registration.dataset_id,
                payload={"manifest_sha256": registration.manifest_sha256},
            )
            return record

    def has_dataset(self, dataset_id: str) -> bool:
        """Return whether an immutable dataset identity is already registered."""
        with self.database.session() as session:
            return session.get(DatasetRecord, dataset_id) is not None

    def register_model(self, registration: ModelRegistration) -> RegisteredModel:
        payload = registration.model_dump(mode="json")
        payload_hash = _payload_hash(payload)
        with self.database.session() as session:
            if session.get(DatasetRecord, registration.training_dataset_id) is None:
                raise RegistryStateError(
                    f"Training dataset is not registered: {registration.training_dataset_id}"
                )
            existing = session.get(RegisteredModel, registration.model_id)
            if existing is not None:
                _assert_same(existing.payload_hash, payload_hash, "model", registration.model_id)
                return existing
            record = RegisteredModel(
                **registration.model_dump(),
                payload_hash=payload_hash,
            )
            session.add(record)
            _audit(
                session,
                actor=self.actor,
                action="model.registered",
                entity_type="model",
                entity_id=registration.model_id,
                payload={
                    "selection_hash": registration.selection_hash,
                    "artifact_sha256": registration.artifact_sha256,
                },
            )
            return record

    def start_run(self, registration: RunRegistration, *, run_id: str | None = None) -> RunRecord:
        resolved_run_id = run_id or _new_id("run")
        with self.database.session() as session:
            if session.get(RunRecord, resolved_run_id) is not None:
                raise RegistryConflictError(f"Run already exists: {resolved_run_id}")
            _require_optional_reference(session, DatasetRecord, registration.dataset_id, "dataset")
            _require_optional_reference(session, RegisteredModel, registration.model_id, "model")
            _require_optional_reference(
                session, RunRecord, registration.parent_run_id, "parent run"
            )
            record = RunRecord(
                run_id=resolved_run_id,
                status="running",
                **registration.model_dump(),
            )
            session.add(record)
            _audit(
                session,
                actor=self.actor,
                action="run.started",
                entity_type="run",
                entity_id=resolved_run_id,
                payload={"run_type": registration.run_type, "as_of": registration.as_of},
            )
            return record

    def complete_run(self, run_id: str, *, result_counts: dict[str, int]) -> RunRecord:
        with self.database.session() as session:
            run = _require_running_run(session, run_id)
            run.status = "succeeded"
            run.result_counts = dict(result_counts)
            run.finished_at = utc_now()
            _audit(
                session,
                actor=self.actor,
                action="run.succeeded",
                entity_type="run",
                entity_id=run_id,
                payload={"result_counts": result_counts},
            )
            return run

    def fail_run(self, run_id: str, *, error_message: str) -> RunRecord:
        with self.database.session() as session:
            run = _require_running_run(session, run_id)
            run.status = "failed"
            run.error_message = redact_sensitive_text(error_message, max_length=4_000)
            run.finished_at = utc_now()
            _audit(
                session,
                actor=self.actor,
                action="run.failed",
                entity_type="run",
                entity_id=run_id,
                payload={"error_message": run.error_message},
            )
            return run

    def append_forecasts(
        self,
        run_id: str,
        forecasts: Sequence[ForecastDraft],
    ) -> dict[str, int]:
        inserted = 0
        skipped = 0
        with self.database.session() as session:
            run = _require_running_run(session, run_id, expected_type="forecast")
            if run.model_id is None or run.dataset_id is None:
                raise RegistryStateError("Forecast runs require a registered model and dataset")
            forecast_as_of = _as_utc(run.as_of)
            for draft in forecasts:
                if draft.accepted_at > forecast_as_of:
                    raise RegistryStateError(
                        f"Forecast event {draft.event_id} was not available at run time"
                    )
                if draft.entry_at <= forecast_as_of:
                    raise RegistryStateError(
                        f"Forecast event {draft.event_id} was no longer tradable at run time"
                    )
                if draft.horizon_at <= forecast_as_of:
                    raise RegistryStateError(
                        f"Forecast event {draft.event_id} was already mature at run time"
                    )
                identity = {
                    "model_id": run.model_id,
                    "dataset_id": run.dataset_id,
                    "event_id": draft.event_id,
                    "forecast_as_of": forecast_as_of,
                    "forecast": draft.model_dump(mode="json"),
                }
                payload_hash = _payload_hash(identity)
                existing = session.scalar(
                    select(ForecastRecord).where(
                        ForecastRecord.model_id == run.model_id,
                        ForecastRecord.event_id == draft.event_id,
                        ForecastRecord.forecast_as_of == forecast_as_of,
                    )
                )
                if existing is not None:
                    _assert_same(
                        existing.payload_hash, payload_hash, "forecast", existing.forecast_id
                    )
                    skipped += 1
                    continue
                forecast_id = _stable_id(
                    "forecast", run.model_id, draft.event_id, forecast_as_of.isoformat()
                )
                session.add(
                    ForecastRecord(
                        forecast_id=forecast_id,
                        run_id=run_id,
                        model_id=run.model_id,
                        dataset_id=run.dataset_id,
                        forecast_as_of=forecast_as_of,
                        payload_hash=payload_hash,
                        **draft.model_dump(),
                    )
                )
                inserted += 1
            _audit(
                session,
                actor=self.actor,
                action="forecast.batch_appended",
                entity_type="run",
                entity_id=run_id,
                payload={"inserted": inserted, "idempotent_skips": skipped},
            )
        return {"inserted": inserted, "idempotent_skips": skipped}

    def settle_labels(self, run_id: str, labels: Sequence[LabelDraft]) -> dict[str, int]:
        inserted = 0
        skipped = 0
        with self.database.session() as session:
            run = _require_running_run(session, run_id, expected_type="settlement")
            settlement_as_of = _as_utc(run.as_of)
            for draft in labels:
                forecast = session.get(ForecastRecord, draft.forecast_id)
                if forecast is None:
                    raise RegistryStateError(f"Forecast is not registered: {draft.forecast_id}")
                if _as_utc(forecast.horizon_at) > settlement_as_of:
                    raise RegistryStateError(f"Forecast is not mature: {draft.forecast_id}")
                if draft.matured_at > settlement_as_of:
                    raise RegistryStateError(
                        f"Label maturity is after settlement as-of: {draft.forecast_id}"
                    )
                if session.get(DatasetRecord, draft.source_dataset_id) is None:
                    raise RegistryStateError(
                        f"Label source dataset is not registered: {draft.source_dataset_id}"
                    )
                identity = draft.model_dump(mode="json")
                payload_hash = _payload_hash(identity)
                existing = session.scalar(
                    select(LabelRecord).where(LabelRecord.forecast_id == draft.forecast_id)
                )
                if existing is not None:
                    _assert_same(existing.payload_hash, payload_hash, "label", existing.label_id)
                    skipped += 1
                    continue
                session.add(
                    LabelRecord(
                        label_id=_stable_id("label", draft.forecast_id),
                        settlement_run_id=run_id,
                        payload_hash=payload_hash,
                        **draft.model_dump(),
                    )
                )
                inserted += 1
            _audit(
                session,
                actor=self.actor,
                action="label.batch_appended",
                entity_type="run",
                entity_id=run_id,
                payload={"inserted": inserted, "idempotent_skips": skipped},
            )
        return {"inserted": inserted, "idempotent_skips": skipped}

    def add_quality_checks(
        self,
        run_id: str,
        checks: Sequence[QualityCheckDraft],
    ) -> dict[str, int]:
        inserted = 0
        skipped = 0
        with self.database.session() as session:
            _require_running_run(session, run_id)
            for draft in checks:
                payload_hash = _payload_hash(draft.model_dump(mode="json"))
                existing = session.scalar(
                    select(DataQualityRecord).where(
                        DataQualityRecord.run_id == run_id,
                        DataQualityRecord.name == draft.name,
                    )
                )
                if existing is not None:
                    _assert_same(
                        existing.payload_hash, payload_hash, "quality check", existing.check_id
                    )
                    skipped += 1
                    continue
                session.add(
                    DataQualityRecord(
                        check_id=_stable_id("check", run_id, draft.name),
                        run_id=run_id,
                        payload_hash=payload_hash,
                        **draft.model_dump(),
                    )
                )
                inserted += 1
            _audit(
                session,
                actor=self.actor,
                action="quality.batch_appended",
                entity_type="run",
                entity_id=run_id,
                payload={"inserted": inserted, "idempotent_skips": skipped},
            )
        return {"inserted": inserted, "idempotent_skips": skipped}

    def register_artifact(
        self,
        run_id: str,
        *,
        kind: str,
        reference: ArtifactReference,
    ) -> ArtifactRecord:
        identity = {
            "run_id": run_id,
            "kind": kind,
            "uri": reference.uri,
            "sha256": reference.sha256,
            "size_bytes": reference.size_bytes,
        }
        payload_hash = _payload_hash(identity)
        artifact_id = _stable_id("artifact", run_id, kind, reference.sha256)
        with self.database.session() as session:
            if session.get(RunRecord, run_id) is None:
                raise RegistryStateError(f"Run is not registered: {run_id}")
            existing = session.get(ArtifactRecord, artifact_id)
            if existing is not None:
                _assert_same(existing.payload_hash, payload_hash, "artifact", artifact_id)
                return existing
            record = ArtifactRecord(
                artifact_id=artifact_id,
                payload_hash=payload_hash,
                **identity,
            )
            session.add(record)
            _audit(
                session,
                actor=self.actor,
                action="artifact.registered",
                entity_type="artifact",
                entity_id=artifact_id,
                payload={"run_id": run_id, "kind": kind, "sha256": reference.sha256},
            )
            return record

    def list_artifacts(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        """Return immutable artifact references for independent verification."""
        statement = select(ArtifactRecord).order_by(
            ArtifactRecord.created_at, ArtifactRecord.artifact_id
        )
        if limit is not None:
            if limit < 1:
                raise ValueError("Artifact limit must be positive")
            statement = statement.limit(limit)
        with self.database.session() as session:
            records = session.scalars(statement).all()
            return [
                {
                    "artifact_id": row.artifact_id,
                    "run_id": row.run_id,
                    "kind": row.kind,
                    "uri": row.uri,
                    "sha256": row.sha256,
                    "size_bytes": row.size_bytes,
                }
                for row in records
            ]

    def status(
        self,
        *,
        now: datetime | None = None,
        stale_after: timedelta = DEFAULT_STALE_AFTER,
    ) -> dict[str, Any]:
        """Return coverage plus operational health for the prospective runner.

        ``stale_after`` is deliberately configurable for deterministic tests and
        operator-specific schedules. The default accommodates the Tuesday–Saturday
        production cadence without masking a missed multi-day run.
        """
        observed_at = _as_utc(now or utc_now())
        with self.database.session() as session:
            forecast_count = int(
                session.scalar(select(func.count(ForecastRecord.forecast_id))) or 0
            )
            matured_count = int(session.scalar(select(func.count(LabelRecord.label_id))) or 0)
            model_count = int(session.scalar(select(func.count(RegisteredModel.model_id))) or 0)
            run_count = int(session.scalar(select(func.count(RunRecord.run_id))) or 0)
            latest = session.scalar(
                select(RunRecord)
                .where(RunRecord.status == "succeeded")
                .order_by(RunRecord.finished_at.desc())
                .limit(1)
            )
            latest_run = session.scalar(
                select(RunRecord).order_by(RunRecord.started_at.desc()).limit(1)
            )
            latest_failed = session.scalar(
                select(RunRecord)
                .where(RunRecord.status == "failed")
                .order_by(RunRecord.finished_at.desc())
                .limit(1)
            )
            latest_quality_rows = (
                session.execute(
                    select(DataQualityRecord.status, DataQualityRecord.name).where(
                        DataQualityRecord.run_id == latest_run.run_id
                    )
                ).all()
                if latest_run is not None
                else []
            )
            quality_failures = sum(row.status == "failed" for row in latest_quality_rows)
            quality_warnings = sum(row.status == "warning" for row in latest_quality_rows)
            # Naming the checks that warned lets a reader, and the alert
            # classifier, tell an expected condition from a new problem.
            quality_warning_names = sorted(
                {row.name for row in latest_quality_rows if row.status == "warning"}
            )
            age_seconds = (
                max(0, int((observed_at - _as_utc(latest.finished_at)).total_seconds()))
                if latest is not None and latest.finished_at is not None
                else None
            )
            stale_after_seconds = max(0, int(stale_after.total_seconds()))
            if latest is None:
                health_status = "degraded"
                health_message = "No successful forward run has been recorded."
            elif latest_run is not None and latest_run.status == "failed":
                health_status = "degraded"
                health_message = "The latest forward run failed; inspect its run details."
            elif quality_failures:
                health_status = "degraded"
                health_message = f"The latest run has {quality_failures} failed quality gate(s)."
            elif age_seconds is not None and age_seconds > stale_after_seconds:
                health_status = "degraded"
                health_message = (
                    "No successful forward run completed within the configured freshness window."
                )
            elif quality_warnings:
                health_status = "warning"
                health_message = f"The latest run has {quality_warnings} quality warning(s)."
            else:
                health_status = "ok"
                health_message = "Forward runner is healthy and within its freshness window."
            return {
                "configured": True,
                "model_count": model_count,
                "run_count": run_count,
                "forecast_count": forecast_count,
                "matured_count": matured_count,
                "pending_count": max(forecast_count - matured_count, 0),
                "latest_successful_run_at": _iso(latest.finished_at) if latest else None,
                "health_status": health_status,
                "health_message": health_message,
                "latest_run_at": _iso(latest_run.started_at) if latest_run else None,
                "latest_run_status": latest_run.status if latest_run else None,
                "latest_failed_run_at": _iso(latest_failed.finished_at) if latest_failed else None,
                "age_seconds": age_seconds,
                "stale_after_seconds": stale_after_seconds,
                "running_run_count": int(
                    session.scalar(
                        select(func.count(RunRecord.run_id)).where(RunRecord.status == "running")
                    )
                    or 0
                ),
                "latest_quality_warnings": quality_warnings,
                "latest_quality_warning_names": quality_warning_names,
                "latest_quality_failures": quality_failures,
            }

    def latest_forecast_as_of(self, *, model_id: str) -> datetime | None:
        """Return when a model's most recent successful forecast run scored events."""
        with self.database.session() as session:
            value = session.scalar(
                select(func.max(RunRecord.as_of)).where(
                    RunRecord.run_type == "forecast",
                    RunRecord.status == "succeeded",
                    RunRecord.model_id == model_id,
                )
            )
        return _as_utc(value) if value is not None else None

    def list_runs(self, *, limit: int = 25) -> list[dict[str, Any]]:
        with self.database.session() as session:
            records = session.scalars(
                select(RunRecord).order_by(RunRecord.started_at.desc()).limit(limit)
            ).all()
            return [
                {
                    "run_id": row.run_id,
                    "run_type": row.run_type,
                    "status": row.status,
                    "dataset_id": row.dataset_id,
                    "model_id": row.model_id,
                    "as_of": _iso(row.as_of),
                    "code_revision": row.code_revision,
                    "result_counts": row.result_counts,
                    "error_message": row.error_message,
                    "started_at": _iso(row.started_at),
                    "finished_at": _iso(row.finished_at),
                }
                for row in records
            ]

    def list_forecasts(
        self,
        *,
        ticker: str | None = None,
        model_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        if limit < 1 or not 0 <= offset <= _MAX_PAGE_OFFSET:
            raise ValueError(f"limit must be positive and offset within 0..{_MAX_PAGE_OFFSET}")
        filters: list[Any] = []
        if ticker:
            filters.append(ForecastRecord.ticker == ticker.upper())
        if model_id:
            filters.append(ForecastRecord.model_id == model_id)
        with self.database.session() as session:
            total = int(
                session.scalar(select(func.count(ForecastRecord.forecast_id)).where(*filters)) or 0
            )
            rows = session.execute(
                select(ForecastRecord, LabelRecord)
                .outerjoin(LabelRecord, LabelRecord.forecast_id == ForecastRecord.forecast_id)
                .where(*filters)
                .order_by(ForecastRecord.forecast_as_of.desc(), ForecastRecord.ticker)
                .offset(offset)
                .limit(limit)
            ).all()
            # A forecast's rank is its percentile among the forecasts recorded in the same
            # run, which often holds only one or two filings. Report each run's full size,
            # regardless of page or filters, so a rank is never read as market-wide.
            run_ids = sorted({forecast.run_id for forecast, _ in rows})
            cohort_sizes: dict[str, int] = (
                {
                    run_id: int(size)
                    for run_id, size in session.execute(
                        select(ForecastRecord.run_id, func.count(ForecastRecord.forecast_id))
                        .where(ForecastRecord.run_id.in_(run_ids))
                        .group_by(ForecastRecord.run_id)
                    ).all()
                }
                if run_ids
                else {}
            )
            items = [
                {
                    "forecast_id": forecast.forecast_id,
                    "run_id": forecast.run_id,
                    "model_id": forecast.model_id,
                    "event_id": forecast.event_id,
                    "accession_number": forecast.accession_number,
                    "security_id": forecast.security_id,
                    "ticker": forecast.ticker,
                    "company_name": forecast.company_name,
                    "form": forecast.form,
                    "accepted_at": _iso(forecast.accepted_at),
                    "entry_at": _iso(forecast.entry_at),
                    "entry_date": forecast.entry_date.isoformat(),
                    "horizon_at": _iso(forecast.horizon_at),
                    "forecast_as_of": _iso(forecast.forecast_as_of),
                    "score": forecast.score,
                    "rank": forecast.rank,
                    "cohort_size": cohort_sizes.get(forecast.run_id, 1),
                    "fundamental_score": forecast.fundamental_score,
                    "expert_weights": forecast.expert_weights,
                    "realized_abnormal_return": (
                        label.realized_abnormal_return if label is not None else None
                    ),
                    "label_recorded_at": _iso(label.recorded_at) if label is not None else None,
                }
                for forecast, label in rows
            ]
            return {"items": items, "total": total, "offset": offset, "limit": limit}

    def performance(self, *, model_id: str | None = None) -> dict[str, Any]:
        filters: list[Any] = []
        if model_id:
            filters.append(ForecastRecord.model_id == model_id)
        with self.database.session() as session:
            forecast_count = int(
                session.scalar(select(func.count(ForecastRecord.forecast_id)).where(*filters)) or 0
            )
            pairs = session.execute(
                select(
                    ForecastRecord.score,
                    LabelRecord.realized_abnormal_return,
                    ForecastRecord.accepted_at,
                )
                .join(LabelRecord, LabelRecord.forecast_id == ForecastRecord.forecast_id)
                .where(*filters)
                .order_by(ForecastRecord.accepted_at, ForecastRecord.forecast_id)
            ).all()
        scores = [float(row.score) for row in pairs]
        labels = [float(row.realized_abnormal_return) for row in pairs]
        metrics = forward_metrics(
            scores,
            labels,
            forecast_count=forecast_count,
        )
        interval = clustered_rank_ic_interval(scores, labels, [row.accepted_at for row in pairs])
        return {
            "model_id": model_id,
            "forecast_count": metrics.forecast_count,
            "matured_count": metrics.matured_count,
            "pending_count": metrics.pending_count,
            "coverage": metrics.coverage,
            "rank_ic": metrics.rank_ic,
            "rank_ic_low": interval.low,
            "rank_ic_high": interval.high,
            "rank_ic_interval_method": "calendar_month_moving_block",
            "rank_ic_interval_status": interval.status,
            "rank_ic_calendar_months": interval.calendar_months,
            "rank_ic_block_months": interval.block_months,
            "rank_ic_bootstrap_samples": interval.resamples,
            "rmse": metrics.rmse,
            "mae": metrics.mae,
            "directional_accuracy": metrics.directional_accuracy,
        }

    def pending_forecasts(
        self,
        *,
        as_of: datetime,
        model_id: str | None = None,
    ) -> list[dict[str, Any]]:
        cutoff = _as_utc(as_of)
        filters: list[Any] = [
            ForecastRecord.horizon_at <= cutoff,
            LabelRecord.label_id.is_(None),
        ]
        if model_id:
            filters.append(ForecastRecord.model_id == model_id)
        with self.database.session() as session:
            rows = session.scalars(
                select(ForecastRecord)
                .outerjoin(LabelRecord, LabelRecord.forecast_id == ForecastRecord.forecast_id)
                .where(*filters)
                .order_by(ForecastRecord.horizon_at, ForecastRecord.forecast_id)
            ).all()
            return [
                {
                    "forecast_id": row.forecast_id,
                    "event_id": row.event_id,
                    "model_id": row.model_id,
                    "horizon_at": _iso(row.horizon_at),
                }
                for row in rows
            ]

    def list_quality_checks(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.database.session() as session:
            rows = session.scalars(
                select(DataQualityRecord).order_by(DataQualityRecord.created_at.desc()).limit(limit)
            ).all()
            return [
                {
                    "check_id": row.check_id,
                    "run_id": row.run_id,
                    "name": row.name,
                    "status": row.status,
                    "observed_value": row.observed_value,
                    "threshold": row.threshold,
                    "details": row.details,
                    "created_at": _iso(row.created_at),
                }
                for row in rows
            ]


def _require_running_run(
    session: Session,
    run_id: str,
    *,
    expected_type: str | None = None,
) -> RunRecord:
    run = session.get(RunRecord, run_id)
    if run is None:
        raise RegistryStateError(f"Run is not registered: {run_id}")
    if run.status != "running":
        raise RegistryStateError(f"Run is not active: {run_id} ({run.status})")
    if expected_type is not None and run.run_type != expected_type:
        raise RegistryStateError(f"Run {run_id} has type {run.run_type}; expected {expected_type}")
    return run


def _require_optional_reference(
    session: Session,
    record_type: type[Any],
    record_id: str | None,
    label: str,
) -> None:
    if record_id is not None and session.get(record_type, record_id) is None:
        raise RegistryStateError(f"Referenced {label} is not registered: {record_id}")


def _assert_same(stored_hash: str, payload_hash: str, label: str, record_id: str) -> None:
    if stored_hash != payload_hash:
        raise RegistryConflictError(
            f"Conflicting {label} payload for immutable identity {record_id}"
        )


def _audit(
    session: Session,
    *,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str,
    payload: dict[str, Any],
) -> None:
    serializable_payload = orjson.loads(orjson.dumps(payload, option=orjson.OPT_UTC_Z))
    identity = {
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "actor": actor,
        "payload": serializable_payload,
    }
    session.add(
        AuditEvent(
            audit_id=_new_id("audit"),
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            actor=actor,
            payload=serializable_payload,
            payload_hash=_payload_hash(identity),
        )
    )


def _payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        orjson.dumps(payload, option=orjson.OPT_SORT_KEYS | orjson.OPT_UTC_Z)
    ).hexdigest()


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()
    return f"{prefix}-{digest[:32]}"


def _new_id(prefix: str) -> str:
    stamp = utc_now().strftime("%Y%m%dT%H%M%S%fZ")
    return f"{prefix}-{stamp}-{uuid4().hex[:12]}"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    return _as_utc(value).isoformat().replace("+00:00", "Z") if value else None
