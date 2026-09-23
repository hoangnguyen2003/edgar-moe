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
    assert "reader audit secret must not be job-scoped" in result.stdout


def test_provider_reader_secret_alias_cannot_be_job_scoped(tmp_path: Path) -> None:
    for name in PROVIDER_WORKFLOWS:
        copy2(WORKFLOW_ROOT / name, tmp_path / name)

    reader = tmp_path / "provider-reader-contract-audit.yml"
    reader_text = reader.read_text(encoding="utf-8")
    reader.write_text(
        reader_text.replace(
            "    env:\n      AUDIT_DIR:",
            "    env:\n      DATABASE_ALIAS: "
            "${{secrets['EDGAR_MOE_REGISTRY_READ_DATABASE_URL']}}\n      AUDIT_DIR:",
        ),
        encoding="utf-8",
    )

    result = _run_validator(tmp_path)

    assert result.returncode != 0
    assert "reader audit secret must not be job-scoped" in result.stdout


def test_provider_reader_case_variant_secret_context_cannot_be_job_scoped(
    tmp_path: Path,
) -> None:
    for name in PROVIDER_WORKFLOWS:
        copy2(WORKFLOW_ROOT / name, tmp_path / name)

    reader = tmp_path / "provider-reader-contract-audit.yml"
    reader_text = reader.read_text(encoding="utf-8")
    reader.write_text(
        reader_text.replace(
            "    env:\n      AUDIT_DIR:",
            "    env:\n      DATABASE_ALIAS: "
            "${{ SECRETS['edgar_moe_registry_read_database_url'] }}\n      AUDIT_DIR:",
            1,
        ),
        encoding="utf-8",
    )

    result = _run_validator(tmp_path)

    assert result.returncode != 0
    assert "reader audit secret must not be job-scoped" in result.stdout


def test_provider_reader_job_rename_cannot_bypass_secret_scope(tmp_path: Path) -> None:
    for name in PROVIDER_WORKFLOWS:
        copy2(WORKFLOW_ROOT / name, tmp_path / name)

    reader = tmp_path / "provider-reader-contract-audit.yml"
    reader_text = reader.read_text(encoding="utf-8")
    reader.write_text(
        reader_text.replace("  reader-contract:\n", "  renamed-reader-audit:\n", 1),
        encoding="utf-8",
    )

    result = _run_validator(tmp_path)

    assert result.returncode != 0
    assert "reader audit job must be named reader-contract" in result.stdout


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
    assert "reader audit secret must not enter Install dependencies" in result.stdout


def test_provider_reader_secret_alias_cannot_enter_unrelated_step(tmp_path: Path) -> None:
    for name in PROVIDER_WORKFLOWS:
        copy2(WORKFLOW_ROOT / name, tmp_path / name)

    reader = tmp_path / "provider-reader-contract-audit.yml"
    reader_text = reader.read_text(encoding="utf-8")
    reader.write_text(
        reader_text.replace(
            "      - run: uv sync --locked --extra dev",
            "      - name: Install dependencies\n"
            "        env:\n"
            "          DATABASE_ALIAS: ${{secrets['EDGAR_MOE_REGISTRY_READ_DATABASE_URL']}}\n"
            "        run: uv sync --locked --extra dev",
        ),
        encoding="utf-8",
    )

    result = _run_validator(tmp_path)

    assert result.returncode != 0
    assert "reader audit secret must not enter Install dependencies" in result.stdout


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


def test_provider_workflow_policy_rejects_alternate_secret_expression_syntax(
    tmp_path: Path,
) -> None:
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
            checkout + "\n        with:\n          token: ${{secrets['UNSAFE_TOKEN']}}",
        ),
        encoding="utf-8",
    )

    result = _run_validator(tmp_path)

    assert result.returncode != 0
    assert "secret expressions may only appear" in result.stdout


def test_provider_workflow_policy_rejects_case_variant_secret_context(
    tmp_path: Path,
) -> None:
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
            checkout + "\n        with:\n          token: ${{ SeCrEtS.UNSAFE_TOKEN }}",
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


def test_r2_audit_rejects_job_scoped_credentials(tmp_path: Path) -> None:
    for name in PROVIDER_WORKFLOWS:
        copy2(WORKFLOW_ROOT / name, tmp_path / name)

    workflow = tmp_path / "provider-r2-evidence-audit.yml"
    text = workflow.read_text(encoding="utf-8")
    workflow.write_text(
        text.replace(
            "    env:\n      AUDIT_DIR:",
            "    env:\n      AUDITOR_DATABASE_URL: "
            "${{ secrets.EDGAR_MOE_REGISTRY_AUDITOR_DATABASE_URL }}\n      AUDIT_DIR:",
            1,
        ),
        encoding="utf-8",
    )

    result = _run_validator(tmp_path)

    assert result.returncode != 0
    assert "R2 audit credentials must not be job-scoped" in result.stdout


def test_r2_audit_rejects_credentials_in_dependency_setup(tmp_path: Path) -> None:
    for name in PROVIDER_WORKFLOWS:
        copy2(WORKFLOW_ROOT / name, tmp_path / name)

    workflow = tmp_path / "provider-r2-evidence-audit.yml"
    text = workflow.read_text(encoding="utf-8")
    workflow.write_text(
        text.replace(
            "      - run: uv sync --locked --extra dev",
            "      - name: Install dependencies\n"
            "        env:\n"
            "          AUDITOR_R2_SECRET_ACCESS_KEY: "
            "${{ secrets.EDGAR_MOE_R2_AUDITOR_SECRET_ACCESS_KEY }}\n"
            "        run: uv sync --locked --extra dev",
            1,
        ),
        encoding="utf-8",
    )

    result = _run_validator(tmp_path)

    assert result.returncode != 0
    assert "R2 audit credentials must not enter Install dependencies" in result.stdout


def test_r2_audit_rejects_aliased_secret_in_dependency_setup(tmp_path: Path) -> None:
    for name in PROVIDER_WORKFLOWS:
        copy2(WORKFLOW_ROOT / name, tmp_path / name)

    workflow = tmp_path / "provider-r2-evidence-audit.yml"
    text = workflow.read_text(encoding="utf-8")
    workflow.write_text(
        text.replace(
            "      - run: uv sync --locked --extra dev",
            "      - name: Install dependencies\n"
            "        env:\n"
            "          EXTRA: ${{secrets['EDGAR_MOE_R2_AUDITOR_SECRET_ACCESS_KEY']}}\n"
            "        run: uv sync --locked --extra dev",
            1,
        ),
        encoding="utf-8",
    )

    result = _run_validator(tmp_path)

    assert result.returncode != 0
    assert "R2 audit credentials must not enter Install dependencies" in result.stdout


def test_r2_auditor_requires_all_read_only_credentials(tmp_path: Path) -> None:
    for name in PROVIDER_WORKFLOWS:
        copy2(WORKFLOW_ROOT / name, tmp_path / name)

    workflow = tmp_path / "provider-r2-evidence-audit.yml"
    text = workflow.read_text(encoding="utf-8")
    auditor = "      - name: Run the independent Go auditor"
    before, after = text.split(auditor, 1)
    after = after.replace(
        "          AUDITOR_R2_SECRET_ACCESS_KEY: "
        "${{ secrets.EDGAR_MOE_R2_AUDITOR_SECRET_ACCESS_KEY }}\n",
        "",
        1,
    )
    workflow.write_text(before + auditor + after, encoding="utf-8")

    result = _run_validator(tmp_path)

    assert result.returncode != 0
    assert "Run the independent Go auditor must receive the read-only credentials" in result.stdout
