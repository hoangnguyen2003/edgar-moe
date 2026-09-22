from __future__ import annotations

import subprocess
import sys
from pathlib import Path

CONFIG = Path("ops/scheduler/wrangler.toml.example")
SCRIPT = Path("scripts/validate_scheduler_config.py")
WORKFLOW_SCRIPT = Path("scripts/validate_scheduler_workflow.py")
WORKFLOW = Path(".github/workflows/deploy-forward-scheduler.yml")


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
