from __future__ import annotations

from fastapi.testclient import TestClient

from edgar_moe.api.app import app

REVIEW_DETAIL = (
    "Prospective forecasts and outcomes are withheld from the public application "
    "pending source-rights review. The registry remains private."
)


def test_public_forward_status_reports_only_publication_policy() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/forward/status")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "registry_read_ms=" not in response.headers["x-edgar-read-timing"]
    assert response.json() == {
        "public_visibility": "withheld_review",
        "message": REVIEW_DETAIL,
    }


def test_public_forward_data_routes_withhold_registry_records() -> None:
    routes = (
        "/api/v1/forward/runs",
        "/api/v1/forward/forecasts",
        "/api/v1/forward/performance",
        "/api/v1/forward/data-quality",
    )

    with TestClient(app) as client:
        for path in routes:
            response = client.get(path)
            assert response.status_code == 410
            assert response.headers["cache-control"] == "no-store"
            assert response.json() == {"detail": REVIEW_DETAIL}


def test_governance_discloses_synthetic_and_withheld_public_boundaries() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/governance")

    assert response.status_code == 200
    payload = response.json()
    assert payload["public_data"]["current_output_mode"] == "synthetic_fixture"
    assert payload["public_data"]["historical_v1_served_by_application"] is False
    assert payload["public_data"]["prospective_outputs_served_by_application"] is False
    assert payload["forward_status"] == {
        "public_visibility": "withheld_review",
        "message": REVIEW_DETAIL,
    }
    assert any(
        control["key"] == "prospective_publication" and control["status"] == "withheld_review"
        for control in payload["controls"]
    )
