from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import edgar_moe.api.app as app_module
from edgar_moe.api.app import app, get_repository
from edgar_moe.api.repository import SnapshotRepository


def test_research_evidence_api_is_withheld_for_the_synthetic_public_snapshot() -> None:
    repo = SnapshotRepository(
        "data/demo/snapshot.json", lock_path="config/public_snapshot.lock.json"
    )
    app.dependency_overrides[get_repository] = lambda: repo
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/research-evidence")
        assert response.status_code == 410
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {
            "detail": "Frozen v1 research evidence is withheld pending source-rights review."
        }
        assert "private_event_rows" not in response.text
    finally:
        app.dependency_overrides.clear()


def test_research_evidence_api_withholding_happens_before_catalog_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def reject(_repo: SnapshotRepository) -> None:
        nonlocal called
        called = True
        raise AssertionError("the retired public route must not read the v1 catalog")

    monkeypatch.setattr(app_module, "build_research_evidence", reject)
    with TestClient(app) as client:
        response = client.get("/api/v1/research-evidence")
    assert response.status_code == 410
    assert called is False
    assert response.headers["cache-control"] == "no-store"
