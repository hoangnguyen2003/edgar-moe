from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

CONFIG = Path("ops/scheduler/wrangler.toml.example")
SCRIPT = Path("scripts/validate_scheduler_config.py")
WORKFLOW_SCRIPT = Path("scripts/validate_scheduler_workflow.py")
WORKFLOW = Path(".github/workflows/deploy-forward-scheduler.yml")

_spec = importlib.util.spec_from_file_location("validate_scheduler_workflow", WORKFLOW_SCRIPT)
assert _spec is not None and _spec.loader is not None
_validator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_validator)
WRANGLER_ACTION = _validator.WRANGLER_ACTION
validate_scheduler_workflow = _validator.validate_scheduler_workflow


def test_committed_scheduler_config_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(CONFIG)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "scheduler configuration contract passed" in result.stdout


def test_manual_scheduler_deployment_workflow_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(WORKFLOW_SCRIPT), str(WORKFLOW)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "scheduler deployment workflow contract passed" in result.stdout


def test_scheduler_workflow_uses_reviewed_wrangler_action() -> None:
    assert WRANGLER_ACTION == (
        "cloudflare/wrangler-action@953926a2e2182532811c01a25e53647d93bf07c0"
    )


@pytest.mark.parametrize(
    "action",
    [
        "cloudflare/wrangler-action@v4.1.3",
        "cloudflare/wrangler-action@ebbaa1584979971c8614a24965b4405ff95890e0",
        "cloudflare/wrangler-action@" + "0" * 40,
    ],
)
def test_scheduler_workflow_rejects_unreviewed_wrangler_action(tmp_path: Path, action: str) -> None:
    workflow = tmp_path / WORKFLOW.name
    original = WORKFLOW.read_text(encoding="utf-8")
    assert original.count(WRANGLER_ACTION) == 1
    workflow.write_text(original.replace(WRANGLER_ACTION, action, 1), encoding="utf-8")

    assert "deploy job must use the pinned Wrangler action exactly once" in (
        validate_scheduler_workflow(workflow)
    )


def test_scheduler_workflow_rejects_duplicate_wrangler_deploy(tmp_path: Path) -> None:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = document["jobs"]["deploy"]["steps"]
    deploy = next(step for step in steps if step.get("uses") == WRANGLER_ACTION)
    steps.append(deploy.copy())
    workflow = tmp_path / WORKFLOW.name
    workflow.write_text(yaml.safe_dump(document), encoding="utf-8")

    assert "deploy job must use the pinned Wrangler action exactly once" in (
        validate_scheduler_workflow(workflow)
    )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("command", "preview --config wrangler.toml.example"),
        ("wranglerVersion", "latest"),
        ("quiet", False),
    ],
)
def test_scheduler_workflow_rejects_wrangler_input_drift(
    tmp_path: Path, key: str, value: str | bool
) -> None:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    deploy = next(
        step for step in document["jobs"]["deploy"]["steps"] if step.get("uses") == WRANGLER_ACTION
    )
    deploy["with"][key] = value
    workflow = tmp_path / WORKFLOW.name
    workflow.write_text(yaml.safe_dump(document), encoding="utf-8")

    assert any(
        error.startswith(f"Wrangler input {key!r} must be")
        for error in validate_scheduler_workflow(workflow)
    )


def test_scheduler_workflow_rejects_secret_outside_step_environment(tmp_path: Path) -> None:
    workflow = tmp_path / WORKFLOW.name
    workflow.write_text(
        WORKFLOW.read_text(encoding="utf-8").replace(
            "    environment:\n      name: scheduler\n",
            "    environment:\n"
            "      name: scheduler\n"
            "      leaked: ${{ secrets.CLOUDFLARE_API_TOKEN }}\n",
            1,
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(WORKFLOW_SCRIPT), str(workflow)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "scheduler deployment workflow contract failed" in result.stderr


def test_scheduler_config_rejects_secrets_and_schedule_drift(tmp_path: Path) -> None:
    config = tmp_path / "wrangler.toml"
    config.write_text(
        """
name = "edgar-moe-forward-scheduler"
main = "cloudflare-forward-scheduler.mjs"
compatibility_date = "2026-09-22"
[triggers]
crons = ["17 6 * * 2-6"]
[vars]
GITHUB_REPOSITORY = "hoangnguyen2003/edgar-moe"
GITHUB_WORKFLOW = "forward-production.yml"
GITHUB_REF = "main"
GITHUB_DEVICE = "cpu"
GITHUB_TOKEN = "should-not-be-here"
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(config)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "scheduler configuration contract failed" in result.stderr
    assert "validation error(s) detected" in result.stderr


def test_missing_scheduler_config_fails_closed(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path / "missing.toml")],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "scheduler configuration contract failed" in result.stderr
