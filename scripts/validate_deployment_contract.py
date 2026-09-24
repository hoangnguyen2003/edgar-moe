"""Validate the provider-neutral deployment contract in ``vercel.json``."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

EXPECTED_BUILD_COMMAND = (
    "npm --prefix apps/web ci && npm run build:public && "
    "python3 scripts/verify_public_snapshot_lock.py && "
    "python3 scripts/verify_research_evidence_catalog.py && "
    "python3 scripts/validate_public_bundle.py"
)
MAX_BUILD_COMMAND_LENGTH = 256
REQUIRED_HEADERS = {
    "X-Content-Type-Options",
    "X-Frame-Options",
    "Referrer-Policy",
    "Cross-Origin-Opener-Policy",
    "Cross-Origin-Resource-Policy",
    "Strict-Transport-Security",
    "Permissions-Policy",
    "Content-Security-Policy",
}
PRIVATE_RUNTIME_NAMES = (
    "ALPACA_API_KEY",
    "ALPACA_API_SECRET",
    "FRED_API_KEY",
    "SEC_USER_AGENT",
    "EDGAR_MOE_REGISTRY_DATABASE_URL",
    "EDGAR_MOE_REGISTRY_READ_DATABASE_URL",
    "EDGAR_MOE_R2_ACCESS_KEY_ID",
    "EDGAR_MOE_R2_SECRET_ACCESS_KEY",
)
REQUIRED_VERCEL_IGNORE_RULES = (
    "uv.lock",
    "scripts/*",
    "!scripts/verify_public_snapshot_lock.py",
    "!scripts/verify_research_evidence_catalog.py",
    "!scripts/validate_public_bundle.py",
    "reports/*",
    "!reports/locked_rank_ic_interval_2026-09-23.md",
    "config/*",
    "!config/public_snapshot.lock.json",
    "data/cache",
    "data/forward",
    "*.duckdb.wal",
    "ops",
    "tools",
    "migrations",
)
REQUIRED_FUNCTION_EXCLUDE_PATHS = (
    "data/**",
    "mlruns/**",
    "**/*.{duckdb,duckdb.wal}",
    "ops/**",
    "tools/**",
    "migrations/**",
    ".env*",
    ".coverage",
)
MAX_FUNCTION_GLOB_LENGTH = 256


def validate_deployment_contract(
    path: Path = Path("vercel.json"),
    *,
    vercelignore_path: Path = Path(".vercelignore"),
) -> list[str]:
    """Return redacted validation errors for the serving-tier contract."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [f"deployment contract is not readable JSON: {path}"]
    if not isinstance(payload, dict):
        return ["deployment contract must be a JSON object"]

    errors: list[str] = []
    if payload.get("outputDirectory") != "public":
        errors.append("outputDirectory must be public")
    if payload.get("buildCommand") != EXPECTED_BUILD_COMMAND:
        errors.append("buildCommand must run deployment and public-bundle validation")
    if (
        isinstance(payload.get("buildCommand"), str)
        and len(payload["buildCommand"]) > MAX_BUILD_COMMAND_LENGTH
    ):
        errors.append("buildCommand exceeds Vercel's 256-character schema limit")
    if payload.get("regions") != ["sin1"]:
        errors.append("regions must pin the documented sin1 serving region")

    functions = payload.get("functions")
    if not isinstance(functions, dict):
        errors.append("functions must define the api/**/*.py deployment boundary")
    else:
        api_function = functions.get("api/**/*.py")
        if not isinstance(api_function, dict):
            errors.append("functions.api/**/*.py must be an object")
        else:
            _require_path_tokens(
                errors,
                api_function.get("includeFiles"),
                (
                    "config/public_snapshot.lock.json",
                    "data/demo/snapshot.json",
                    "src/edgar_moe/**",
                ),
                "functions.api/**/*.py.includeFiles",
            )
            _require_path_tokens(
                errors,
                api_function.get("excludeFiles"),
                (
                    ".github/**",
                    "apps/**",
                    "config/**",
                    "docs/**",
                    "scripts/**",
                    "tests/**",
                    *REQUIRED_FUNCTION_EXCLUDE_PATHS,
                ),
                "functions.api/**/*.py.excludeFiles",
            )
            exclude_files = api_function.get("excludeFiles")
            if isinstance(exclude_files, str) and len(exclude_files) > MAX_FUNCTION_GLOB_LENGTH:
                errors.append(
                    "functions.api/**/*.py.excludeFiles must be at most "
                    f"{MAX_FUNCTION_GLOB_LENGTH} characters"
                )
            if "env" in api_function:
                errors.append("functions.api/**/*.py must not define deployment env secrets")

    headers = payload.get("headers")
    observed_headers: set[str] = set()
    if isinstance(headers, list):
        for rule in headers:
            if isinstance(rule, dict) and isinstance(rule.get("headers"), list):
                observed_headers.update(
                    str(item.get("key"))
                    for item in rule["headers"]
                    if isinstance(item, dict) and item.get("key")
                )
    else:
        errors.append("headers must be a list")
    missing_headers = sorted(REQUIRED_HEADERS - observed_headers)
    if missing_headers:
        errors.append("missing required security headers: " + ", ".join(missing_headers))

    rewrites = payload.get("rewrites")
    if not isinstance(rewrites, list) or not any(
        isinstance(rule, dict)
        and rule.get("source") == "/((?!api/).*)"
        and rule.get("destination") == "/index.html"
        for rule in rewrites
    ):
        errors.append("rewrites must route non-API paths to /index.html")

    serialized = json.dumps(payload, sort_keys=True)
    for name in PRIVATE_RUNTIME_NAMES:
        if name in serialized:
            errors.append(f"private runtime name found in deployment contract: {name}")
    _validate_vercel_source_boundary(vercelignore_path, errors)
    return errors


def _validate_vercel_source_boundary(path: Path, errors: list[str]) -> None:
    """Keep research dependencies and private operator inputs out of Vercel builds."""
    try:
        rules = {
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
    except (OSError, UnicodeDecodeError):
        errors.append(f"Vercel source boundary file is not readable: {path}")
        return
    for rule in REQUIRED_VERCEL_IGNORE_RULES:
        if rule not in rules:
            errors.append(f".vercelignore is missing required source-boundary rule: {rule}")


def _require_path_tokens(
    errors: list[str], value: Any, required: tuple[str, ...], label: str
) -> None:
    if not isinstance(value, str) or any(token not in value for token in required):
        errors.append(f"{label} is missing required boundary paths")


def main() -> int:
    errors = validate_deployment_contract()
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("Deployment contract validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
