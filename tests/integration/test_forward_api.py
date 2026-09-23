from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from edgar_moe.api.app import app, get_forward_registry
from edgar_moe.forward.database import RegistryDatabase
from edgar_moe.forward.domain import (
    DatasetRegistration,
    ForecastDraft,
    LabelDraft,
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
            assert page["items"][0]["cohort_size"] == 1
            # An unbounded offset used to overflow SQLite and return HTTP 500.
            assert client.get(f"/api/v1/forward/forecasts?offset={10**30}").status_code == 422
            beyond = client.get("/api/v1/forward/forecasts?offset=1000000").json()
            assert beyond["items"] == [] and beyond["total"] == 1
            performance = client.get("/api/v1/forward/performance").json()
            assert performance["pending_count"] == 1
            assert performance["rank_ic_interval_status"] == "insufficient_pairs"
            assert performance["rank_ic_calendar_months"] == 0
            assert client.get("/api/v1/forward/data-quality").json()[0]["status"] == "passed"
    finally:
        app.dependency_overrides.clear()
        database.dispose()


def test_forward_api_reports_clustered_interval_only_after_calendar_history(tmp_path: Path) -> None:
    database = RegistryDatabase(f"sqlite:///{tmp_path / 'clustered-forward.sqlite3'}")
    database.create_schema()
    registry = ForwardRegistry(database, actor="interval-test")
    registry.register_dataset(
        DatasetRegistration(
            dataset_id="dataset-1",
            as_of=date(2025, 1, 1),
            source_manifest_hash=HASH_A,
            manifest_sha256=HASH_B,
            row_counts={"events": 120},
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
            frozen_at=datetime(2024, 12, 31, tzinfo=UTC),
        )
    )
    for month in range(1, 13):
        as_of = datetime(2025, month, 10, 12, tzinfo=UTC)
        run = registry.start_run(
            RunRegistration(
                run_type="forecast",
                as_of=as_of,
                code_revision="deadbeef",
                config_hash=HASH_C,
                dataset_id="dataset-1",
                model_id="model-1",
            ),
            run_id=f"forecast-{month}",
        )
        drafts = [
            ForecastDraft(
                event_id=f"event-{month}-{index}",
                accession_number=f"0000000000-25-{month * 10 + index:06d}",
                security_id=f"asset-{month}-{index}",
                ticker=f"T{month:02d}{index:02d}",
                company_name="Test Corp",
                form="10-Q",
                accepted_at=as_of - timedelta(hours=1),
                entry_at=as_of + timedelta(hours=12),
                entry_date=(as_of + timedelta(days=1)).date(),
                horizon_at=as_of + timedelta(days=30),
                industry_code="3571",
                score=math.sin((month * 10 + index) * 0.23),
                rank=(index + 1) / 10,
                fundamental_score=0.1,
                expert_weights={"text": 0.2, "fundamental": 0.6, "market": 0.2},
            )
            for index in range(10)
        ]
        assert registry.append_forecasts(run.run_id, drafts)["inserted"] == 10
        registry.complete_run(run.run_id, result_counts={"forecasts": 10})

    page_a = registry.list_forecasts(limit=100)
    page_b = registry.list_forecasts(limit=100, offset=100)
    rows = page_a["items"] + page_b["items"]
    assert len(rows) == 120
    settlement_as_of = datetime(2026, 2, 1, tzinfo=UTC)
    settlement = registry.start_run(
        RunRegistration(
            run_type="settlement",
            as_of=settlement_as_of,
            code_revision="deadbeef",
            config_hash=HASH_C,
            dataset_id="dataset-1",
            model_id="model-1",
        ),
        run_id="settlement-1",
    )
    labels = [
        LabelDraft(
            forecast_id=row["forecast_id"],
            source_dataset_id="dataset-1",
            realized_abnormal_return=row["score"] * 0.3 + math.cos(index * 0.51),
            matured_at=settlement_as_of,
        )
        for index, row in enumerate(rows)
    ]
    assert registry.settle_labels(settlement.run_id, labels)["inserted"] == 120
    registry.complete_run(settlement.run_id, result_counts={"labels": 120})

    app.dependency_overrides[get_forward_registry] = lambda: registry
    try:
        with TestClient(app) as client:
            performance = client.get("/api/v1/forward/performance").json()
            assert performance["matured_count"] == 120
            assert performance["rank_ic_interval_status"] == "ready"
            assert performance["rank_ic_interval_method"] == "calendar_month_moving_block"
            assert performance["rank_ic_calendar_months"] == 12
            assert performance["rank_ic_block_months"] == 2
            assert performance["rank_ic_bootstrap_samples"] == 1000
            assert performance["rank_ic_low"] is not None
            assert performance["rank_ic_high"] is not None
            assert performance["rank_ic_low"] <= performance["rank_ic_high"]
    finally:
        app.dependency_overrides.clear()
        database.dispose()
