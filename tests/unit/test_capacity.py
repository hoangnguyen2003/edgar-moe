from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

from edgar_moe.capacity import build_capacity_baseline
from edgar_moe.forward.database import RegistryDatabase


def snapshot_payload() -> dict[str, object]:
    return {
        "metadata": {"as_of": "2026-09-01", "data_mode": "test"},
        "summary": {},
        "predictive_metrics": {},
        "portfolio_scenarios": [{"cost_bps": 10}],
        "experiments": [],
        "equity_curves": {"cost_10bps": []},
        "events": [],
        "latest_signals": [],
        "methodology": {},
        "freshness": {},
    }


def test_capacity_baseline_records_local_snapshot_registry_and_storage(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps(snapshot_payload()), encoding="utf-8")
    database_path = tmp_path / "registry.sqlite3"
    database = RegistryDatabase(f"sqlite:///{database_path}")
    database.create_schema()
    database.dispose()
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "one.bin").write_bytes(b"123")

    report = build_capacity_baseline(
        snapshot_path=snapshot,
        database_url=f"sqlite:///{database_path}",
        paths=[cache],
        workflow_runtime_seconds=12.5,
    )

    assert report["status"] == "observed"
    assert report["snapshot"]["status"] == "observed"
    assert report["snapshot"]["event_count"] == 0
    assert report["registry"]["status"] == "observed"
    assert report["storage"][0]["bytes"] == 3
    assert report["workflow"] == {
        "runtime_seconds": 12.5,
        "runtime_source": "operator_input",
    }
    assert report["decision"]["status"] == "requires_provider_measurements"


def test_capacity_baseline_marks_missing_snapshot_and_unconfigured_registry(tmp_path: Path) -> None:
    report = build_capacity_baseline(
        snapshot_path=tmp_path / "missing.json",
        database_url=None,
        paths=[tmp_path / "missing-cache"],
    )

    assert report["status"] == "partial"
    assert report["snapshot"]["status"] == "unavailable"
    assert report["registry"]["status"] == "not_configured"
    assert report["storage"][0]["status"] == "not_present"


def test_capacity_baseline_rejects_negative_workflow_runtime(tmp_path: Path) -> None:
    try:
        build_capacity_baseline(
            snapshot_path=tmp_path / "missing.json",
            workflow_runtime_seconds=-1,
        )
    except ValueError as error:
        assert "negative" in str(error)
    else:
        raise AssertionError("negative workflow runtime should be rejected")


def test_capacity_baseline_rejects_credentials_in_api_url(tmp_path: Path) -> None:
    report = build_capacity_baseline(
        snapshot_path=tmp_path / "missing.json",
        api_url="https://user:secret@example.test",
        paths=(),
    )

    assert report["api"] == {
        "status": "unavailable",
        "error_type": "api_url_contains_credentials",
    }


def test_capacity_baseline_measures_optional_http_api(tmp_path: Path) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            payload = b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        report = build_capacity_baseline(
            snapshot_path=tmp_path / "missing.json",
            api_url=f"http://127.0.0.1:{server.server_port}",
            paths=(),
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert report["api"]["status"] == "observed"
    assert report["api"]["endpoints"]["health"]["http_status"] == 200
