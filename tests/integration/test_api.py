import importlib.util
import json
import re
from hashlib import sha256
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from edgar_moe.api.app import SpaStaticFiles, app, get_forward_registry, get_repository
from edgar_moe.api.repository import SnapshotRepository

# The deployment smoke check's HTML parser, so the docs page is held to the same
# external-scripts-only rule that production is probed with.
_SMOKE_PATH = Path(__file__).parents[2] / "scripts" / "smoke_deployment.py"
_SMOKE_SPEC = importlib.util.spec_from_file_location("smoke_deployment", _SMOKE_PATH)
assert _SMOKE_SPEC is not None and _SMOKE_SPEC.loader is not None
_SMOKE = importlib.util.module_from_spec(_SMOKE_SPEC)
_SMOKE_SPEC.loader.exec_module(_SMOKE)


def fixture_snapshot() -> dict:
    event = {
        "accession_number": "0000000000-26-000001",
        "event_id": "event-1",
        "security_id": "asset-1",
        "ticker": "TEST",
        "company_name": "Test Company",
        "form": "10-Q",
        "accepted_at": "2026-01-01T21:00:00Z",
        "entry_date": "2026-01-02",
        "horizon_date": "2026-02-02",
        "industry_code": "3571",
        "score": 0.1,
        "rank": 0.95,
        "direction": "long",
        "expert_weights": {"text": 0.5, "fundamental": 0.3, "market": 0.2},
        "top_attributions": [{"feature": "text_expert", "contribution": 0.03}],
        "realized_abnormal_return": None,
        "filing_url": "https://www.sec.gov/example",
    }
    return {
        "metadata": {
            "project": "EDGAR-MoE",
            "version": "0.1.0",
            "generated_at": "2026-01-01T00:00:00Z",
            "as_of": "2026-01-01",
            "data_mode": "authenticated_locked_test",
            "research_only": True,
            "disclaimer": "test",
            "selection_hash": "a" * 64,
            "locked_test_hash": "b" * 64,
        },
        "summary": {
            "title": "Test",
            "thesis": "Test thesis",
            "universe": "Test",
            "horizon_sessions": 20,
            "events": 1,
            "issuers": 1,
            "development_events": 1,
            "validation_events": 1,
            "test_events": 1,
            "latest_signal_count": 1,
        },
        "predictive_metrics": {"validation": {"rank_ic": 0.1}, "locked_test": {"rank_ic": 0.2}},
        "portfolio_scenarios": [{"cost_bps": 10, "sharpe": 1.0}],
        "experiments": [
            {"name": "MoE", "family": "multimodal", "validation_rmse": 0.1, "selected": True}
        ],
        "equity_curves": {
            "cost_10bps": [{"date": "2026-01-01", "equity": 1.0, "drawdown": 0.0, "turnover": 0.0}]
        },
        "events": [event],
        "latest_signals": [event],
        "methodology": {
            "target": "20D",
            "split": "time",
            "model": "MoE",
            "portfolio": "neutral",
            "costs": "10bps",
            "limitations": ["test"],
        },
        "freshness": {
            "status": "ok",
            "last_successful_update": "2026-01-01T00:00:00Z",
            "next_scheduled_update": None,
            "message": "ok",
        },
    }


