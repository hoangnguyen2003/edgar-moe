from pathlib import Path

WORKFLOW = Path(".github/workflows/provider-r2-evidence-audit.yml")


def test_provider_r2_workflow_is_manual_read_only_and_retained() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in text
    assert "permissions:\n  contents: read" in text
    assert "actions/checkout@v7" in text
    assert "astral-sh/setup-uv@v7" in text
    assert "uv sync --locked --extra dev" in text
    assert "actions/setup-go@v6" in text
    assert "actions/upload-artifact@v7" in text
    assert "AUDITOR_DATABASE_URL" in text
    assert "AUDITOR_R2_SECRET_ACCESS_KEY" in text
    assert "-timeout 10m -stale-after 96h" in text
    assert "retention-days: 30" in text


def test_provider_r2_workflow_has_no_repair_or_writer_path() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "--repair" not in text
    assert "EDGAR_MOE_REGISTRY_DATABASE_URL" not in text
    assert "EDGAR_MOE_R2_ACCESS_KEY_ID" not in text
    assert "EDGAR_MOE_R2_SECRET_ACCESS_KEY" not in text
    assert "SHA256SUMS" in text
    assert "scripts/validate_redacted_artifacts.py" in text
    assert "steps.redaction.outcome == 'success'" in text
    assert "Fail unless the provider audit passed" in text

    setup_uv = text.index("astral-sh/setup-uv@v7")
    install = text.index("uv sync --locked --extra dev")
    redaction = text.index("scripts/validate_redacted_artifacts.py")
    assert setup_uv < install < redaction
