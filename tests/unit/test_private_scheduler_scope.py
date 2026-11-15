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


def signed(stamp="2026-11-18T07:17:00.000Z", mode="forecast"):
    key = "12" * 32
    return key, {
        "device": "cpu",
        "mode": mode,
        "scheduled_at": stamp,
        "signature": hmac.new(key.encode(), scope.message(stamp, mode), hashlib.sha256).hexdigest(),
    }


def test_signed_slot_requires_real_time_main_ref_and_expected_workflow_context():
    key, inputs = signed()
    now = datetime(2026, 11, 18, 7, 18, tzinfo=UTC)
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
    for stamp in ("2026-11-08T07:17:00.000Z", "2026-11-18T07:18:00.000Z"):
        key, altered = signed(stamp)
        with pytest.raises(ValueError):
            scope.admit(altered, key=key, now=now, event="workflow_dispatch", ref="refs/heads/main")


def test_activation_must_finish_before_the_probe_and_does_not_extend_dates():
    deadline = datetime.fromisoformat(scope.PROBE_DATE + "T06:17:00+00:00")
    scope.admit_activation(deadline - timedelta(seconds=1))
    for now in (deadline, deadline + timedelta(days=7), deadline.replace(tzinfo=None)):
        with pytest.raises(ValueError, match="activation"):
            scope.admit_activation(now)


def test_predecessor_scope_is_retained_and_predecessor_signatures_are_not_admitted():
    path = Path("ops/issue156/private-scheduler-scope-20261106.json")
    assert (
        hashlib.sha256(path.read_bytes()).hexdigest()
        == "0e5d81c5e005be948fa8deb71f1466248ab4620de4b43529b3ab3a609d83ad95"
    )
    for day in ("2026-11-07", "2026-11-10", "2026-11-11"):
        key, payload = signed(day + "T07:17:00.000Z")
        with pytest.raises(ValueError):
            scope.admit(
                payload,
                key=key,
                now=datetime.fromisoformat(day + "T07:18:00+00:00"),
                event="workflow_dispatch",
                ref="refs/heads/main",
            )


def test_node_worker_and_python_gate_interoperate_for_both_signed_modes():
    import shutil
    import subprocess

    if shutil.which("node") is None:
        pytest.skip("Node is required for cross-runtime HMAC verification")
    code = """
    import {privateInputs, CRON, DATES, PROBE_DATE} from './ops/scheduler/private-forward-scheduler.mjs';
    const env = {GITHUB_TOKEN:'synthetic-token', GITHUB_REPOSITORY:'hoangnguyen2003/edgar-moe',
      GITHUB_WORKFLOW:'forward-private-scheduler.yml', GITHUB_REF:'main', GITHUB_DEVICE:'cpu',
      SCHEDULER_SIGNATURE_KEY:'12'.repeat(32)};
    const result=[];
    for (const day of [PROBE_DATE, DATES[0]]) {
      const event={cron:CRON, scheduledTime:Date.parse(`${day}T07:17:00.000Z`)};
      result.push(await privateInputs(event,env,event.scheduledTime));
    }
    console.log(JSON.stringify(result));
    """
    rows = json.loads(
        subprocess.check_output(["node", "--input-type=module", "-e", code], text=True)
    )
    assert [row["mode"] for row in rows] == ["delivery_probe", "forecast"]
    for payload in rows:
        now = datetime.fromisoformat(payload["scheduled_at"].replace("Z", "+00:00"))
        assert (
            scope.admit(
                payload,
                key="12" * 32,
                now=now,
                event="workflow_dispatch",
                ref="refs/heads/main",
                allow_delivery_probe=True,
            )
            == now
        )


def test_scope_binds_private_destinations_frozen_inputs_and_finite_limits(tmp_path):
    record = scope.read_scope(Path.cwd())
    assert record["bounds"]["maximum_schedule_slots"] == 4
    assert not record["bounds"]["global_source_gate_release"]
    assert not record["bounds"]["publication_of_provider_data_or_model_outputs"]
    assert record["private_cache_seed"]["size_bytes"] < 1_000_000_000
    destination = tmp_path / "ops/issue156"
    destination.mkdir(parents=True)
    (tmp_path / scope.SCOPE_PATH).write_text(json.dumps(record) + " ")
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


def test_cron_diagnostic_stage_uses_main_branch_scoped_metadata_without_upload_or_secret_writes():
    doc = yaml.safe_load(Path(".github/workflows/deploy-private-scheduler.yml").read_text())
    trigger = doc.get("on", doc.get(True))
    assert "VERIFY_CRON_EVENTS" in trigger["workflow_dispatch"]["inputs"]["stage"]["options"]
    job = doc["jobs"]["manage"]
    assert job["if"] == "${{ github.ref == 'refs/heads/main' }}"
    assert job["environment"]["name"] == "scheduler"
    diagnostic = next(
        s for s in job["steps"] if "inspect-private-cron-events.mjs" in s.get("run", "")
    )
    assert diagnostic["if"] == "${{ inputs.stage == 'VERIFY_CRON_EVENTS' }}"
    assert set(diagnostic["env"]) == {"CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"}
    for step in job["steps"]:
        if any(
            command in step.get("run", "")
            for command in ("INSTALL_KEY", "upload-private-worker.mjs", "wrangler deploy")
        ):
            assert "VERIFY_CRON_EVENTS" not in step["if"]
