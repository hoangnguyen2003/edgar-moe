from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

INPUT_SCRIPT = Path("scripts/validate_forward_diagnostic_history_inputs.py")
WORKFLOW_SCRIPT = Path("scripts/validate_forward_diagnostic_history_workflow.py")
WORKFLOW = Path(".github/workflows/build-forward-diagnostic-history.yml")

_input_spec = importlib.util.spec_from_file_location("diagnostic_history_inputs", INPUT_SCRIPT)
assert _input_spec is not None and _input_spec.loader is not None
inputs = importlib.util.module_from_spec(_input_spec)
_input_spec.loader.exec_module(inputs)


def test_selection_requires_paired_bounded_successful_run_inputs() -> None:
    assert (
        inputs.validate_selection(
            run_ids="101,202,303",
            artifact_names="forward-diagnostic-2026-09-17,forward-diagnostic-2026-09-18,forward-diagnostic-2026-09-19",
            minimum_reports="3",
        )
        == ()
    )
    assert "selection_lengths_mismatch" in inputs.validate_selection(
        run_ids="101,202",
        artifact_names="forward-diagnostic-2026-09-17",
        minimum_reports="2",
    )


def test_selection_rejects_unsafe_values_duplicates_and_unbounded_inputs() -> None:
    errors = inputs.validate_selection(
        run_ids="101,101",
        artifact_names="forward-diagnostic-2026-09-17,../../private",
        minimum_reports="9",
    )

    assert "run_id_duplicate" in errors
    assert "artifact_name_invalid" in errors
    assert "minimum_reports_out_of_range" in errors


def test_input_validator_cli_does_not_echo_selection_values() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(INPUT_SCRIPT),
            "--run-ids",
            "not-a-run-id",
            "--artifact-names",
            "secret-artifact-name",
            "--minimum-reports",
            "1",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "secret-artifact-name" not in result.stderr
    assert "artifact_name_invalid" in result.stderr or "run_id_invalid" in result.stderr


def test_history_workflow_contract_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(WORKFLOW_SCRIPT), str(WORKFLOW)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "workflow contract passed" in result.stdout


def test_history_workflow_rejects_write_permissions(tmp_path: Path) -> None:
    unsafe = tmp_path / "unsafe.yml"
    unsafe.write_text(
        WORKFLOW.read_text(encoding="utf-8").replace("actions: read", "actions: write"),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(WORKFLOW_SCRIPT), str(unsafe)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "validation error(s) detected" in result.stderr


def test_history_module_imports_without_copilot_cycle() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from edgar_moe.forward.diagnostic_history import DiagnosticHistoryError, read_forward_diagnostic_history",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
