"""Validate the provider-neutral deployment contract in ``vercel.json``."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

EXPECTED_BUILD_COMMAND = (
    "npm --prefix apps/web ci && npm --prefix apps/web run build && "
    "npm run build:public && "
    "python3 scripts/verify_public_snapshot_lock.py && "
    "test -f public/robots.txt && test -f public/.well-known/security.txt && "
    "test -f public/data-provenance.json"
)
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


def validate_deployment_contract(path: Path = Path("vercel.json")) -> list[str]:
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
                ("data/demo/snapshot.json", "src/edgar_moe/**"),
                "functions.api/**/*.py.includeFiles",
            )
            _require_path_tokens(
                errors,
                api_function.get("excludeFiles"),
                (".github/**", "apps/**", "config/**", "docs/**", "scripts/**", "tests/**"),
                "functions.api/**/*.py.excludeFiles",
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
    return errors


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
