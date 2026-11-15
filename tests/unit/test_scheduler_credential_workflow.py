from __future__ import annotations

import re
from pathlib import Path

import yaml


def test_dormant_credential_workflow_is_manual_main_only_and_bounded() -> None:
    document = yaml.safe_load(
        Path(".github/workflows/verify-dormant-scheduler.yml").read_text(encoding="utf-8")
    )
    assert document.get("on", document.get(True)) == {"workflow_dispatch": None}
    assert document["permissions"] == {"contents": "read"}
    assert document["concurrency"] == {
        "group": "verify-dormant-scheduler",
        "cancel-in-progress": False,
    }
    assert set(document["jobs"]) == {"verify"}
    job = document["jobs"]["verify"]
    assert job["if"] == "${{ github.ref == 'refs/heads/main' }}"
    assert job["runs-on"] == "ubuntu-24.04"
    assert job["timeout-minutes"] == 5
    assert job["environment"] == {"name": "scheduler"}
    steps = job["steps"]
    assert len(steps) == 7
    for step in steps:
        if "uses" in step:
            assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", step["uses"])
        assert "wrangler" not in str(step).lower()
    assert steps[0]["with"] == {"persist-credentials": False}
    assert steps[1]["with"] == {"node-version": "24", "package-manager-cache": False}
    verify = steps[2]
    assert verify["id"] == "verify"
    assert verify["continue-on-error"] is True
    assert verify["env"] == {
        "CLOUDFLARE_ACCOUNT_ID": "${{ secrets.CLOUDFLARE_ACCOUNT_ID }}",
        "CLOUDFLARE_API_TOKEN": "${{ secrets.CLOUDFLARE_API_TOKEN }}",
        "EXPECTED_WORKER_ID": "d8be7a3ac48547e98a48e99f1eb145a1",
        "EXPECTED_ACCOUNT_SHA256": (
            "d55626b5e28b4d2ac50ff058e39cd722f2779d300ca7db10f76e3e3d7966d386"
        ),
    }
    assert verify["run"] == (
        "node ops/scheduler/verify-dormant-credentials.mjs "
        "reports/scheduler-credentials/verification.json"
    )
    # Secrets must not escape the one GET-only verifier's process environment.
    assert "secrets." not in str([*steps[:2], *steps[3:]])
    assert steps[3]["run"] == (
        "python3 scripts/validate_redacted_artifacts.py --root reports/scheduler-credentials"
    )
    assert steps[4]["run"] == (
        "sha256sum reports/scheduler-credentials/verification.json "
        "> reports/scheduler-credentials/SHA256SUMS"
    )
    assert "steps.redaction.outcome == 'success'" in steps[5]["if"]
    assert "steps.hash.outcome == 'success'" in steps[5]["if"]
    assert steps[5]["with"]["path"] == "reports/scheduler-credentials/"
    assert steps[5]["with"]["retention-days"] == 7
    assert steps[5]["with"]["if-no-files-found"] == "error"
    assert "steps.verify.outcome != 'success'" in steps[6]["if"]
    assert steps[6]["run"] == "exit 1"
