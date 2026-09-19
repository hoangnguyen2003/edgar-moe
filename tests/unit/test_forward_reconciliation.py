from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from edgar_moe.forward.artifacts import ArtifactReference, LocalArtifactStore
from edgar_moe.forward.database import RegistryDatabase
from edgar_moe.forward.domain import DatasetRegistration, ModelRegistration, RunRegistration
from edgar_moe.forward.reconciliation import (
    ArtifactReconciliationError,
    reconcile_registry_artifacts,
)
from edgar_moe.forward.registry import ForwardRegistry

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def registry_with_artifact(tmp_path: Path) -> tuple[RegistryDatabase, ForwardRegistry, Path, str]:
    database = RegistryDatabase(f"sqlite:///{tmp_path / 'registry.sqlite3'}")
    database.create_schema()
    registry = ForwardRegistry(database, actor="reconciliation-test")
    registry.register_dataset(
        DatasetRegistration(
            dataset_id="dataset-1",
            as_of=date(2026, 9, 1),
            source_manifest_hash=HASH_A,
            manifest_sha256=HASH_B,
            row_counts={"events": 1},
        )
    )
    registry.register_model(
        ModelRegistration(
            model_id="model-1",
            name="Frozen MoE",
            version="1.0.0",
            training_dataset_id="dataset-1",
            selection_hash=HASH_A,
            artifact_uri="local://model.pt",
            artifact_sha256=HASH_B,
            config_hash=HASH_C,
            champion_family="anchored_multimodal",
            frozen_at=datetime(2026, 9, 1, tzinfo=UTC),
        )
    )
    run = registry.start_run(
        RunRegistration(
            run_type="forecast",
            as_of=datetime(2026, 9, 2, tzinfo=UTC),
            code_revision="deadbeef",
            config_hash=HASH_C,
            dataset_id="dataset-1",
            model_id="model-1",
        ),
        run_id="run-1",
    )
    local = LocalArtifactStore(tmp_path / "artifacts")
    reference = local.put_bytes(b"immutable batch", logical_name="forecast-batch.json")
    registry.register_artifact(run.run_id, kind="forecast_batch", reference=reference)
    return database, registry, local.root, reference.key


def test_reconciliation_verifies_and_repairs_only_registry_references(tmp_path: Path) -> None:
    database, registry, local_root, _ = registry_with_artifact(tmp_path)
    mirror = LocalArtifactStore(tmp_path / "mirror")
    local = LocalArtifactStore(local_root)

    report = reconcile_registry_artifacts(registry, local, mirror=mirror, repair=True)

    assert report["status"] == "passed"
    assert report["expected_artifacts"] == 1
    assert report["verified_artifacts"] == 1
    assert report["mirrored_artifacts"] == 1
    assert report["issues"] == []
    database.dispose()


def test_reconciliation_reports_missing_primary_object_without_repair(tmp_path: Path) -> None:
    database, registry, local_root, key = registry_with_artifact(tmp_path)
    (local_root / key).unlink()

    report = reconcile_registry_artifacts(registry, LocalArtifactStore(local_root))

    assert report["status"] == "failed"
    assert report["issue_count"] == 1
    assert report["issues"][0]["code"] == "missing_local_object"
    database.dispose()


def test_reconciliation_requires_mirror_for_explicit_repair(tmp_path: Path) -> None:
    database, registry, local_root, _ = registry_with_artifact(tmp_path)

    with pytest.raises(ArtifactReconciliationError, match="requires an artifact mirror"):
        reconcile_registry_artifacts(
            registry,
            LocalArtifactStore(local_root),
            repair=True,
        )
    database.dispose()


def test_reconciliation_does_not_accept_non_local_primary_uri(tmp_path: Path) -> None:
    database, registry, local_root, _ = registry_with_artifact(tmp_path)
    registry.register_artifact(
        "run-1",
        kind="remote_batch",
        reference=ArtifactReference(
            uri="r2://private-bucket/object",
            sha256=HASH_A,
            size_bytes=1,
            key="sha256/aa/object",
        ),
    )

    report = reconcile_registry_artifacts(registry, LocalArtifactStore(local_root))

    assert report["status"] == "failed"
    assert any(issue["code"] == "unsupported_primary_uri" for issue in report["issues"])
    database.dispose()
