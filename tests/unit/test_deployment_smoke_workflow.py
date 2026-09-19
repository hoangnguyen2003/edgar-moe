from pathlib import Path

WORKFLOW = Path(".github/workflows/deployment-smoke.yml")


def test_deployment_smoke_runs_for_successful_production_deployments() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "  deployment_status:" in text
    assert "github.event.deployment_status.state == 'success'" in text
    assert "github.event.deployment.environment == 'Production'" in text
    assert "startsWith(github.event.deployment_status.target_url, 'https://')" in text


def test_deployment_smoke_keeps_manual_origin_override() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "  workflow_dispatch:" in text
    assert "github.event_name == 'workflow_dispatch' && inputs.deployment_url" in text
    assert "vars.EDGAR_MOE_PUBLIC_DEPLOYMENT_URL" in text
    assert "github.event.deployment_status.target_url" in text
