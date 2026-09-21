"""Validate the secret groups required by the scheduled forward runner.

Only variable names are reported. Secret values, including database URLs, are
never echoed to logs. The optional independent auditor is an all-or-none group:
partially configuring it would otherwise turn a typo into an ambiguous audit
failure later in the workflow.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping

REQUIRED_ENVIRONMENT = (
    "ALPACA_API_KEY",
    "ALPACA_API_SECRET",
    "FRED_API_KEY",
    "SEC_USER_AGENT",
    "EDGAR_MOE_REGISTRY_DATABASE_URL",
    "EDGAR_MOE_R2_ENDPOINT_URL",
    "EDGAR_MOE_R2_BUCKET",
    "EDGAR_MOE_R2_ACCESS_KEY_ID",
    "EDGAR_MOE_R2_SECRET_ACCESS_KEY",
)

AUDITOR_ENVIRONMENT = (
    "EDGAR_MOE_REGISTRY_AUDITOR_DATABASE_URL",
    "EDGAR_MOE_R2_AUDITOR_ACCESS_KEY_ID",
    "EDGAR_MOE_R2_AUDITOR_SECRET_ACCESS_KEY",
)


def validation_errors(environment: Mapping[str, str]) -> list[str]:
    """Return safe configuration errors without inspecting secret contents."""
    errors: list[str] = []
    missing_required = [name for name in REQUIRED_ENVIRONMENT if not environment.get(name, "")]
    if missing_required:
        errors.append("Missing required forward-runner secret(s): " + ", ".join(missing_required))

    configured_auditor = [name for name in AUDITOR_ENVIRONMENT if environment.get(name, "")]
    if configured_auditor and len(configured_auditor) != len(AUDITOR_ENVIRONMENT):
        missing_auditor = [name for name in AUDITOR_ENVIRONMENT if not environment.get(name, "")]
        errors.append(
            "Auditor configuration is incomplete; missing optional auditor secret(s): "
            + ", ".join(missing_auditor)
        )
    return errors


def main() -> int:
    errors = validation_errors(os.environ)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    auditor_state = (
        "enabled" if all(os.environ.get(name, "") for name in AUDITOR_ENVIRONMENT) else "disabled"
    )
    print(f"Forward runtime configuration validated (auditor={auditor_state}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
