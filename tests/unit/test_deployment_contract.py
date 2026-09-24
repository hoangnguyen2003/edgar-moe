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
    assert len(_VALIDATOR_MODULE.EXPECTED_BUILD_COMMAND) <= 256


def test_deployment_contract_rejects_wrong_build_command(tmp_path: Path) -> None:
    payload = json.loads(Path("vercel.json").read_text(encoding="utf-8"))
    payload["buildCommand"] = "true"
    path = tmp_path / "vercel.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    errors = validate_deployment_contract(path)

    assert "buildCommand must run deployment and public-bundle validation" in errors


def test_deployment_contract_rejects_vercel_schema_length_violation(tmp_path: Path) -> None:
    payload = json.loads(Path("vercel.json").read_text(encoding="utf-8"))
    payload["buildCommand"] = "x" * 257
    path = tmp_path / "vercel.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    errors = validate_deployment_contract(path)

    assert "buildCommand exceeds Vercel's 256-character schema limit" in errors


def test_deployment_contract_rejects_private_runtime_names(tmp_path: Path) -> None:
    payload = json.loads(Path("vercel.json").read_text(encoding="utf-8"))
    payload["description"] = "EDGAR_MOE_REGISTRY_READ_DATABASE_URL"
    path = tmp_path / "vercel.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    errors = validate_deployment_contract(path)

    assert any("private runtime name" in error for error in errors)


def test_deployment_contract_requires_serving_only_vercel_source_boundary(
    tmp_path: Path,
) -> None:
    ignore_path = tmp_path / ".vercelignore"
    ignore_path.write_text("uv.lock\nscripts/*\n", encoding="utf-8")

    errors = validate_deployment_contract(Path("vercel.json"), vercelignore_path=ignore_path)

    assert any("!scripts/verify_public_snapshot_lock.py" in error for error in errors)
    assert any("!scripts/validate_public_bundle.py" in error for error in errors)
    assert any("config/*" in error for error in errors)
    assert any("!config/public_snapshot.lock.json" in error for error in errors)


def test_deployment_contract_excludes_private_data_from_the_function() -> None:
    payload = json.loads(Path("vercel.json").read_text(encoding="utf-8"))
    exclude_files = payload["functions"]["api/**/*.py"]["excludeFiles"]

    for path in (
        "data/**",
        "mlruns/**",
        "**/*.{duckdb,duckdb.wal}",
        "ops/**",
        "tools/**",
        "migrations/**",
        ".env*",
        ".coverage",
    ):
        assert path in exclude_files


def test_deployment_contract_includes_the_runtime_snapshot_lock() -> None:
    payload = json.loads(Path("vercel.json").read_text(encoding="utf-8"))
    include_files = payload["functions"]["api/**/*.py"]["includeFiles"]

    assert "config/public_snapshot.lock.json" in include_files
    assert "data/demo/snapshot.json" in include_files


def test_deployment_contract_rejects_overlong_function_glob(tmp_path: Path) -> None:
    payload = json.loads(Path("vercel.json").read_text(encoding="utf-8"))
    payload["functions"]["api/**/*.py"]["excludeFiles"] = "x" * 257
    path = tmp_path / "vercel.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    errors = validate_deployment_contract(path)

    assert any("excludeFiles must be at most 256 characters" in error for error in errors)
