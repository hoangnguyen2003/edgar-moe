from __future__ import annotations

import hashlib
import importlib.util
import json
import threading
import tomllib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from edgar_moe.api import private_read as pilot

_SPEC = importlib.util.spec_from_file_location(
    "stage_private_read_pilot",
    Path(__file__).resolve().parents[2] / "scripts/stage_private_read_pilot.py",
)
assert _SPEC is not None and _SPEC.loader is not None
_STAGER = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_STAGER)
FILES, stage = _STAGER.FILES, _STAGER.stage

NOW = datetime(2026, 10, 4, tzinfo=UTC)
URL = (
    "postgresql://edgar_recovery_auditor_20261004:private-password@"
    "ep-isolated.c-3.ap-southeast-1.aws.neon.tech/edgar_recovery_rehearsal_20261004"
    "?sslmode=verify-full&channel_binding=require"
)


def environment() -> dict[str, str]:
    return {
        "PILOT_DATABASE_URL": URL,
        "PILOT_BEARER_TOKEN": "a" * 43,
        "PILOT_ENDPOINT_SHA256": pilot.endpoint_hash(pilot.endpoint(URL)),
        "PILOT_REGISTRY_SHA256": "b" * 64,
        "PILOT_EXPIRES_AT": (NOW + timedelta(hours=1)).isoformat(),
    }


def client(reader: Any = None) -> TestClient:
    return TestClient(
        pilot.create_app(
            environment(),
            reader=reader or Mock(return_value={"synthetic": True}),
            clock=lambda: NOW,
        ),
        headers={"Authorization": "Bearer " + "a" * 43},
    )


@pytest.mark.parametrize("path", ["/v1/status", "/v1/forecasts", "/v1/performance", "/docs", "/"])
def test_authentication_precedes_any_database_access(path: str) -> None:
    reader = Mock()
    response = client(reader).get(path, headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}
    reader.assert_not_called()
    for name in ("Cache-Control", "CDN-Cache-Control", "Vercel-CDN-Cache-Control"):
        assert response.headers[name] == "private, no-store"
    assert "access-control-allow-origin" not in response.headers


def test_duplicate_or_query_token_does_not_authorize() -> None:
    reader = Mock()
    with TestClient(pilot.create_app(environment(), reader=reader, clock=lambda: NOW)) as c:
        assert c.get("/v1/status?token=" + "a" * 43).status_code == 401
        assert (
            c.get("/v1/status", headers=[("authorization", "Bearer " + "a" * 43)] * 2).status_code
            == 401
        )
    reader.assert_not_called()


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete", "options", "head"])
def test_mutating_and_other_methods_never_reach_reader(method: str) -> None:
    reader = Mock()
    response = getattr(client(reader), method)("/v1/status")
    assert response.status_code == 405
    assert response.headers["Allow"] == "GET"
    reader.assert_not_called()


@pytest.mark.parametrize(
    "path", ["/docs", "/openapi.json", "/api/v1/forward/status", "/v1/status/", "/.env"]
)
def test_no_docs_public_app_or_static_escape(path: str) -> None:
    reader = Mock()
    response = client(reader).get(path)
    assert response.status_code == 404
    reader.assert_not_called()


@pytest.mark.parametrize(
    "path",
    [
        "/v1/status?anything=1",
        "/v1/performance?limit=1",
        "/v1/forecasts?limit=500",
        "/v1/forecasts?limit=01",
        "/v1/forecasts?limit=1&limit=2",
        "/v1/forecasts?ticker=FIXT%27",
        "/v1/forecasts?sql=SELECT+1",
        "/v1/forecasts?ticker=FIXT&ticker=FIXT",
    ],
)
def test_bounded_filters_with_redacted_validation(path: str) -> None:
    reader = Mock()
    response = client(reader).get(path)
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_query"}
    reader.assert_not_called()


def test_valid_read_and_timing_and_filtered_query() -> None:
    reader = Mock(return_value={"data_mode": "synthetic_fixture"})
    c = client(reader)
    response = c.get("/v1/forecasts?ticker=FIXT&limit=2")
    assert response.status_code == 200
    assert ";process=first" in response.headers["X-Pilot-Read-Timing"]
    assert "Vary" in response.headers
    assert reader.call_args.args[1:] == ("/v1/forecasts", {"ticker": "FIXT", "limit": "2"})
    assert ";process=subsequent" in c.get("/v1/status").headers["X-Pilot-Read-Timing"]


