from pathlib import Path

WORKFLOW = Path(".github/workflows/provider-reader-contract-audit.yml")


def test_provider_reader_workflow_is_manual_and_read_only() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in text
    assert "permissions:\n  contents: read" in text
    assert "actions/checkout@v7" in text
    assert "astral-sh/setup-uv@v7" in text
    assert "actions/upload-artifact@v7" in text
    assert "EDGAR_MOE_REGISTRY_READ_DATABASE_URL" in text
    assert "EDGAR_MOE_REGISTRY_DATABASE_URL" not in text
    assert "scripts/verify_postgres_reader.py" in text
    assert "retention-days: 30" in text


def test_provider_reader_workflow_retains_evidence_before_failing() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "if: ${{ always() }}" in text
    assert "reader-role.error" in text
    assert "SHA256SUMS" in text
    assert "scripts/validate_redacted_artifacts.py" in text
    assert "steps.redaction.outcome == 'success'" in text
    assert "Fail unless the provider contract passed" in text
