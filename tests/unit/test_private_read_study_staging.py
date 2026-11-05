from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

SPEC = importlib.util.spec_from_file_location(
    "portfolio_study_stager", "scripts/stage_private_read_study.py"
)
assert SPEC and SPEC.loader
stager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stager)


def test_standalone_dependencies_are_hash_pinned_without_research_or_deployment_sdks():
    raw = stager.requirements(Path("uv.lock")).decode()
    assert raw.startswith("--require-hashes\n")
    for line in raw.splitlines()[1:]:
        assert "==" in line and "--hash=sha256:" in line
    assert "fastapi==" in raw and "psycopg-binary==" in raw and "certifi==" in raw
    for unexpected in ("torch==", "transformers==", "boto3==", "httpx==", "vercel=="):
        assert unexpected not in raw


def test_missing_or_untrusted_distribution_cannot_be_staged(tmp_path):
    lock = tmp_path / "uv.lock"
    lock.write_text(
        '[[package]]\nname="fastapi"\nversion="1.0"\nsource={registry="https://untrusted.invalid"}\n'
    )
    with pytest.raises(ValueError, match="reviewed public registry"):
        stager.requirements(lock)


def test_observer_is_dormant_and_never_receives_database_credentials():
    workflow = yaml.safe_load(Path(".github/workflows/observe-private-read-study.yml").read_text())
    job = workflow["jobs"]["observe"]
    assert "ISSUE290_STUDY_ENABLED == 'true'" in job["if"]
    env = next(step["env"] for step in job["steps"] if step.get("id") == "probe")
    assert not any("DATABASE" in name or "PASSWORD" in name for name in env)
    stop = workflow["jobs"]["stop-failed-study"]
    assert stop["permissions"] == {"actions": "write"}
    assert set(stop["steps"][0]["env"]) == {"GH_TOKEN"}
    assert "observe-private-read-study.yml" in stop["steps"][0]["run"]
    assert "156" not in stop["steps"][0]["run"]


def test_refused_probe_retains_a_zero_request_receipt(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "portfolio_study_probe_refusal", "scripts/observe_private_read_study.py"
    )
    assert spec and spec.loader
    probe = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = probe
    spec.loader.exec_module(probe)
    output = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", ["probe", "--output", str(output)])
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    monkeypatch.setattr(
        probe, "request", lambda *a: pytest.fail("A refused scope must not send requests")
    )
    assert probe.main() == 1
    report = json.loads(output.read_text())
    assert report["reason"] == "rerun_refused"
    assert report["http_requests"] == 0
    assert report["seven_day_evidence_proven"] is False
