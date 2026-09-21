from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from edgar_moe.api.app import app, get_forward_registry
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


def test_forward_api_contracts(tmp_path: Path) -> None:
    database = RegistryDatabase(f"sqlite:///{tmp_path / 'forward.sqlite3'}")
    database.create_schema()
    registry = ForwardRegistry(database, actor="api-test")
    registry.register_dataset(
        DatasetRegistration(
            dataset_id="dataset-1",
            as_of=date(2026, 8, 7),
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
            frozen_at=datetime(2026, 8, 6, tzinfo=UTC),
        )
    )
    forecast_as_of = datetime(2026, 8, 7, 1, 0, tzinfo=UTC)
    run = registry.start_run(
        RunRegistration(
            run_type="forecast",
            as_of=forecast_as_of,
            code_revision="deadbeef",
            config_hash=HASH_C,
            dataset_id="dataset-1",
            model_id="model-1",
        ),
        run_id="run-1",
    )
    registry.append_forecasts(
        run.run_id,
        [
            ForecastDraft(
                event_id="event-1",
                accession_number="0000000000-26-000001",
                security_id="asset-1",
                ticker="TEST",
                company_name="Test Corp",
                form="10-Q",
                accepted_at=forecast_as_of - timedelta(hours=1),
                entry_at=forecast_as_of + timedelta(hours=12),
                entry_date=date(2026, 8, 8),
                horizon_at=forecast_as_of + timedelta(days=30),
                industry_code="3571",
                score=0.12,
                rank=1.0,
                fundamental_score=0.1,
                expert_weights={"text": 0.2, "fundamental": 0.6, "market": 0.2},
            )
        ],
    )
    registry.add_quality_checks(
        run.run_id,
        [QualityCheckDraft(name="point_in_time_availability", status="passed")],
    )
    registry.complete_run(run.run_id, result_counts={"forecasts": 1})

    app.dependency_overrides[get_forward_registry] = lambda: registry
    try:
        with TestClient(app) as client:
            status = client.get("/api/v1/forward/status").json()
            assert status["available"] is True
            assert status["forecast_count"] == 1
            assert status["health_status"] == "ok"
            assert status["latest_run_status"] == "succeeded"
            assert status["latest_quality_failures"] == 0
            assert client.get("/api/v1/forward/runs").json()[0]["run_id"] == "run-1"
            page = client.get("/api/v1/forward/forecasts?ticker=test").json()
            assert page["total"] == 1
            assert page["items"][0]["entry_at"].endswith("Z")
            # An unbounded offset used to overflow SQLite and return HTTP 500.
            assert client.get(f"/api/v1/forward/forecasts?offset={10**30}").status_code == 422
            beyond = client.get("/api/v1/forward/forecasts?offset=1000000").json()
            assert beyond["items"] == [] and beyond["total"] == 1
            performance = client.get("/api/v1/forward/performance").json()
            assert performance["pending_count"] == 1
            assert client.get("/api/v1/forward/data-quality").json()[0]["status"] == "passed"
    finally:
        app.dependency_overrides.clear()
        database.dispose()
