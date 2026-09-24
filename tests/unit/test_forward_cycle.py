from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import psycopg
import pytest

from edgar_moe.forward.registry import RegistryStateError

_SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "run_forward_cycle.py"
_SPEC = importlib.util.spec_from_file_location("run_forward_cycle", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_runner_failure_message_reduces_external_driver_text_to_type() -> None:
    error = psycopg.OperationalError(
        "connection failed for postgresql://runner:super-secret@example.test/registry"
    )

    message = _MODULE._failure_message(error)

    assert message == "forward cycle failed: OperationalError"
    assert "super-secret" not in message
    assert "example.test" not in message


def test_runner_failure_message_redacts_internal_error_details() -> None:
    error = RegistryStateError("failed at https://provider.example/objects token=abcdef123")

    message = _MODULE._failure_message(error)

    assert message.startswith("forward cycle failed: RegistryStateError:")
    assert "provider.example" not in message
    assert "abcdef123" not in message


def test_prewarm_builds_dataset_without_forecast_or_registry_writes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    commands: list[tuple[str, ...]] = []
    dataset = tmp_path / "processed" / "dataset"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_forward_cycle.py",
            "--prewarm-only",
            "--cutoff",
            "2026-09-23",
            "--raw-root",
            str(tmp_path / "raw"),
            "--processed-root",
            str(tmp_path / "processed"),
        ],
    )
    monkeypatch.setattr(_MODULE, "validate_cutoff", lambda cutoff: cutoff)
    monkeypatch.setattr(_MODULE, "rolling_source_start", lambda cutoff, **kwargs: "2024-09-23")
    monkeypatch.setattr(_MODULE.shutil, "which", lambda command: "/bin/edgar-moe")
    monkeypatch.setattr(_MODULE, "seed_filing_documents", lambda **kwargs: 2)
    monkeypatch.setattr(_MODULE, "update_filing_cache", lambda **kwargs: 1)
    monkeypatch.setattr(_MODULE, "find_processed_dataset", lambda **kwargs: dataset)
    monkeypatch.setattr(_MODULE, "_run", lambda executable, *args: commands.append(args))

    _MODULE.main()

    assert [command[0] for command in commands] == ["refresh-data", "build-dataset"]
    assert "--embedding-cache" in commands[1]
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result == {
        "mode": "prewarm_only",
        "cutoff": "2026-09-23",
        "checkpoint": str(tmp_path / "raw" / "2026-09-23"),
        "dataset": str(dataset),
        "new_cached_filings": 1,
    }


def test_prewarm_rejects_a_diagnostic_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_forward_cycle.py", "--prewarm-only", "--diagnostic-output", "report.json"],
    )

    with pytest.raises(SystemExit, match="2"):
        _MODULE.main()
