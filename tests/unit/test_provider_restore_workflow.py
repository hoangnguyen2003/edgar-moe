import json
import os
from pathlib import Path
from subprocess import run

import pytest
import yaml

WORKFLOW = Path(".github/workflows/provider-restore-rehearsal.yml")


def test_provider_restore_workflow_is_manual_and_explicitly_gated() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in text
    assert "confirm_isolated_target:" in text
    assert "I_UNDERSTAND_ISOLATED_TARGET" in text
    assert "default: CANCEL" in text
    assert "permissions:\n  contents: read" in text
    assert "concurrency:" in text
    assert "timeout-minutes: 45" in text


def test_postgresql_18_setup_is_shared_with_disposable_restore_ci() -> None:
    provider = yaml.safe_load(WORKFLOW.read_text())["jobs"]["restore-rehearsal"]
    ci = yaml.safe_load(Path(".github/workflows/ci.yml").read_text())["jobs"][
        "postgres-restore-rehearsal"
    ]
    assert ci["services"]["postgres"]["image"] == "postgres:18"
    for job in (provider, ci):
        setup = next(
            step
            for step in job["steps"]
            if step.get("name") == "Install reviewed PostgreSQL client tools"
        )
        assert setup["run"] == "bash scripts/setup_postgres_client.sh"
        assert "env" not in setup
    script = Path("scripts/setup_postgres_client.sh").read_text()
    assert "postgresql-client-18" in script
    assert "--no-install-recommends" in script
    assert "secrets." not in script
    result = run(
        ["bash", "scripts/setup_postgres_client.sh"],
        env={"PATH": os.defpath},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "restricted to the Linux Actions runner" in result.stderr


def test_provider_restore_workflow_protects_source_and_target_evidence() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "EDGAR_MOE_RESTORE_SOURCE_DATABASE_URL" in text
    assert "EDGAR_MOE_RESTORE_TARGET_DATABASE_URL" in text
    assert "EDGAR_MOE_RESTORE_SOURCE_AUDITOR_DATABASE_URL" in text
    assert "EDGAR_MOE_RESTORE_TARGET_AUDITOR_DATABASE_URL" in text
    assert 'scripts/verify_restore_identities.py --output "$REHEARSAL_DIR/preflight.json"' in text
    assert "inet_server_addr" not in text
    assert "n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'" in text
    assert "scripts/export_registry_dump.py" in text
    assert '"$WORK_DIR/registry-functions.sql"' in text
    assert '"$REHEARSAL_DIR/dump-scope.json"' in text
    assert "pg_dump --format=custom" not in text
    assert "pg_restore --no-owner --no-privileges --exit-on-error" in text
    assert "--clean" not in text
    assert "registry.dump" in text
    assert '"$WORK_DIR/pg-dump.error"' in text
    assert '"$WORK_DIR/pg-restore.error"' in text
    assert '"$REHEARSAL_DIR/pg-restore.error"' not in text
    assert "Remove private temporary dump" in text
    assert "retention-days: 30" in text
    assert "SHA256SUMS" in text
    assert "scripts/validate_redacted_artifacts.py" in text
    assert "steps.redaction.outcome == 'success'" in text


def test_runbook_never_uses_owner_credentials_for_read_verification() -> None:
    text = Path("docs/restore-rehearsal.md").read_text()
    assert 'export AUDITOR_DATABASE_URL="$SOURCE_AUDITOR_DATABASE_URL"' in text
    assert 'export AUDITOR_DATABASE_URL="$RESTORE_AUDITOR_DATABASE_URL"' in text
    assert 'EDGAR_MOE_REGISTRY_READ_DATABASE_URL="$RESTORE_AUDITOR_DATABASE_URL"' in text
    assert 'export AUDITOR_DATABASE_URL="$SOURCE_DATABASE_URL"' not in text
    assert 'export AUDITOR_DATABASE_URL="$RESTORE_DATABASE_URL"' not in text
    assert 'EDGAR_MOE_REGISTRY_READ_DATABASE_URL="$RESTORE_DATABASE_URL"' not in text
    for profile in ("evidence", "empty-restore-target", "registry-reader"):
        assert f"scripts/verify_postgres_auditor.py --profile {profile}" in text
    assert "post-restore check is mandatory" in text


@pytest.mark.parametrize(
    ("gate", "outcome"),
    [
        ("", "success"),
        *(
            (gate, outcome)
            for gate in (
                "SOURCE_GRANTS_OUTCOME",
                "EMPTY_TARGET_GRANTS_OUTCOME",
                "RESTORED_GRANTS_OUTCOME",
            )
            for outcome in ("failure", "skipped")
        ),
    ],
)
def test_summary_executes_fail_closed_for_each_permission_gate(
    tmp_path: Path, gate: str, outcome: str
) -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    summary = next(
        step
        for step in workflow["jobs"]["restore-rehearsal"]["steps"]
        if step.get("name") == "Write redacted rehearsal summary"
    )
    environment = dict.fromkeys(summary["env"], "success")
    if gate:
        environment[gate] = outcome
    environment.update(
        {
            "PATH": os.defpath,
            "REHEARSAL_DIR": str(tmp_path),
            "GITHUB_OUTPUT": str(tmp_path / "outputs"),
            "GITHUB_RUN_ID": "synthetic-run",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_SHA": "0" * 40,
        }
    )
    result = run(["bash", "-c", summary["run"]], env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    report = json.loads((tmp_path / "summary.json").read_text())
    assert report["status"] == ("failed" if gate else "passed")
    assert report["steps"]["source_grants"] == environment["SOURCE_GRANTS_OUTCOME"]
    assert report["steps"]["empty_target_grants"] == environment["EMPTY_TARGET_GRANTS_OUTCOME"]
    assert report["steps"]["restored_grants"] == environment["RESTORED_GRANTS_OUTCOME"]