def test_expiration_stops_reads_without_connecting() -> None:
    clock = Mock(return_value=NOW)
    reader = Mock(return_value={})
    c = TestClient(pilot.create_app(environment(), reader=reader, clock=clock))
    clock.return_value = NOW + timedelta(hours=1)
    response = c.get("/v1/status", headers={"Authorization": "Bearer " + "a" * 43})
    assert response.status_code == 410
    reader.assert_not_called()


@pytest.mark.parametrize(
    "key,value",
    [
        ("PILOT_DATABASE_URL", URL.replace(pilot.DATABASE, "neondb")),
        ("PILOT_DATABASE_URL", URL.replace(pilot.ROLE, "neondb_owner")),
        ("PILOT_DATABASE_URL", URL.replace("verify-full", "require")),
        ("PILOT_DATABASE_URL", URL + "&options=-c+default_transaction_read_only=off"),
        ("PILOT_DATABASE_URL", URL + "&host=production.neon.tech"),
        ("PILOT_DATABASE_URL", URL.replace("ep-isolated", "ep-production")),
        ("PILOT_DATABASE_URL", URL.replace("channel_binding=require", "channel_binding=prefer")),
        ("PILOT_BEARER_TOKEN", "short"),
        ("PILOT_ENDPOINT_SHA256", "x" * 64),
        ("PILOT_REGISTRY_SHA256", "x" * 64),
        ("PILOT_EXPIRES_AT", NOW.isoformat()),
        ("PILOT_EXPIRES_AT", (NOW + timedelta(days=2)).isoformat()),
        ("PILOT_EXPIRES_AT", "2026-10-05T00:00:00"),
    ],
)
def test_configuration_cannot_fall_back_to_production(key: str, value: str) -> None:
    env = environment() | {key: value, "EDGAR_MOE_REGISTRY_DATABASE_URL": "writer-url"}
    reader = Mock()
    c = TestClient(pilot.create_app(env, reader=reader, clock=lambda: NOW))
    response = c.get("/v1/status", headers={"Authorization": "Bearer " + "a" * 43})
    assert response.status_code == 503
    assert response.json() == {"error": "pilot_unavailable"}
    reader.assert_not_called()


def test_missing_config_and_driver_errors_never_disclose_secrets() -> None:
    assert TestClient(pilot.create_app({})).get("/v1/status").json() == {
        "error": "pilot_unavailable"
    }
    c = client(Mock(side_effect=RuntimeError(URL)))
    response = c.get("/v1/status")
    assert response.status_code == 503
    assert response.json() == {"error": "read_unavailable"}
    assert "private-password" not in response.text


def test_per_process_budget_no_implicit_retry() -> None:
    reader = Mock(return_value={})
    c = client(reader)
    for _ in range(pilot.MAX_REQUESTS_PER_PROCESS):
        assert c.get("/v1/status").status_code == 200
    assert c.get("/v1/status").status_code == 429
    assert reader.call_count == pilot.MAX_REQUESTS_PER_PROCESS


def test_parallel_requests_refuse_instead_of_opening_more_connections() -> None:
    entered, release = threading.Event(), threading.Event()

    def reader(*args: Any) -> dict[str, Any]:
        entered.set()
        assert release.wait(5)
        return {}

    c = client(reader)
    with ThreadPoolExecutor() as pool:
        pending = pool.submit(c.get, "/v1/status")
        assert entered.wait(5)
        try:
            assert c.get("/v1/status").status_code == 429
        finally:
            release.set()
        assert pending.result().status_code == 200


def fixture() -> dict[str, Any]:
    rows: dict[str, Any] = {table: [] for table in pilot.READER_TABLES}
    rows["forward_datasets"] = [
        {"dataset_id": "restore-fixture-dataset", "provenance": {"fixture": "ci-restore-rehearsal"}}
    ]
    rows["forward_forecasts"] = [
        {
            "forecast_id": "1",
            "ticker": "FIXT",
            "form": "10-Q",
            "entry_date": "2026-10-05",
            "score": 0.25,
            "rank": 1.0,
            "uri": URL,
        }
    ]
    rows["forward_runs"] = [{"status": "failed", "error_message": URL}]
    return rows


def test_explicit_projections_never_return_raw_rows() -> None:
    rows = fixture()
    status = pilot.response_payload(rows, "/v1/status", {})
    assert status["failed_runs"] == 1
    page = pilot.response_payload(rows, "/v1/forecasts", {"ticker": "NONE"})
    assert page["total"] == 0 and page["items"] == []
    for route in pilot.ROUTES:
        encoded = json.dumps(pilot.response_payload(rows, route, {}))
        assert "private-password" not in encoded and "error_message" not in encoded


