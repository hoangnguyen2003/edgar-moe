import subprocess
import sys
from pathlib import Path

WORKFLOW = Path(".github/workflows/forward-production.yml")
BOUNDARY_SCRIPT = Path("scripts/validate_forward_schedule_boundary.py")


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


def test_forward_workflow_prunes_old_runtime_caches_with_a_separate_scope() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    cleanup = text.index("prune-forward-runtime-caches:")
    cleanup_text = text[cleanup:]

    assert "needs: forecast-and-settle" in cleanup_text
    assert "needs.forecast-and-settle.result != 'cancelled'" in cleanup_text
    assert "actions: write" in cleanup_text
    assert "GH_TOKEN: ${{ github.token }}" in cleanup_text
    assert 'startswith("forward-runtime-")' in cleanup_text
    assert ".[2:]" in cleanup_text
    assert "gh api --method DELETE" in cleanup_text
    assert "remaining_bytes > 10000000000" in cleanup_text
    assert "${{ secrets." not in cleanup_text


def test_forward_schedule_boundary_contract_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(BOUNDARY_SCRIPT), str(WORKFLOW)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "forward schedule boundary contract passed" in result.stdout


def test_forward_schedule_boundary_rejects_accidental_schedule_removal(tmp_path: Path) -> None:
    workflow = tmp_path / WORKFLOW.name
    workflow.write_text(
        WORKFLOW.read_text(encoding="utf-8").replace(
            '  schedule:\n    # 07:17 UTC Tuesday-Saturday is after the prior U.S. SEC acceptance window\n    # and leaves at least six hours before the next regular NYSE open.\n    - cron: "17 7 * * 2-6"\n',
            "",
            1,
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(BOUNDARY_SCRIPT), str(workflow)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "forward schedule boundary contract failed" in result.stderr


def test_forward_schedule_boundary_rejects_cron_drift(tmp_path: Path) -> None:
    workflow = tmp_path / WORKFLOW.name
    workflow.write_text(
        WORKFLOW.read_text(encoding="utf-8").replace(
            '    - cron: "17 7 * * 2-6"', '    - cron: "17 6 * * 2-6"', 1
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(BOUNDARY_SCRIPT), str(workflow)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "forward schedule boundary contract failed" in result.stderr
