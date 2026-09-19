from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "validate_forward_runtime_config.py"
_SPEC = importlib.util.spec_from_file_location("validate_forward_runtime_config", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def _required_environment() -> dict[str, str]:
    return {name: "configured" for name in _MODULE.REQUIRED_ENVIRONMENT}


def test_complete_required_configuration_passes_without_optional_auditor() -> None:
    assert _MODULE.validation_errors(_required_environment()) == []


def test_missing_required_configuration_reports_names_only() -> None:
    environment = _required_environment()
    environment.pop("EDGAR_MOE_R2_SECRET_ACCESS_KEY")
    environment["EDGAR_MOE_REGISTRY_DATABASE_URL"] = "postgresql://writer:super-secret@example.test/db"

    errors = _MODULE.validation_errors(environment)

    assert errors == [
        "Missing required forward-runner secret(s): EDGAR_MOE_R2_SECRET_ACCESS_KEY"
    ]
    assert "super-secret" not in " ".join(errors)


def test_partial_auditor_configuration_fails_closed() -> None:
    environment = _required_environment()
    environment["EDGAR_MOE_REGISTRY_AUDITOR_DATABASE_URL"] = "configured"

    errors = _MODULE.validation_errors(environment)

    assert errors == [
        "Auditor configuration is incomplete; missing optional auditor secret(s): "
        "EDGAR_MOE_R2_AUDITOR_ACCESS_KEY_ID, EDGAR_MOE_R2_AUDITOR_SECRET_ACCESS_KEY"
    ]


def test_complete_auditor_configuration_passes() -> None:
    environment = _required_environment()
    environment.update({name: "configured" for name in _MODULE.AUDITOR_ENVIRONMENT})

    assert _MODULE.validation_errors(environment) == []
