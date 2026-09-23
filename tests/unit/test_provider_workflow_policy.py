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


def test_provider_reader_secret_cannot_be_job_scoped(tmp_path: Path) -> None:
    for name in PROVIDER_WORKFLOWS:
        copy2(WORKFLOW_ROOT / name, tmp_path / name)

    reader = tmp_path / "provider-reader-contract-audit.yml"
    reader_text = reader.read_text(encoding="utf-8")
    reader.write_text(
        reader_text.replace(
            "    env:\n      AUDIT_DIR:",
            "    env:\n      EDGAR_MOE_REGISTRY_READ_DATABASE_URL: "
            "${{ secrets.EDGAR_MOE_REGISTRY_READ_DATABASE_URL }}\n      AUDIT_DIR:",
        ),
        encoding="utf-8",
    )

    result = _run_validator(tmp_path)

    assert result.returncode != 0
    assert "reader database secret must not be job-scoped" in result.stdout


def test_provider_reader_secret_cannot_enter_unrelated_step(tmp_path: Path) -> None:
    for name in PROVIDER_WORKFLOWS:
        copy2(WORKFLOW_ROOT / name, tmp_path / name)

    reader = tmp_path / "provider-reader-contract-audit.yml"
    reader_text = reader.read_text(encoding="utf-8")
    reader.write_text(
        reader_text.replace(
            "      - run: uv sync --locked --extra dev",
            "      - name: Install dependencies\n"
            "        env:\n"
            "          EDGAR_MOE_REGISTRY_READ_DATABASE_URL: "
            "${{ secrets.EDGAR_MOE_REGISTRY_READ_DATABASE_URL }}\n"
            "        run: uv sync --locked --extra dev",
        ),
        encoding="utf-8",
    )

    result = _run_validator(tmp_path)

    assert result.returncode != 0
    assert "reader secret must not enter Install dependencies" in result.stdout


def test_provider_reader_verifier_must_receive_secret(tmp_path: Path) -> None:
    for name in PROVIDER_WORKFLOWS:
        copy2(WORKFLOW_ROOT / name, tmp_path / name)

    reader = tmp_path / "provider-reader-contract-audit.yml"
    reader_text = reader.read_text(encoding="utf-8")
    verifier = "      - name: Run the effective reader-role verifier"
    before, after = reader_text.split(verifier, 1)
    after = after.replace(
        "        env:\n"
        "          EDGAR_MOE_REGISTRY_READ_DATABASE_URL: "
        "${{ secrets.EDGAR_MOE_REGISTRY_READ_DATABASE_URL }}\n",
        "",
        1,
    )
    reader.write_text(before + verifier + after, encoding="utf-8")

    result = _run_validator(tmp_path)

    assert result.returncode != 0
    assert "Run the effective reader-role verifier must receive the reader secret" in result.stdout


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
