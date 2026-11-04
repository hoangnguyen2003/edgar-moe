from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import sys
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path("scripts").resolve()))
spec = importlib.util.spec_from_file_location(
    "private_scheduler_scope", "scripts/private_scheduler_scope.py"
)
assert spec and spec.loader
scope = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scope)


def signed(stamp="2026-11-05T07:17:00.000Z"):
    key = "12" * 32
    return key, {
        "device": "cpu",
        "scheduled_at": stamp,
        "signature": hmac.new(key.encode(), scope.message(stamp), hashlib.sha256).hexdigest(),
    }


def test_signed_slot_requires_real_time_main_ref_and_expected_workflow_context():
    key, inputs = signed()
    now = datetime(2026, 11, 5, 7, 18, tzinfo=UTC)
    assert (
        scope.admit(
            inputs, key=key, now=now, event="workflow_dispatch", ref="refs/heads/main"
        ).minute
        == 17
    )
    for changes in (
        {"key": "34" * 32},
        {"now": now - timedelta(minutes=2)},
        {"now": now + timedelta(minutes=90)},
        {"ref": "refs/heads/other"},
        {"event": "schedule"},
    ):
        kwargs = {
            "key": key,
            "now": now,
            "event": "workflow_dispatch",
            "ref": "refs/heads/main",
        } | changes
        with pytest.raises(ValueError):
            scope.admit(inputs, **kwargs)
    for stamp in ("2026-11-08T07:17:00.000Z", "2026-11-05T07:18:00.000Z"):
        key, altered = signed(stamp)
        with pytest.raises(ValueError):
            scope.admit(altered, key=key, now=now, event="workflow_dispatch", ref="refs/heads/main")


def test_scope_binds_private_destinations_frozen_inputs_and_finite_limits(tmp_path):
    record = scope.read_scope(Path.cwd())
    assert record["bounds"]["maximum_schedule_slots"] == 3
    assert not record["bounds"]["global_source_gate_release"]
    assert not record["bounds"]["publication_of_provider_data_or_model_outputs"]
    assert record["private_cache_seed"]["size_bytes"] < 1_000_000_000
    destination = tmp_path / "ops/issue156"
    destination.mkdir(parents=True)
    (destination / "private-scheduler-scope.json").write_text(json.dumps(record) + " ")
    with pytest.raises(ValueError, match="scope changed"):
        scope.read_scope(tmp_path)


def test_private_workflow_has_no_github_cron_or_public_provider_cache_and_old_hold_remains():
    profile = tomllib.loads(Path("ops/scheduler/wrangler.private.toml.example").read_text())
    assert profile["triggers"]["crons"] == [scope.CRON]
    assert profile["main"] == "private-forward-scheduler.mjs"
    assert profile["workers_dev"] is profile["preview_urls"] is profile["logpush"] is False
    workflow = yaml.safe_load(Path(".github/workflows/forward-private-scheduler.yml").read_text())
    trigger = workflow.get("on", workflow.get(True))
    assert set(trigger) == {"workflow_dispatch"}
    assert workflow["concurrency"]["group"] == "edgar-moe-forward-production"
    raw = Path(".github/workflows/forward-private-scheduler.yml").read_text()
    assert "actions/cache" not in raw
    assert "path: reports/private-scheduler/" in raw
    assert "data/private-scheduler" not in raw
    original = yaml.safe_load(Path(".github/workflows/forward-production.yml").read_text())
    assert original["jobs"]["provider-use-review"]["steps"][0]["run"].endswith("exit 1\n")
