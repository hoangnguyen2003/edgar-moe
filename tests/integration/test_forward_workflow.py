from __future__ import annotations

import hashlib
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import orjson
import pandas as pd
import pytest

from edgar_moe.features.dataset import ResearchDataset
from edgar_moe.forward.artifacts import (
    ArtifactReference,
    ArtifactStore,
    ArtifactWriteError,
    LocalArtifactStore,
    MirroredArtifactStore,
)
from edgar_moe.forward.config import FrozenModelSpec
from edgar_moe.forward.database import RegistryDatabase
from edgar_moe.forward.domain import ForecastDraft, QualityCheckDraft
from edgar_moe.forward.inference import ForecastBatch
from edgar_moe.forward.reconciliation import reconcile_registry_artifacts
from edgar_moe.forward.registry import ForwardRegistry
from edgar_moe.forward.workflow import ForwardWorkflow

SELECTION_HASH = "a" * 64
SOURCE_HASH = "b" * 64


class FixturePredictor:
    def forecast(
        self,
        dataset: ResearchDataset,
        *,
        as_of: datetime,
        device: str = "cpu",
    ) -> ForecastBatch:
        del dataset, device
        draft = ForecastDraft(
            event_id="event-1",
            accession_number="0000000000-26-000001",
            security_id="asset-1",
            ticker="TEST",
            company_name="Test Corp",
            form="10-Q",
            accepted_at=as_of - timedelta(hours=1),
            entry_at=as_of + timedelta(hours=12),
            entry_date=(as_of + timedelta(days=1)).date(),
            horizon_at=as_of + timedelta(days=30),
            industry_code="3571",
            score=0.12,
            rank=1.0,
            fundamental_score=0.1,
            expert_weights={"text": 0.2, "fundamental": 0.6, "market": 0.2},
        )
        return ForecastBatch(
            forecasts=[draft],
            checks=[QualityCheckDraft(name="point_in_time_availability", status="passed")],
            candidate_indices=np.array([0]),
        )


class FailOnceMirror:
    """Mirror fixture that fails one byte write, then behaves normally."""

    def __init__(self, root: Path) -> None:
        self.delegate = LocalArtifactStore(root)
        self.fail_next_bytes = True

    def put_file(
        self, source: str | Path, *, logical_name: str | None = None
    ) -> ArtifactReference:
        return self.delegate.put_file(source, logical_name=logical_name)

    def put_bytes(self, content: bytes, *, logical_name: str) -> ArtifactReference:
        if self.fail_next_bytes:
            self.fail_next_bytes = False
            raise OSError("injected mirror outage")
        return self.delegate.put_bytes(content, logical_name=logical_name)

    def read_bytes(self, reference: ArtifactReference) -> bytes:
        return self.delegate.read_bytes(reference)


def build_workflow_fixture(
    tmp_path: Path,
    *,
    artifact_store: ArtifactStore | None = None,
) -> tuple[ForwardWorkflow, ForwardRegistry, RegistryDatabase, Path, datetime, list[datetime]]:
    forecast_as_of = datetime.now(UTC)
    training_dir = save_dataset(
        tmp_path / "datasets",
        dataset_id="training-dataset",
        as_of=forecast_as_of.date(),
        target=np.array([np.nan]),
        horizon_at=forecast_as_of + timedelta(days=30),
    )
    model_path = tmp_path / "frozen-model.pt"
    model_path.write_bytes(b"reviewed frozen model")
    model_hash = hashlib.sha256(model_path.read_bytes()).hexdigest()
    locked_result_path = tmp_path / "locked-test.json"
    locked_payload = {
        "run_id": "locked-run",
        "dataset_id": "training-dataset",
        "selection_hash": SELECTION_HASH,
        "model_sha256": model_hash,
        "champion_name": "Fixture MoE",
        "champion_family": "anchored_multimodal",
        "champion_parameters": {
            "fundamental_anchor_weight": 0.75,
            "moe_residual_weight": 0.25,
        },
    }
    locked_hash = hashlib.sha256(
        orjson.dumps(locked_payload, option=orjson.OPT_SORT_KEYS)
    ).hexdigest()
    locked_payload["locked_test_hash"] = locked_hash
    locked_result_path.write_bytes(
        orjson.dumps(locked_payload, option=orjson.OPT_SORT_KEYS)
    )
    spec = FrozenModelSpec(
        model_id="model-1",
        name="Fixture MoE",
        version="1.0.0",
        training_dataset_id="training-dataset",
        training_dataset_dir=training_dir,
        model_path=model_path,
        locked_result_path=locked_result_path,
        artifact_sha256=model_hash,
        selection_hash=SELECTION_HASH,
        locked_test_hash=locked_hash,
        frozen_at=forecast_as_of - timedelta(days=1),
    )
    database = RegistryDatabase(f"sqlite:///{tmp_path / 'registry.sqlite3'}")
    database.create_schema()
    registry = ForwardRegistry(database, actor="workflow-test")
    clock = [forecast_as_of]
    workflow = ForwardWorkflow(
        registry,
        artifact_store or LocalArtifactStore(tmp_path / "artifacts"),
        spec,
        clock=lambda: clock[0],
    )
    return workflow, registry, database, training_dir, forecast_as_of, clock


