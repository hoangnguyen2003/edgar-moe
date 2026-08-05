import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from edgar_moe.api.app import SpaStaticFiles, app, get_repository
from edgar_moe.api.repository import SnapshotRepository


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
        "metadata": {"project": "EDGAR-MoE", "version": "0.1.0", "generated_at": "2026-01-01T00:00:00Z", "as_of": "2026-01-01", "data_mode": "test", "research_only": True, "disclaimer": "test"},
        "summary": {"title": "Test", "thesis": "Test thesis", "universe": "Test", "horizon_sessions": 20, "events": 1, "issuers": 1, "development_events": 1, "validation_events": 1, "test_events": 1, "latest_signal_count": 1},
        "predictive_metrics": {"validation": {"rank_ic": 0.1}, "locked_test": {"rank_ic": 0.2}},
        "portfolio_scenarios": [{"cost_bps": 10, "sharpe": 1.0}],
        "experiments": [{"name": "MoE", "family": "multimodal", "validation_rmse": 0.1, "selected": True}],
        "equity_curves": {"cost_10bps": [{"date": "2026-01-01", "equity": 1.0, "drawdown": 0.0, "turnover": 0.0}]},
        "events": [event],
        "latest_signals": [event],
        "methodology": {"target": "20D", "split": "time", "model": "MoE", "portfolio": "neutral", "costs": "10bps", "limitations": ["test"]},
        "freshness": {"status": "ok", "last_successful_update": "2026-01-01T00:00:00Z", "next_scheduled_update": None, "message": "ok"},
    }


def test_api_contracts(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(fixture_snapshot()), encoding="utf-8")
    repo = SnapshotRepository(path)
    app.dependency_overrides[get_repository] = lambda: repo
    with TestClient(app) as client:
        assert client.get("/api/v1/health").json()["status"] == "ok"
        assert client.get("/api/v1/summary").status_code == 200
        events = client.get("/api/v1/events?direction=long").json()
        assert events["total"] == 1
        assert client.get("/api/v1/events/0000000000-26-000001").json()["ticker"] == "TEST"
        assert client.get("/api/v1/equity-curves?cost_bps=17").status_code == 422
    app.dependency_overrides.clear()


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
