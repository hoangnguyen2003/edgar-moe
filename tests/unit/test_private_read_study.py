from __future__ import annotations

import hashlib
import importlib.util
import json
import runpy
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

SPEC = importlib.util.spec_from_file_location(
    "portfolio_read_study_app", "ops/private-read-study/study_app.py"
)
assert SPEC and SPEC.loader
study = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = study
SPEC.loader.exec_module(study)
NOW = datetime(2026, 11, 6, tzinfo=UTC)
URL = "postgresql://study290_reader_20261105:sentinel-private-password@ep-study.c-4.ap-southeast-1.aws.neon.tech/study290_fixture_20261105?sslmode=verify-full&channel_binding=require"


def policy():
    return {
        "scope": study.SCOPE,
        "study_id": "portfolio290_fixture_20261105",
        "starts_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(days=7)).isoformat(),
        "target": {
            "kind": "neon-hosted",
            "database": "study290_fixture_20261105",
            "role": "study290_reader_20261105",
            "host": "ep-study.c-4.ap-southeast-1.aws.neon.tech",
            "port": 5432,
        },
        "contract_sha256": "b" * 64,
        "traffic": {
            "hourly_pairs": 168,
            "requests_per_pair": 2,
            "verification_reads": 24,
            "total_read_attempts": 360,
        },
    }


def environment(p=None):
    return {
        "STUDY_POLICY_SHA256": hashlib.sha256(study.canonical(p or policy())).hexdigest(),
        "STUDY_READ_DATABASE_URL": URL,
        "STUDY_BEARER_TOKEN": "a" * 64,
    }


def result():
    return study.ReadResult(
        b'{"synthetic":true}',
        {
            key: 123
            for key in (
                "observer_connect",
                "reader_connect",
                "integrity",
                "service",
                "snapshot",
                "lifecycle",
            )
        },
        True,
        True,
    )


def client(reader=None, **kwargs):
    return TestClient(
        study.create_app(
            policy(),
            environment(),
            reader=reader or Mock(return_value=result()),
            clock=lambda: NOW,
            **kwargs,
        ),
        headers={"Authorization": "Bearer " + "a" * 64},
    )


