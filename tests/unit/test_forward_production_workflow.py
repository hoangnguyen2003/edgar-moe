from pathlib import Path

WORKFLOW = Path(".github/workflows/forward-production.yml")


def test_forward_workflow_validates_secrets_before_installing_runtime() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    setup_uv = text.index("astral-sh/setup-uv@")
    install = text.index("uv sync --locked --extra research --extra operations")
    validation = text.index("scripts/validate_forward_runtime_config.py")
    setup_go = text.index("actions/setup-go@")

    assert setup_uv < install < validation < setup_go
    assert "Require every private runtime secret" not in text


def test_forward_workflow_declares_the_optional_auditor_group() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    for name in (
        "EDGAR_MOE_REGISTRY_AUDITOR_DATABASE_URL",
        "EDGAR_MOE_R2_AUDITOR_ACCESS_KEY_ID",
        "EDGAR_MOE_R2_AUDITOR_SECRET_ACCESS_KEY",
    ):
        assert name in text


def test_forward_workflow_cross_validates_frozen_bundle_before_copying_it() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    validation = text.index("scripts/validate_frozen_runtime.py")
    copy = text.index("install -m 0644 ops/frozen/frozen-model.pt")

    assert validation < copy
