from pathlib import Path

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


def test_provider_restore_workflow_protects_source_and_target_evidence() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "EDGAR_MOE_RESTORE_SOURCE_DATABASE_URL" in text
    assert "EDGAR_MOE_RESTORE_TARGET_DATABASE_URL" in text
    assert "EDGAR_MOE_RESTORE_SOURCE_AUDITOR_DATABASE_URL" in text
    assert "EDGAR_MOE_RESTORE_TARGET_AUDITOR_DATABASE_URL" in text
    assert "source and isolated target identities are not distinct" in text
    assert "isolated target is not empty" in text
    assert "pg_dump --format=custom" in text
    assert "pg_restore --no-owner --no-privileges --exit-on-error" in text
    assert "--clean" not in text
    assert "registry.dump" in text
    assert '"$WORK_DIR/pg-dump.error"' in text
    assert '"$WORK_DIR/pg-restore.error"' in text
    assert '"$REHEARSAL_DIR/pg-restore.error"' not in text
    assert "Remove private temporary dump" in text
    assert "retention-days: 30" in text
    assert "SHA256SUMS" in text