def test_digest_and_volume_and_synthetic_marker_are_required() -> None:
    rows = fixture()
    connection = Mock()
    connection.execute.side_effect = [
        [(row,) for row in rows[table]] for table in pilot.READER_TABLES
    ]
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    assert pilot.fixture_rows(connection, digest) == rows
    connection.execute.side_effect = [
        [(row,) for row in rows[table]] for table in pilot.READER_TABLES
    ]
    with pytest.raises(pilot.PilotRefusal, match="fixture_identity_changed"):
        pilot.fixture_rows(connection, "a" * 64)
    connection.execute.side_effect = [[({},)] * 101]
    with pytest.raises(pilot.PilotRefusal, match="fixture_volume_exceeded"):
        pilot.fixture_rows(connection, digest)
    rows["forward_datasets"][0]["provenance"] = {"fixture": "not-synthetic"}
    connection.execute.side_effect = [
        [(row,) for row in rows[table]] for table in pilot.READER_TABLES
    ]
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    with pytest.raises(pilot.PilotRefusal, match="synthetic_fixture_required"):
        pilot.fixture_rows(connection, digest)


def test_read_uses_readonly_transaction_policy_and_closes_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = Mock()
    conn.execute.side_effect = [None] * 5 + [
        Mock(fetchone=lambda: (pilot.DATABASE, pilot.ROLE, pilot.ROLE)),
        Mock(fetchone=lambda: (112, "on", "3s")),
        None,
    ]
    manager = Mock(__enter__=Mock(return_value=conn), __exit__=Mock(return_value=False))
    connect = Mock(return_value=manager)
    inspector = Mock()
    monkeypatch.setattr(pilot.psycopg, "connect", connect)
    monkeypatch.setattr(pilot, "inspect_permissions", inspector)
    monkeypatch.setattr(pilot, "fixture_rows", lambda *_: fixture())
    result = pilot.read(pilot.PilotConfig.load(environment(), NOW), "/v1/status", {})
    assert result["read_policy"]["persistent_connections"] == 0
    assert result["read_policy"]["global_connection_cap_verified"] is False
    assert connect.call_args.kwargs == {"connect_timeout": 3, "autocommit": True}
    inspector.assert_called_once_with(conn, profile="registry-reader")
    statements = [call.args[0] for call in conn.execute.call_args_list]
    assert statements[0] == "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"
    assert "SET LOCAL statement_timeout = '3s'" in statements
    assert statements[-1] == "ROLLBACK"
    manager.__exit__.assert_called_once()


@pytest.mark.parametrize("wrong_identity", [True, False])
def test_live_identity_or_session_policy_failure_closes_before_fixture(
    monkeypatch: pytest.MonkeyPatch, wrong_identity: bool
) -> None:
    conn = Mock()
    identity = (
        (pilot.DATABASE, "neondb_owner", "neondb_owner")
        if wrong_identity
        else (pilot.DATABASE, pilot.ROLE, pilot.ROLE)
    )
    conn.execute.side_effect = [None] * 5 + [
        Mock(fetchone=lambda: identity),
        Mock(fetchone=lambda: (112, "off", "3s")),
        None,
    ]
    manager = Mock(__enter__=Mock(return_value=conn), __exit__=Mock(return_value=False))
    monkeypatch.setattr(pilot.psycopg, "connect", Mock(return_value=manager))
    monkeypatch.setattr(pilot, "inspect_permissions", Mock())
    fixture_reader = Mock()
    monkeypatch.setattr(pilot, "fixture_rows", fixture_reader)
    code = "observed_identity_mismatch" if wrong_identity else "session_policy_invalid"
    with pytest.raises(pilot.PilotRefusal, match=code):
        pilot.read(pilot.PilotConfig.load(environment(), NOW), "/v1/status", {})
    fixture_reader.assert_not_called()
    assert conn.execute.call_args.args == ("ROLLBACK",)
    manager.__exit__.assert_called_once()


def test_stage_is_allowlisted_and_never_contains_public_app_or_secrets(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    target = tmp_path / "bundle"
    report = stage(root, target)
    assert report["secrets_included"] is False
    assert not (target / "public").exists()
    assert not (target / ".vercel").exists()
    assert not (target / "edgar_moe/api/app.py").exists()
    assert not any(".env" in path.name for path in target.rglob("*"))
    assert len(list(target.rglob("*.py"))) == 8
    assert all((target / name).is_file() for name in FILES.values())
    with pytest.raises(FileExistsError):
        stage(root, target)
    lock = tomllib.loads((root / "uv.lock").read_text())
    versions = {package["name"]: package["version"] for package in lock["package"]}
    for line in (target / "requirements.txt").read_text().splitlines():
        if line and not line.startswith("#"):
            name, version = line.split("==")
            assert versions[name] == version
