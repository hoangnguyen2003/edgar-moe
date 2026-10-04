from pathlib import Path
from shutil import copy2
from subprocess import run
from sys import executable

import pytest
import yaml

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


@pytest.mark.parametrize("mutation", ["skip_helper", "ignore_failure", "backend_ip", "public_only"])
def test_restore_identity_and_all_schema_emptiness_cannot_be_bypassed(tmp_path, mutation):
    for name in PROVIDER_WORKFLOWS:
        copy2(WORKFLOW_ROOT / name, tmp_path / name)
    path = tmp_path / "provider-restore-rehearsal.yml"
    text = path.read_text()
    command = 'uv run python scripts/verify_restore_identities.py --output "$REHEARSAL_DIR/preflight.json"'
    if mutation == "skip_helper":
        text = text.replace(command, "true")
    elif mutation == "ignore_failure":
        text = text.replace(command, command + " || true")
    elif mutation == "backend_ip":
        text = text.replace(command, "echo inet_server_addr\n          " + command)
    else:
        text = text.replace(
            "n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'", "n.nspname = 'public'"
        )
    path.write_text(text)
    result = _run_validator(tmp_path)
    assert result.returncode != 0
    assert "endpoint identity checks" in result.stdout or "all non-system schemas" in result.stdout


@pytest.mark.parametrize(
    "mutation", ["whole_database_dump", "missing_functions", "ignored_function_failure"]
)
def test_restore_rejects_export_scope_and_function_installation_bypass(
    tmp_path: Path, mutation: str
) -> None:
    for name in PROVIDER_WORKFLOWS:
        copy2(WORKFLOW_ROOT / name, tmp_path / name)
    path = tmp_path / "provider-restore-rehearsal.yml"
    text = path.read_text()
    if mutation == "whole_database_dump":
        text = text.replace(
            "uv run python scripts/export_registry_dump.py", "pg_dump --format=custom"
        )
    elif mutation == "missing_functions":
        text = text.replace(
            '--file="$WORK_DIR/registry-functions.sql"', '--file="$WORK_DIR/unreviewed.sql"'
        )
    else:
        text = text.replace('if [[ "$functions_code" -ne 0 ]]', "if [[ 0 -ne 0 ]]")
    path.write_text(text)
    result = _run_validator(tmp_path)
    assert result.returncode != 0
    assert (
        "scoped registry exporter" in result.stdout or "reviewed trigger functions" in result.stdout
    )


def test_r2_permissions_cannot_receive_object_store_credentials(tmp_path: Path) -> None:
    for name in PROVIDER_WORKFLOWS:
        copy2(WORKFLOW_ROOT / name, tmp_path / name)
    workflow = tmp_path / "provider-r2-evidence-audit.yml"
    before, after = workflow.read_text().split(
        "      - name: Verify auditor database permissions", 1
    )
    after = after.replace(
        "        run: uv run python scripts/verify_postgres_auditor.py",
        "          AUDITOR_R2_ACCESS_KEY_ID: ${{ secrets.EDGAR_MOE_R2_AUDITOR_ACCESS_KEY_ID }}\n"
        "        run: uv run python scripts/verify_postgres_auditor.py",
        1,
    )
    workflow.write_text(before + "      - name: Verify auditor database permissions" + after)
    result = _run_validator(tmp_path)
    assert result.returncode != 0
    assert (
        "Verify auditor database permissions must receive the read-only credentials"
        in result.stdout
    )


def test_r2_go_audit_cannot_bypass_failed_permissions(tmp_path: Path) -> None:
    for name in PROVIDER_WORKFLOWS:
        copy2(WORKFLOW_ROOT / name, tmp_path / name)
    workflow = tmp_path / "provider-r2-evidence-audit.yml"
    workflow.write_text(
        workflow.read_text().replace(" && steps.grants.outcome == 'success'", " || true", 1)
    )
    result = _run_validator(tmp_path)
    assert result.returncode != 0
    assert "Go audit must require passed auditor permissions" in result.stdout


def test_r2_final_gate_cannot_ignore_failed_permissions(tmp_path: Path) -> None:
    for name in PROVIDER_WORKFLOWS:
        copy2(WORKFLOW_ROOT / name, tmp_path / name)
    workflow = tmp_path / "provider-r2-evidence-audit.yml"
    workflow.write_text(
        workflow.read_text().replace(" || steps.grants.outcome != 'success'", "", 1)
    )
    result = _run_validator(tmp_path)
    assert result.returncode != 0
    assert "final audit gate must reject failed auditor permissions" in result.stdout


