"""Keep the replacement diagnostic separate from the hash-bound forecast scope."""

from pathlib import Path

import yaml


def test_replacement_inspection_has_only_manual_main_branch_metadata_reads() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load(
        (root / ".github/workflows/inspect-private-scheduler.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    assert trigger == {"workflow_dispatch": None}
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"] == {
        "group": "deploy-forward-scheduler",
        "cancel-in-progress": False,
    }
    job = workflow["jobs"]["inspect"]
    assert job["if"] == "${{ github.ref == 'refs/heads/main' }}"
    assert job["environment"] == {"name": "scheduler"}
    credential_steps = [step for step in job["steps"] if "env" in step]
    assert len(credential_steps) == 2
    assert credential_steps[0]["run"] == (
        "node ops/scheduler/manage-private-scheduler.mjs VERIFY_ACTIVE "
        "reports/private-scheduler-inspection/state.json"
    )
    assert credential_steps[1]["run"] == (
        "node ops/scheduler/inspect-private-cron-events.mjs "
        "reports/private-scheduler-inspection/events.json replacement-20261114"
    )
    for step, secret in zip(
        credential_steps,
        ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ANALYTICS_READ_TOKEN"),
        strict=True,
    ):
        assert step["env"] == {
            "CLOUDFLARE_API_TOKEN": "${{ secrets." + secret + " }}",
            "CLOUDFLARE_ACCOUNT_ID": "${{ secrets.CLOUDFLARE_ACCOUNT_ID }}",
        }
    receipt = next(step for step in job["steps"] if step.get("id") == "receipt")
    assert receipt["if"] == "${{ always() }}"
    assert "validate_redacted_artifacts.py" in receipt["run"]
    upload = job["steps"][-1]
    assert upload["if"] == "${{ always() && steps.receipt.outcome == 'success' }}"
    assert upload["with"]["retention-days"] == 14