@pytest.mark.parametrize("path", ["/", "/docs", "/openapi.json", "/v1/status", "/v1/performance"])
def test_every_path_authenticates_before_the_reader(path):
    reader = Mock()
    r = client(reader).get(path, headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
    reader.assert_not_called()
    assert all(
        r.headers[name] == "private, no-store"
        for name in ("cache-control", "cdn-cache-control", "vercel-cdn-cache-control")
    )
    assert "access-control-allow-origin" not in r.headers


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete", "head", "options"])
def test_only_get_reaches_the_reader(method):
    reader = Mock()
    assert getattr(client(reader), method)("/v1/status").status_code == 405
    reader.assert_not_called()


def test_unknown_paths_queries_and_duplicate_authorization_are_not_admitted():
    reader = Mock()
    c = client(reader)
    assert c.get("/v1/forecasts?limit=1000").status_code == 400
    assert c.get("/v1/forecasts?ticker=untrusted").status_code == 400
    assert c.get("/api/v1/forward/status").status_code == 404
    assert (
        c.get("/v1/status", headers=[("authorization", "Bearer " + "a" * 64)] * 2).status_code
        == 401
    )
    reader.assert_not_called()


def test_success_reports_process_marker_and_numeric_timings_without_claiming_cold_start():
    reader = Mock(return_value=result())
    c = client(reader)
    first = c.get("/v1/status")
    assert first.status_code == 200
    assert "process=first" in first.headers["x-study-read-timing"]
    assert "cold" not in first.headers["x-study-read-timing"]
    assert "process=subsequent" in c.get("/v1/ticker-filter").headers["x-study-read-timing"]
    assert reader.call_args.args[1] == "ticker_filter"
    assert first.headers["x-study-policy-sha256"] == environment()["STUDY_POLICY_SHA256"]
    assert "sentinel-private-password" not in str(first.headers)


@pytest.mark.parametrize(
    "now,status", [(NOW - timedelta(seconds=1), 425), (NOW + timedelta(days=7), 410)]
)
def test_absolute_window_blocks_before_any_connection(now, status):
    reader = Mock()
    app = study.create_app(policy(), environment(), reader=reader, clock=lambda: now)
    assert (
        TestClient(app)
        .get("/v1/status", headers={"Authorization": "Bearer " + "a" * 64})
        .status_code
        == status
    )
    reader.assert_not_called()


@pytest.mark.parametrize(
    "url",
    [
        URL.replace("verify-full", "require"),
        URL.replace("channel_binding=require", "channel_binding=prefer"),
        URL.replace("study290_reader_20261105", "private_read_pilot_reader_20261012"),
        URL.replace("study290_fixture_20261105", "private_read_pilot_20261012"),
        URL + "&options=-cdefault_transaction_read_only=off",
        URL + "&hostaddr=127.0.0.1",
        URL + "&sslrootcert=/tmp/untrusted-ca.pem",
        URL.replace("ep-study.", "ep-study-pooler."),
    ],
)
def test_no_old_pilot_writer_pooler_or_tls_fallback(url):
    env = environment() | {"STUDY_READ_DATABASE_URL": url}
    with pytest.raises(study.StudyRefusal, match="configuration_refused"):
        study.StudyConfig.load(policy(), env)


def test_no_writer_environment_fallback_or_scope_extension():
    env = {"EDGAR_MOE_REGISTRY_DATABASE_URL": URL, "PILOT_DATABASE_URL": URL}
    with pytest.raises(study.StudyRefusal):
        study.StudyConfig.load(policy(), environment() | env | {"STUDY_READ_DATABASE_URL": ""})
    p = policy() | {"expires_at": (NOW + timedelta(days=8)).isoformat()}
    with pytest.raises(study.StudyRefusal):
        study.StudyConfig.load(p, environment(p))
    with pytest.raises(study.StudyRefusal):
        study.StudyConfig.load(policy() | {"scope": "expired_pilot"}, environment())


@pytest.mark.parametrize(
    "name", ["PGSERVICE", "PGHOSTADDR", "PGOPTIONS", "PGSSLROOTCERT", "PGPASSWORD", "PGPASSFILE"]
)
def test_libpq_environment_overrides_refuse_before_any_reader(name):
    reader = Mock()
    app = study.create_app(
        policy(), environment() | {name: "untrusted"}, reader=reader, clock=lambda: NOW
    )
    response = TestClient(app).get("/v1/status", headers={"Authorization": "Bearer " + "a" * 64})
    assert response.status_code == 503
    reader.assert_not_called()


def test_hosted_connection_supplies_ca_bundle_and_redacts_tls_failure(tmp_path, monkeypatch):
    import certifi
    import psycopg

    prep = Path("ops/private-read-admission/study-prep-20261027/portable-kernel-v1").resolve()
    monkeypatch.syspath_prepend(str(prep))
    import snapshot_kernel as kernel

    raw = runpy.run_path(str(prep / "build.py"))["sql_contract"]()
    (tmp_path / "contract.json").write_text(json.dumps(raw))
    contract = kernel.SqlContract(
        **{
            name: raw[name]
            for name in ("session", "original_session", "schema", "fixture", "source_sha256")
        }
    )
    p = policy()
    p["target"].update(
        scope="separate-study290-snapshot-v1",
        profile_sha256="c" * 64,
        schema_sha256="d" * 64,
        sql_contract_sha256=contract.sha256,
        fixture=tuple((name, 0, "e" * 64) for name in kernel.TABLES),
        sample_now=NOW.isoformat(),
    )
    p["contract_sha256"] = hashlib.sha256((tmp_path / "contract.json").read_bytes()).hexdigest()
    config = study.StudyConfig.load(p, environment(p))
    connector = Mock(side_effect=psycopg.OperationalError("sentinel-sensitive-tls-detail"))
    monkeypatch.setattr(psycopg, "connect", connector)
    with pytest.raises(study.StudyRefusal, match="^read_or_closure_refused$"):
        study.read(config, "status", tmp_path)
    assert connector.call_count == 1
    assert connector.call_args.kwargs["sslrootcert"] == certifi.where()
    assert connector.call_args.kwargs["connect_timeout"] == 5


def test_read_failure_stops_the_process_without_leaking_driver_details_or_retrying():
    reader = Mock(side_effect=RuntimeError(URL))
    c = client(reader)
    assert c.get("/v1/status").json() == {"error": "read_unavailable"}
    assert c.get("/v1/status").json() == {"error": "study_stopped"}
    assert reader.call_count == 1


def test_unconfirmed_closure_or_unsafe_timing_is_not_a_success():
    for bad in (
        study.ReadResult(b"{}", result().timing_us, True, False),
        study.ReadResult(b"{}", {"secret": "sentinel-private-password"}, True, True),
    ):
        r = client(Mock(return_value=bad)).get("/v1/status")
        assert r.status_code == 503
        assert "sentinel" not in r.text
        assert "x-study-read-timing" not in r.headers
