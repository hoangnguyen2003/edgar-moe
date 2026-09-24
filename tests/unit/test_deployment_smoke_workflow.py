import importlib.util
from pathlib import Path

FAILURE_SCRIPT = Path("scripts/write_deployment_failure_evidence.py")
SPEC = importlib.util.spec_from_file_location("write_deployment_failure_evidence", FAILURE_SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

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


def test_deployment_smoke_requires_exact_serving_commit_for_production() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert (
        "ref: ${{ github.event_name == 'deployment_status' && github.event.deployment.sha || github.sha }}"
        in text
    )
    assert "github.event_name == 'deployment_status' && github.event.deployment.sha" in text
    assert (
        'if [[ "$GITHUB_EVENT_NAME" == "deployment_status" && -z "$EXPECTED_COMMIT_SHA" ]]' in text
    )
    assert 'smoke_args+=(--expect-commit "$EXPECTED_COMMIT_SHA")' in text
    assert "expected_commit_sha:" in text


def test_failed_production_deployments_retain_redacted_evidence() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    script = FAILURE_SCRIPT.read_text(encoding="utf-8")

    assert "record-failed-deployment:" in text
    assert "github.event.deployment_status.state == 'failure'" in text
    assert "github.event.deployment_status.state == 'error'" in text
    assert "github.event.deployment_status.state == 'inactive'" in text
    assert "deployment-failure.json" in text
    assert "deployment_failed" in script
    assert "actions/upload-artifact@" in text
    assert "retention-days: 30" in text
    assert "Fail the deployment gate" in text


def test_failure_report_drops_query_and_fragment_from_target_url() -> None:
    report = MODULE.build_failure_report(
        state="failure",
        environment="Production",
        sha="abc123",
        deployment_id="42",
        status_id="43",
        target_url="https://example.vercel.app/deployment?token=secret#logs",
        checked_at="2026-09-20T00:00:00+00:00",
    )

    assert report["status"] == "deployment_failed"
    assert report["deployment"]["target_url"] == "https://example.vercel.app/deployment"

    userinfo_report = MODULE.build_failure_report(
        state="error",
        environment="Production",
        sha="abc123",
        deployment_id="42",
        status_id="44",
        target_url="https://operator:secret@example.vercel.app/deployment",
        checked_at="2026-09-20T00:00:00+00:00",
    )
    assert userinfo_report["deployment"]["target_url"] == ""
