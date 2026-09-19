from __future__ import annotations

import importlib.util
from pathlib import Path

import psycopg

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
    error = RegistryStateError(
        "failed at https://provider.example/objects token=abcdef123"
    )

    message = _MODULE._failure_message(error)

    assert message.startswith("forward cycle failed: RegistryStateError:")
    assert "provider.example" not in message
    assert "abcdef123" not in message
