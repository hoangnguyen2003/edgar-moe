"""Keep admission-candidate CI synthetic and separate from provider workflows."""

from pathlib import Path

import yaml


def test_offline_admission_job_uses_standard_runner_without_provider_inputs() -> None:
    workflow = yaml.safe_load(Path(".github/workflows/ci.yml").read_text(encoding="utf-8"))
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["offline-read-admission"]
    assert set(job) == {"runs-on", "timeout-minutes", "steps"}
    assert job["runs-on"] == "ubuntu-24.04"
    assert job["timeout-minutes"] == 5
    checkout, node, syntax, tests = job["steps"]
    assert checkout["uses"].startswith("actions/checkout@")
    assert node["uses"].startswith("actions/setup-node@")
    assert node["with"] == {"node-version": "24", "package-manager-cache": False}
    assert all(set(step) <= {"name", "uses", "with", "run"} for step in job["steps"])
    assert syntax["run"].strip() == (
        'for module in ops/private-read-admission/*.mjs; do\n  node --check "$module"\ndone'
    )
    assert tests["run"].split() == [
        "node",
        "--test",
        "ops/private-read-admission/admission.test.mjs",
        "ops/private-read-admission/executor-process.test.mjs",
        "ops/private-read-admission/study-plan.test.mjs",
        "ops/private-read-admission/workerd.test.mjs",
    ]
