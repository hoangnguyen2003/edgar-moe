"""Seed a disposable forward-registry fixture for restore rehearsals.

This script is intentionally synthetic and is only used by the CI restore drill.
It creates one successful forecast run and one content-addressed local evidence
object; it never reads research credentials or production data.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from edgar_moe.forward.artifacts import LocalArtifactStore
from edgar_moe.forward.database import RegistryDatabase
from edgar_moe.forward.domain import (
    DatasetRegistration,
    ForecastDraft,
    ModelRegistration,
    QualityCheckDraft,
    RunRegistration,
)
from edgar_moe.forward.registry import ForwardRegistry

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("EDGAR_MOE_REGISTRY_DATABASE_URL", ""),
        help="Disposable registry URL; defaults to EDGAR_MOE_REGISTRY_DATABASE_URL.",
    )
    parser.add_argument(
        "--artifact-root",
        type=Path,
        required=True,
        help="New local content-addressed root for the fixture evidence.",
    )
    parser.add_argument(
        "--allow-synthetic",
        action="store_true",
        help="Explicitly acknowledge that this writes synthetic CI-only data.",
    )
    return parser.parse_args()


def seed_fixture(database_url: str, artifact_root: Path) -> dict[str, object]:
    if not database_url.strip():
        raise ValueError("A disposable database URL is required")
    if artifact_root.exists() and any(artifact_root.iterdir()):
        raise ValueError("Fixture artifact root must be empty")

    observed_at = datetime.now(UTC).replace(microsecond=0)
    database = RegistryDatabase(database_url)
    database.create_schema()
    store = LocalArtifactStore(artifact_root)
    registry = ForwardRegistry(database, actor="restore-fixture")
    try:
        model_reference = store.put_bytes(
            b"restore-fixture-frozen-model", logical_name="frozen-model.pt"
        )
        registry.register_dataset(
            DatasetRegistration(
                dataset_id="restore-fixture-dataset",
                as_of=observed_at.date(),
                source_manifest_hash=HASH_A,
                manifest_sha256=HASH_B,
                row_counts={"events": 1, "labels": 0},
                provenance={"fixture": "ci-restore-rehearsal"},
            )
        )
        registry.register_model(
            ModelRegistration(
                model_id="restore-fixture-model",
                name="Restore rehearsal fixture",
                version="1.0.0",
                training_dataset_id="restore-fixture-dataset",
                selection_hash=HASH_A,
                artifact_uri=model_reference.uri,
                artifact_sha256=model_reference.sha256,
                config_hash=HASH_C,
                champion_family="fixture",
                frozen_at=observed_at - timedelta(days=1),
            )
        )
        run = registry.start_run(
            RunRegistration(
                run_type="forecast",
                as_of=observed_at,
                code_revision="restore-fixture",
                config_hash=HASH_C,
                dataset_id="restore-fixture-dataset",
                model_id="restore-fixture-model",
                details={"fixture": "ci-restore-rehearsal"},
            ),
            run_id="restore-fixture-run",
        )
        forecast = ForecastDraft(
            event_id="restore-fixture-event",
            accession_number="0000000000-26-000001",
            security_id="restore-fixture-security",
            ticker="FIXT",
            company_name="Restore Fixture Corp",
            form="10-Q",
            accepted_at=observed_at - timedelta(hours=1),
            entry_at=observed_at + timedelta(days=1),
            entry_date=(observed_at + timedelta(days=1)).date(),
            horizon_at=observed_at + timedelta(days=30),
            industry_code="0000",
            score=0.25,
            rank=1.0,
            fundamental_score=0.2,
            expert_weights={"fixture": 1.0},
        )
        forecast_counts = registry.append_forecasts(run.run_id, [forecast])
        quality_counts = registry.add_quality_checks(
            run.run_id,
            [QualityCheckDraft(name="restore_fixture_quality", status="passed")],
        )
        evidence = json.dumps(
            {
                "schema_version": 1,
                "run_id": run.run_id,
                "fixture": "ci-restore-rehearsal",
                "forecast_count": forecast_counts["inserted"],
            },
            sort_keys=True,
        ).encode()
        evidence_reference = store.put_bytes(evidence, logical_name="forecast-batch.json")
        registry.register_artifact(
            run.run_id,
            kind="forecast_batch",
            reference=evidence_reference,
        )
        registry.complete_run(
            run.run_id,
            result_counts={
                "forecasts_inserted": forecast_counts["inserted"],
                "quality_checks_inserted": quality_counts["inserted"],
            },
        )
        return {
            "run_id": run.run_id,
            "artifact_uri": evidence_reference.uri,
            "forecast_count": forecast_counts["inserted"],
            "quality_check_count": quality_counts["inserted"],
        }
    finally:
        database.dispose()


def main() -> int:
    args = _parse_args()
    if not args.allow_synthetic:
        raise SystemExit("Refusing to seed synthetic data without --allow-synthetic")
    result = seed_fixture(args.database_url, args.artifact_root.expanduser().resolve())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
