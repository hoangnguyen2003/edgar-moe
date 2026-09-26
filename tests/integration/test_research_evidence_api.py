from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import edgar_moe.api.app as app_module
from edgar_moe.api.app import app, get_repository
from edgar_moe.api.repository import SnapshotRepository
from edgar_moe.api.research_evidence import CatalogIntegrityError


def test_research_evidence_api_serves_frozen_v1_and_pending_v2() -> None:
    repo = SnapshotRepository(
        "data/demo/snapshot.json", lock_path="config/public_snapshot.lock.json"
    )
    app.dependency_overrides[get_repository] = lambda: repo
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/research-evidence")
        assert response.status_code == 200
        assert response.headers["cache-control"].startswith("public")
        payload = response.json()
        assert payload["schema_version"] == 1
        assert payload["frozen_v1"]["locked_rank_ic_interval_95"]["low"] < 0
        assert payload["frozen_v1"]["portfolio_10bps_sharpe"] < 0
        assert payload["frozen_v1"]["candidate_universe"]["status"] == (
            "retrospective_test_period_screen"
        )
        assert "future information" in payload["frozen_v1"]["candidate_universe"]["interpretation"]
        assert payload["duration_aware_v2"] == {
            "status": "pending_review",
            "reason": (
                "No separately reviewed duration-aware v2 aggregate is published; "
                "historical candidate membership and source rights remain unresolved."
            ),
        }
        assert "private_event_rows" not in response.text
    finally:
        app.dependency_overrides.clear()


def test_research_evidence_api_fails_closed_without_leaking_catalog_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject(_repo: SnapshotRepository) -> None:
        raise CatalogIntegrityError("private-path-or-hash")

    monkeypatch.setattr(app_module, "build_research_evidence", reject)
    with TestClient(app) as client:
        response = client.get("/api/v1/research-evidence")
    assert response.status_code == 503
    assert response.json() == {"detail": "Public research evidence unavailable"}
    assert "private-path-or-hash" not in response.text
