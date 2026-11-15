from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import yaml

spec = importlib.util.spec_from_file_location(
    "scheduler_runtime_targets", "scripts/verify_scheduler_runtime_targets.py"
)
assert spec and spec.loader
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def environment():
    result = {name: "test-secret-value" for name in probe.REQUIRED}
    result["EDGAR_MOE_REGISTRY_DATABASE_URL"] = (
        "postgresql://user:sentinel-password@private.invalid/database"
    )
    result["EDGAR_MOE_R2_ENDPOINT_URL"] = "https://objects.invalid"
    result["EDGAR_MOE_R2_BUCKET"] = "private-bucket"
    return result


def test_exact_targets_match_without_retaining_credentials_or_target_values():
    expected = {
        "registry": hashlib.sha256(b"private.invalid:5432/database").hexdigest(),
        "storage": hashlib.sha256(b"objects.invalid/private-bucket").hexdigest(),
    }
    report = probe.compare_targets(environment(), expected)
    assert report["status"] == "matches_private_reference"
    assert report["provider_execution_authorized"] is False
    assert (
        report["database_connections"]
        == report["storage_connections"]
        == report["provider_requests"]
        == 0
    )
    raw = json.dumps(report)
    for value in [
        "sentinel-password",
        "private.invalid",
        "objects.invalid",
        "private-bucket",
        "test-secret-value",
    ]:
        assert value not in raw


def test_changed_target_and_missing_or_invalid_settings_fail_closed():
    assert probe.compare_targets(environment(), {})["status"] == "different_targets"
    assert probe.compare_targets({}, {})["status"] == "missing_configuration"
    settings = environment()
    settings["EDGAR_MOE_REGISTRY_DATABASE_URL"] = (
        "postgresql://user:sentinel-password@private.invalid:bad/db"
    )
    report = probe.compare_targets(settings, {})
    assert report["status"] == "invalid_targets"
    assert "sentinel-password" not in json.dumps(report)


def test_runtime_metadata_workflow_never_connects_or_installs_provider_runtime():
    doc = yaml.safe_load(Path(".github/workflows/verify-forward-scheduler-runtime.yml").read_text())
    assert doc.get("on", doc.get(True)) == {"workflow_dispatch": None}
    assert doc["permissions"] == {"contents": "read"}
    job = doc["jobs"]["verify"]
    assert job["if"] == "${{ github.ref == 'refs/heads/main' }}"
    assert job["timeout-minutes"] == 3
    secrets = [step for step in job["steps"] if "secrets." in str(step)]
    assert len(secrets) == 1
    assert set(secrets[0]["env"]) == set(probe.REQUIRED)
    assert secrets[0]["run"].startswith("python3 scripts/verify_scheduler_runtime_targets.py ")
    assert "uv sync" not in str(job) and "wrangler" not in str(job)
