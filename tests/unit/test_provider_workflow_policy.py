from pathlib import Path
from shutil import copy2
from subprocess import run
from sys import executable

WORKFLOW_ROOT = Path(".github/workflows")
PROVIDER_WORKFLOWS = (
    "provider-evidence-preflight.yml",
    "provider-reader-contract-audit.yml",
    "provider-r2-evidence-audit.yml",
    "provider-restore-rehearsal.yml",
)


def _run_validator(root: Path):
    return run(
        [executable, "scripts/validate_provider_workflows.py", "--workflow-root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_provider_workflows_satisfy_the_safety_contract() -> None:
    result = _run_validator(WORKFLOW_ROOT)

    assert result.returncode == 0
    assert "provider workflow safety contract passed (4 workflows)" in result.stdout


def test_provider_workflow_policy_rejects_secret_in_step_with_mapping(tmp_path: Path) -> None:
    for name in PROVIDER_WORKFLOWS:
        copy2(WORKFLOW_ROOT / name, tmp_path / name)

    reader = tmp_path / "provider-reader-contract-audit.yml"
    reader_text = reader.read_text(encoding="utf-8")
    checkout = next(
        line
        for line in reader_text.splitlines()
        if line.startswith("      - uses: actions/checkout@")
    )
    reader.write_text(
        reader_text.replace(
            checkout,
            checkout + "\n        with:\n          token: ${{ secrets.UNSAFE_TOKEN }}",
        ),
        encoding="utf-8",
    )

    result = _run_validator(tmp_path)

    assert result.returncode != 0
    assert "secret expressions may only appear" in result.stdout


def test_provider_workflow_policy_rejects_non_manual_trigger(tmp_path: Path) -> None:
    for name in PROVIDER_WORKFLOWS:
        copy2(WORKFLOW_ROOT / name, tmp_path / name)

    reader = tmp_path / "provider-reader-contract-audit.yml"
    reader.write_text(
        reader.read_text(encoding="utf-8").replace(
            "on:\n  workflow_dispatch:",
            "on:\n  push:\n    branches: [main]\n  workflow_dispatch:",
        ),
        encoding="utf-8",
    )

    result = _run_validator(tmp_path)

    assert result.returncode != 0
    assert "manual-only workflow_dispatch" in result.stdout


def test_provider_workflow_policy_rejects_mutable_action_tags(tmp_path: Path) -> None:
    for name in PROVIDER_WORKFLOWS:
        copy2(WORKFLOW_ROOT / name, tmp_path / name)

    reader = tmp_path / "provider-reader-contract-audit.yml"
    reader_text = reader.read_text(encoding="utf-8")
    checkout = next(
        line
        for line in reader_text.splitlines()
        if line.startswith("      - uses: actions/checkout@")
    )
    reader.write_text(
        reader_text.replace(checkout, "      - uses: actions/checkout@v7"), encoding="utf-8"
    )

    result = _run_validator(tmp_path)

    assert result.returncode != 0
    assert "pinned to a full commit SHA" in result.stdout
