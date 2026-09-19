from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_VALIDATOR_PATH = Path(__file__).parents[2] / "scripts" / "validate_deployment_contract.py"
_VALIDATOR_SPEC = importlib.util.spec_from_file_location(
    "validate_deployment_contract", _VALIDATOR_PATH
)
assert _VALIDATOR_SPEC is not None and _VALIDATOR_SPEC.loader is not None
_VALIDATOR_MODULE = importlib.util.module_from_spec(_VALIDATOR_SPEC)
_VALIDATOR_SPEC.loader.exec_module(_VALIDATOR_MODULE)
validate_deployment_contract = _VALIDATOR_MODULE.validate_deployment_contract


def test_checked_in_deployment_contract_is_valid() -> None:
    assert validate_deployment_contract(Path("vercel.json")) == []


def test_deployment_contract_rejects_wrong_build_command(tmp_path: Path) -> None:
    payload = json.loads(Path("vercel.json").read_text(encoding="utf-8"))
    payload["buildCommand"] = "true"
    path = tmp_path / "vercel.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    errors = validate_deployment_contract(path)

    assert "buildCommand must run deployment and public-bundle validation" in errors


def test_deployment_contract_rejects_private_runtime_names(tmp_path: Path) -> None:
    payload = json.loads(Path("vercel.json").read_text(encoding="utf-8"))
    payload["description"] = "EDGAR_MOE_REGISTRY_READ_DATABASE_URL"
    path = tmp_path / "vercel.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    errors = validate_deployment_contract(path)

    assert any("private runtime name" in error for error in errors)
