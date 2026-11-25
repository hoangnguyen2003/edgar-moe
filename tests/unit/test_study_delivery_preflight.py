import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(".github/workflows/study-delivery-preflight.yml")
CRON = "7,22,37,52 * * * *"


@pytest.mark.parametrize(
    ("event", "attempt", "start_offset", "expected"),
    [
        ("workflow_dispatch", "1", None, "manual_setup"),
        ("schedule", "1", 2, "before_window"),
        ("schedule", "1", -1, "observed"),
        ("schedule", "1", -25, "expired"),
        ("schedule", "2", -1, "rerun_excluded"),
        ("schedule", "1", None, None),
    ],
)
def test_delivery_receipt_and_expiry(tmp_path, event, attempt, start_offset, expected):
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["probe"]["steps"]
    script = steps[0]["run"].split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    start = (
        (datetime.now(UTC) + timedelta(hours=start_offset)).strftime("%Y-%m-%dT%H:00:00Z")
        if start_offset is not None
        else ""
    )
    output = tmp_path / "output"
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
        env=os.environ
        | {
            "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": attempt,
            "GITHUB_EVENT_NAME": event,
            "GITHUB_OUTPUT": str(output),
            "DELIVERY_STARTS_AT": start,
            "DELIVERY_CRON": CRON,
        },
    )
    if expected is None:
        assert result.returncode != 0
        assert not output.exists()
        return
    assert result.returncode == 0, result.stderr
    prefix, encoded = result.stdout.split(" ", 1)
    receipt = json.loads(encoded)
    assert prefix == "study290_delivery" and receipt["status"] == expected
    assert receipt["application_requests"] == receipt["provider_requests"] == 0
    assert receipt["measurement_pairs"] == 0
    if expected == "observed":
        assert 1 <= receipt["hourly_slot"] <= 2
    assert output.exists() == (expected == "expired")
    if output.exists():
        assert output.read_text() == "expired=true\n"


def test_probe_has_no_provider_secrets_or_write_access():
    workflow = yaml.safe_load(WORKFLOW.read_text())
    assert workflow[True]["schedule"] == [{"cron": CRON}]
    assert workflow["permissions"] == {}
    assert "secrets." not in json.dumps(workflow)
    probe, stop = (workflow["jobs"][name] for name in ("probe", "stop"))
    assert probe["runs-on"] == "ubuntu-24.04"
    assert "github.event.repository.private == false" in probe["if"]
    assert len(probe["steps"]) == 1 and "GH_TOKEN" not in probe["steps"][0]["env"]
    assert stop["permissions"] == {"actions": "write"}
    assert stop["needs"] == "probe" and "outputs.expired == 'true'" in stop["if"]
    assert "study-delivery-preflight.yml" in stop["steps"][0]["run"]
    assert "observe-private-read-study.yml" not in stop["steps"][0]["run"]