def test_forward_workflow_records_then_settles_without_mutation(tmp_path: Path) -> None:
    workflow, registry, database, training_dir, forecast_as_of, clock = build_workflow_fixture(
        tmp_path
    )

    forecast_result = workflow.forecast(
        training_dir,
        as_of=forecast_as_of,
        code_revision="deadbeef",
        predictor=FixturePredictor(),
    )
    assert forecast_result.counts["forecasts_inserted"] == 1
    recorded = registry.list_forecasts(limit=10)["items"][0]
    assert recorded["realized_abnormal_return"] is None

    settlement_as_of = forecast_as_of + timedelta(days=31)
    clock[0] = settlement_as_of
    shutil.rmtree(training_dir)
    settlement_dir = save_dataset(
        tmp_path / "datasets",
        dataset_id="settlement-dataset",
        as_of=settlement_as_of.date(),
        target=np.array([0.08]),
        horizon_at=forecast_as_of + timedelta(days=30),
    )
    settlement_result = workflow.settle(
        settlement_dir,
        as_of=settlement_as_of,
        code_revision="deadbeef",
    )
    assert settlement_result.counts["labels_inserted"] == 1
    settled = registry.list_forecasts(limit=10)["items"][0]
    assert settled["score"] == 0.12
    assert settled["realized_abnormal_return"] == 0.08
    assert registry.performance()["coverage"] == 1.0
    database.dispose()


def test_mirror_failure_records_primary_for_reconciliation_and_retry(tmp_path: Path) -> None:
    primary = LocalArtifactStore(tmp_path / "artifacts")
    mirror = FailOnceMirror(tmp_path / "mirror")
    workflow, registry, database, training_dir, forecast_as_of, _ = build_workflow_fixture(
        tmp_path,
        artifact_store=MirroredArtifactStore(primary, mirror),
    )

    with pytest.raises(ArtifactWriteError, match="mirror write failed"):
        workflow.forecast(
            training_dir,
            as_of=forecast_as_of,
            code_revision="deadbeef",
            predictor=FixturePredictor(),
        )

    failed_run = registry.list_runs(limit=1)[0]
    assert failed_run["status"] == "failed"
    assert "primary artifact was recorded for reconciliation" in failed_run["error_message"]
    assert registry.list_forecasts(limit=10)["total"] == 1
    references = registry.list_artifacts()
    assert len(references) == 1

    repaired_mirror = LocalArtifactStore(tmp_path / "repaired-mirror")
    report = reconcile_registry_artifacts(
        registry,
        primary,
        mirror=repaired_mirror,
        repair=True,
    )
    assert report["status"] == "passed"
    assert report["mirrored_artifacts"] == 1
    record = references[0]
    repaired_reference = ArtifactReference(
        uri=record["uri"],
        sha256=record["sha256"],
        size_bytes=record["size_bytes"],
        key=record["uri"].removeprefix("local://"),
    )
    assert repaired_mirror.read_bytes(repaired_reference)

    retry = workflow.forecast(
        training_dir,
        as_of=forecast_as_of,
        code_revision="deadbeef",
        predictor=FixturePredictor(),
    )
    assert retry.status == "succeeded"
    assert retry.counts["forecasts_idempotent"] == 1
    assert len(registry.list_artifacts()) == 2
    assert registry.list_runs(limit=1)[0]["status"] == "succeeded"
    database.dispose()


def save_dataset(
    output_root: Path,
    *,
    dataset_id: str,
    as_of: date,
    target: np.ndarray,
    horizon_at: datetime,
) -> Path:
    events = pd.DataFrame(
        [
            {
                "event_id": "event-1",
                "horizon_at": horizon_at,
            }
        ]
    )
    availability = pd.DataFrame(
        [
            {
                "event_id": "event-1",
                "feature_name": "fixture",
                "available_at": horizon_at - timedelta(days=31),
                "prediction_at": horizon_at - timedelta(days=30),
                "source": "fixture",
            }
        ]
    )
    dataset = ResearchDataset(
        dataset_id=dataset_id,
        as_of=as_of,
        events=events,
        modalities={
            "text": np.zeros((1, 1), dtype=np.float32),
            "fundamental": np.zeros((1, 1), dtype=np.float32),
            "market": np.zeros((1, 1), dtype=np.float32),
        },
        regime=np.zeros((1, 1), dtype=np.float32),
        target=target,
        daily_returns=pd.DataFrame({"date": [as_of], "asset-1": [0.0]}),
        availability=availability,
        feature_names={
            "text": ["text"],
            "fundamental": ["fundamental"],
            "market": ["market"],
            "regime": ["regime"],
        },
        attrition={"included_events": 1},
        source_manifest_hash=SOURCE_HASH,
        provenance={"fixture": "true"},
    )
    return dataset.save(output_root)