def test_provider_preflight_rejects_job_scoped_secrets(tmp_path: Path) -> None:
    for name in PROVIDER_WORKFLOWS:
        copy2(WORKFLOW_ROOT / name, tmp_path / name)
    workflow = tmp_path / "provider-evidence-preflight.yml"
    workflow.write_text(
        workflow.read_text(encoding="utf-8").replace(
            "    env:\n      REPORT_DIR:",
            "    env:\n      DATABASE_ALIAS: "
            "${{ secrets.EDGAR_MOE_REGISTRY_DATABASE_URL }}\n      REPORT_DIR:",
            1,
        ),
        encoding="utf-8",
    )

    result = _run_validator(tmp_path)

    assert result.returncode != 0
    assert "provider secrets must not be job-scoped" in result.stdout


def test_provider_preflight_rejects_secrets_in_dependency_setup(tmp_path: Path) -> None:
    for name in PROVIDER_WORKFLOWS:
        copy2(WORKFLOW_ROOT / name, tmp_path / name)
    workflow = tmp_path / "provider-evidence-preflight.yml"
    workflow.write_text(
        workflow.read_text(encoding="utf-8").replace(
            "      - run: uv sync --locked --extra dev",
            "      - name: Install dependencies\n"
            "        env:\n"
            "          DATABASE_ALIAS: ${{ secrets.EDGAR_MOE_REGISTRY_DATABASE_URL }}\n"
            "        run: uv sync --locked --extra dev",
            1,
        ),
        encoding="utf-8",
    )

    result = _run_validator(tmp_path)

    assert result.returncode != 0
    assert "provider secrets must not enter Install dependencies" in result.stdout


def test_provider_preflight_requires_reviewed_secret_set(tmp_path: Path) -> None:
    for name in PROVIDER_WORKFLOWS:
        copy2(WORKFLOW_ROOT / name, tmp_path / name)
    workflow = tmp_path / "provider-evidence-preflight.yml"
    workflow.write_text(
        workflow.read_text(encoding="utf-8").replace(
            "          EDGAR_MOE_REGISTRY_READ_DATABASE_URL: "
            "${{ secrets.EDGAR_MOE_REGISTRY_READ_DATABASE_URL }}\n",
            "",
            1,
        ),
        encoding="utf-8",
    )

    result = _run_validator(tmp_path)

    assert result.returncode != 0
    assert (
        "Run value-redacting prerequisite check must receive only its reviewed secrets"
        in result.stdout
    )


def test_provider_restore_rejects_job_scoped_secrets(tmp_path: Path) -> None:
    for name in PROVIDER_WORKFLOWS:
        copy2(WORKFLOW_ROOT / name, tmp_path / name)
    workflow = tmp_path / "provider-restore-rehearsal.yml"
    workflow.write_text(
        workflow.read_text(encoding="utf-8").replace(
            "    env:\n      CONFIRM_ISOLATED_TARGET:",
            "    env:\n      DATABASE_ALIAS: "
            "${{ secrets.EDGAR_MOE_RESTORE_TARGET_DATABASE_URL }}\n"
            "      CONFIRM_ISOLATED_TARGET:",
            1,
        ),
        encoding="utf-8",
    )

    result = _run_validator(tmp_path)

    assert result.returncode != 0
    assert "provider secrets must not be job-scoped" in result.stdout


def test_provider_restore_rejects_secrets_in_dependency_setup(tmp_path: Path) -> None:
    for name in PROVIDER_WORKFLOWS:
        copy2(WORKFLOW_ROOT / name, tmp_path / name)
    workflow = tmp_path / "provider-restore-rehearsal.yml"
    workflow.write_text(
        workflow.read_text(encoding="utf-8").replace(
            "      - run: uv sync --locked --extra dev",
            "      - name: Install dependencies\n"
            "        env:\n"
            "          DATABASE_ALIAS: ${{ secrets.EDGAR_MOE_RESTORE_TARGET_DATABASE_URL }}\n"
            "        run: uv sync --locked --extra dev",
            1,
        ),
        encoding="utf-8",
    )

    result = _run_validator(tmp_path)

    assert result.returncode != 0
    assert "provider secrets must not enter Install dependencies" in result.stdout


def test_provider_restore_requires_secret_on_restore_step(tmp_path: Path) -> None:
    for name in PROVIDER_WORKFLOWS:
        copy2(WORKFLOW_ROOT / name, tmp_path / name)
    workflow = tmp_path / "provider-restore-rehearsal.yml"
    text = workflow.read_text(encoding="utf-8")
    marker = "      - name: Restore into the isolated target"
    before, after = text.split(marker, 1)
    after = after.replace(
        "        env:\n"
        "          TARGET_DATABASE_URL: ${{ secrets.EDGAR_MOE_RESTORE_TARGET_DATABASE_URL }}\n",
        "",
        1,
    )
    workflow.write_text(before + marker + after, encoding="utf-8")

    result = _run_validator(tmp_path)

    assert result.returncode != 0
    assert (
        "Restore into the isolated target must receive only its reviewed secrets" in result.stdout
    )