def test_api_contracts(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(fixture_snapshot()), encoding="utf-8")
    repo = SnapshotRepository(path)
    app.dependency_overrides[get_repository] = lambda: repo
    app.dependency_overrides[get_forward_registry] = lambda: None
    with TestClient(app) as client:
        health = client.get("/api/v1/health", headers={"X-Request-ID": "smoke-probe-123"})
        assert health.json()["status"] == "ok"
        assert health.headers["x-request-id"] == "smoke-probe-123"
        assert client.get("/api/v1/summary").status_code == 200
        events = client.get("/api/v1/events?direction=long").json()
        assert events["total"] == 1
        assert client.get("/api/v1/events/0000000000-26-000001").json()["ticker"] == "TEST"
        assert client.get("/api/v1/equity-curves?cost_bps=17").status_code == 422
        status = client.get("/api/v1/forward/status")
        assert status.json()["configured"] is False
        governance = client.get("/api/v1/governance")
        assert governance.status_code == 200
        governance_payload = governance.json()
        assert governance_payload["schema_version"] == 1
        assert governance_payload["frozen_v1"]["path"] == "data/demo/snapshot.json"
        assert governance_payload["frozen_v1"]["sha256"] == sha256(path.read_bytes()).hexdigest()
        assert governance_payload["public_data"]["raw_sources_public"] is False
        assert governance_payload["forward_status"]["available"] is False
        assert {control["status"] for control in governance_payload["controls"]} == {
            "enforced",
            "pending_operator_evidence",
        }
        assert status.headers["x-content-type-options"] == "nosniff"
        assert status.headers["x-frame-options"] == "DENY"
        assert status.headers["cross-origin-opener-policy"] == "same-origin"
        assert status.headers["cross-origin-resource-policy"] == "same-site"
        assert status.headers["content-security-policy"].find("object-src 'none'") >= 0
        # Free-text search runs server-side over every event, not one loaded page.
        assert client.get("/api/v1/events?q=test%20comp").json()["total"] == 1
        assert client.get("/api/v1/events?q=TEST").json()["total"] == 1
        assert client.get("/api/v1/events?q=missing").json() == {
            "items": [],
            "next_cursor": None,
            "total": 0,
        }
        assert client.get("/api/v1/events?q=").status_code == 422
        assert client.get("/api/v1/events?q=" + "a" * 65).status_code == 422
        assert client.get("/api/v1/events?ticker=" + "A" * 33).status_code == 422
        assert client.get("/api/v1/events?cursor=" + "1" * 21).status_code == 422
        assert client.get("/api/v1/events/not-an-accession").status_code == 422
    app.dependency_overrides.clear()


def _csp_directive(policy: str, name: str) -> list[str]:
    for directive in policy.split(";"):
        tokens = directive.split()
        if tokens and tokens[0] == name:
            return tokens[1:]
    raise AssertionError(f"CSP is missing {name}")


def test_api_docs_render_under_the_content_security_policy() -> None:
    with TestClient(app) as client:
        docs = client.get("/api/docs")
        assert docs.status_code == 200
        assert docs.headers["content-type"].startswith("text/html")
        script_sources = _csp_directive(docs.headers["content-security-policy"], "script-src")
        assert "'unsafe-inline'" not in script_sources

        scripts = _SMOKE._script_elements(docs.text)
        assert len(scripts) == 2
        for attributes, body in scripts:
            # Browsers block inline script under this CSP, which blanked the page.
            assert not body.strip()
            source = attributes.get("src")
            assert source
            assert source.startswith("/") or any(
                source.startswith(allowed) for allowed in script_sources
            )
        cdn_tags = re.findall(r"<(?:script|link)\b[^>]*https://cdn\.jsdelivr\.net[^>]*>", docs.text)
        assert len(cdn_tags) == 2
        for tag in cdn_tags:
            assert re.search(r'integrity="sha384-[A-Za-z0-9+/=]{64}"', tag)
            assert 'crossorigin="anonymous"' in tag

        initializer = client.get("/api/docs/swagger-init.js")
        assert initializer.status_code == 200
        assert initializer.headers["content-type"].startswith("text/javascript")
        assert "SwaggerUIBundle" in initializer.text
        assert "/api/openapi.json" in initializer.text
        assert client.get("/api/openapi.json").status_code == 200
        # FastAPI's default ReDoc and root docs pages are not part of the public surface.
        assert client.get("/redoc").status_code == 404
        assert client.get("/docs").status_code == 404


def test_spa_static_files_fall_back_to_index(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<main>terminal</main>", encoding="utf-8")
    (tmp_path / "asset.js").write_text("export {};", encoding="utf-8")
    static_app = FastAPI()
    static_app.mount("/", SpaStaticFiles(directory=tmp_path, html=True), name="web")

    with TestClient(static_app) as client:
        assert client.get("/research").text == "<main>terminal</main>"
        assert client.get("/asset.js").text == "export {};"
        assert client.get("/missing.js").status_code == 404
        unknown_api = client.get("/api/v1/missing")
        assert unknown_api.status_code == 404
        assert unknown_api.headers["content-type"].startswith("application/json")
