from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

spec = importlib.util.spec_from_file_location(
    "scheduler_config_prepare", "scripts/validate_scheduler_config.py"
)
assert spec is not None and spec.loader is not None
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)
CONFIG = Path("ops/scheduler/wrangler.prepare.toml.example")
WORKFLOW = Path(".github/workflows/prepare-forward-scheduler.yml")


def test_preparation_profile_requires_explicit_empty_cron():
    assert validator.validate_scheduler_config(CONFIG, prepare=True) == []
    assert validator.validate_scheduler_config(CONFIG) != []
    assert (
        validator.validate_scheduler_config(
            Path("ops/scheduler/wrangler.toml.example"), prepare=True
        )
        != []
    )


def test_preparation_rejects_accidental_activation_or_implicit_cron(tmp_path):
    for changed in ['crons = ["17 7 * * 2-6"]', "# omitted crons"]:
        p = tmp_path / "config.toml"
        p.write_text(CONFIG.read_text().replace("crons = []", changed))
        assert validator.validate_scheduler_config(p, prepare=True) != []


def test_preparation_keeps_private_worker_identity_and_no_credentials():
    import tomllib

    config = tomllib.loads(CONFIG.read_text())
    assert config["name"] == "edgar-moe-forward-scheduler"
    assert config["workers_dev"] is False and config["preview_urls"] is False
    assert config["observability"]["enabled"] is False
    assert "GITHUB_TOKEN" not in config["vars"]
    assert config["vars"] == validator.EXPECTED_VARS


def test_preparation_workflow_is_main_only_and_read_only_by_default():
    doc = yaml.safe_load(WORKFLOW.read_text())
    on = doc.get("on", doc.get(True))
    assert set(on) == {"workflow_dispatch"}
    assert on["workflow_dispatch"]["inputs"]["stage"]["default"] == "VERIFY"
    assert on["workflow_dispatch"]["inputs"]["stage"]["options"] == ["VERIFY", "PREPARE"]
    assert doc["permissions"] == {"contents": "read"}
    assert doc["concurrency"] == {"group": "deploy-forward-scheduler", "cancel-in-progress": False}
    job = doc["jobs"]["prepare"]
    assert job["if"] == "${{ github.ref == 'refs/heads/main' }}"
    assert job["environment"] == {"name": "scheduler"}
    steps = job["steps"]
    before = next(
        s for s in steps if s.get("run", "").startswith("node ops/scheduler/verify-dormant")
    )
    upload = next(
        s for s in steps if str(s.get("uses", "")).startswith("cloudflare/wrangler-action@")
    )
    after = next(
        s for s in steps if s.get("run", "").startswith("node ops/scheduler/verify-prepared")
    )
    assert steps.index(before) < steps.index(upload) < steps.index(after)
    assert before["if"] == upload["if"] == "${{ inputs.stage == 'PREPARE' }}"
    assert upload["with"]["command"] == "deploy --config wrangler.toml"
    materialize = next(
        s
        for s in steps
        if s.get("run")
        == "cp ops/scheduler/wrangler.prepare.toml.example ops/scheduler/wrangler.toml"
    )
    dry_run = next(s for s in steps if "--dry-run" in s.get("run", ""))
    assert steps.index(materialize) < steps.index(dry_run) < steps.index(before)
    assert "--config ops/scheduler/wrangler.toml" in dry_run["run"]
    assert "env" not in materialize
    assert dry_run["env"] == {"WRANGLER_SEND_METRICS": "false"}
    assert upload["with"]["wranglerVersion"] == "4.136.2"
    assert upload["with"]["quiet"] is True
    assert upload["uses"] == "cloudflare/wrangler-action@953926a2e2182532811c01a25e53647d93bf07c0"
    for step in steps:
        if "env" in step:
            assert "GITHUB_TOKEN" not in step["env"]
        if "secrets." in str(step):
            assert "env" in step
            assert "secrets." not in str({k: v for k, v in step.items() if k != "env"})
