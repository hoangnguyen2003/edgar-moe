"""Repository-wide GitHub Actions supply-chain invariants."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOWS = sorted(Path(".github/workflows").glob("*.yml"))
PINNED = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._/-]+@[0-9a-f]{40}$")


def _steps(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        step
        for job in workflow.get("jobs", {}).values()
        for step in job.get("steps", [])
        if isinstance(step, dict)
    ]


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda path: path.name)
def test_every_action_is_pinned_to_a_commit_sha(path: Path) -> None:
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    for step in _steps(workflow):
        if "uses" in step:
            assert PINNED.fullmatch(str(step["uses"])), f"{path.name}: {step['uses']}"
    # Keep the human-readable version next to each SHA for review and Dependabot.
    for line in path.read_text(encoding="utf-8").splitlines():
        if re.match(r"\s*(-\s+)?uses:", line):
            assert re.search(r"@[0-9a-f]{40} # v\d+\.\d+\.\d+$", line), line


def test_forward_production_secrets_reach_only_first_party_run_steps() -> None:
    path = Path(".github/workflows/forward-production.yml")
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    for job in workflow["jobs"].values():
        assert "secrets." not in str(job.get("env", {})), "job-level env exposes secrets"
        for step in job["steps"]:
            if "uses" in step:
                exposed = str(step.get("env", {})) + str(step.get("with", {}))
                assert "secrets." not in exposed, f"third-party action receives secrets: {step}"
    # The writer credential is still delivered to the steps that need it.
    text = path.read_text(encoding="utf-8")
    assert text.count("${{ secrets.EDGAR_MOE_REGISTRY_DATABASE_URL }}") == 4