@pytest.mark.parametrize(
    "step_name",
    [
        "Verify source auditor permissions",
        "Verify empty target auditor permissions",
        "Export source registry counts",
        "Audit source registry and R2 evidence",
        "Dump source into private temporary storage",
        "Re-check target emptiness before restore",
        "Restore into the isolated target",
        "Export restored registry counts",
        "Verify restored target reader permissions",
        "Audit restored registry and R2 evidence",
        "Probe restored read path",
    ],
)
def test_restore_cannot_bypass_permission_dependency_chain(tmp_path: Path, step_name: str) -> None:
    for name in PROVIDER_WORKFLOWS:
        copy2(WORKFLOW_ROOT / name, tmp_path / name)
    path = tmp_path / "provider-restore-rehearsal.yml"
    workflow = yaml.safe_load(path.read_text())
    step = next(
        step
        for step in workflow["jobs"]["restore-rehearsal"]["steps"]
        if step.get("name") == step_name
    )
    step["if"] = "${{ always() }}"
    path.write_text(yaml.safe_dump(workflow))
    result = _run_validator(tmp_path)
    assert result.returncode != 0
    assert f"{step_name} must preserve the restore permission gates" in result.stdout


@pytest.mark.parametrize(
    ("step_name", "mutation", "message"),
    [
        ("Verify source auditor permissions", "owner", "must receive only its reviewed secrets"),
        (
            "Verify empty target auditor permissions",
            "owner",
            "must receive only its reviewed secrets",
        ),
        (
            "Verify restored target reader permissions",
            "owner",
            "must receive only its reviewed secrets",
        ),
        (
            "Verify restored target reader permissions",
            "profile",
            "must retain the correct permission profile",
        ),
        ("Write redacted rehearsal summary", "summary", "must reject failed restored_grants"),
        ("Write redacted rehearsal summary", "loop", "must reject failed restored_grants"),
        ("Fail unless the rehearsal passed", "final", "final restore gate must require summary"),
    ],
)
def test_restore_rejects_owner_substitution_wrong_profile_or_incomplete_final_gate(
    tmp_path: Path, step_name: str, mutation: str, message: str
) -> None:
    for name in PROVIDER_WORKFLOWS:
        copy2(WORKFLOW_ROOT / name, tmp_path / name)
    path = tmp_path / "provider-restore-rehearsal.yml"
    workflow = yaml.safe_load(path.read_text())
    step = next(
        step
        for step in workflow["jobs"]["restore-rehearsal"]["steps"]
        if step.get("name") == step_name
    )
    if mutation == "owner":
        step["env"]["AUDITOR_DATABASE_URL"] = "${{ secrets.EDGAR_MOE_RESTORE_TARGET_DATABASE_URL }}"
    elif mutation == "profile":
        step["run"] = step["run"].replace(
            "--profile registry-reader", "--profile empty-restore-target"
        )
    elif mutation == "summary":
        del step["env"]["RESTORED_GRANTS_OUTCOME"]
    elif mutation == "loop":
        step["run"] = step["run"].replace('"$RESTORED_GRANTS_OUTCOME"', '"success"', 1)
    else:
        step["if"] = "${{ always() && steps.redaction.outcome != 'success' }}"
    path.write_text(yaml.safe_dump(workflow))
    result = _run_validator(tmp_path)
    assert result.returncode != 0
    assert message in result.stdout


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


def test_r2_audit_rejects_case_variant_secret_name_at_job_scope(tmp_path: Path) -> None:
    for name in PROVIDER_WORKFLOWS:
        copy2(WORKFLOW_ROOT / name, tmp_path / name)

    workflow = tmp_path / "provider-r2-evidence-audit.yml"
    text = workflow.read_text(encoding="utf-8")
    workflow.write_text(
        text.replace(
            "    env:\n      AUDIT_DIR:",
            "    env:\n      ALIAS: ${{ secrets.edgar_moe_r2_auditor_secret_access_key }}\n"
            "      AUDIT_DIR:",
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


def test_r2_audit_rejects_case_variant_secret_name_in_dependency_setup(
    tmp_path: Path,
) -> None:
    for name in PROVIDER_WORKFLOWS:
        copy2(WORKFLOW_ROOT / name, tmp_path / name)

    workflow = tmp_path / "provider-r2-evidence-audit.yml"
    text = workflow.read_text(encoding="utf-8")
    workflow.write_text(
        text.replace(
            "      - run: uv sync --locked --extra dev",
            "      - name: Install dependencies\n"
            "        env:\n"
            "          EXTRA: ${{secrets['edgar_moe_r2_auditor_secret_access_key']}}\n"
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
