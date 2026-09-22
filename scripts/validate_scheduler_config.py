"""Validate the committed, non-secret Cloudflare scheduler configuration."""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

EXPECTED_CRON = "17 7 * * 2-6"
EXPECTED_MAIN = "cloudflare-forward-scheduler.mjs"
EXPECTED_NAME = "edgar-moe-forward-scheduler"
EXPECTED_VARS = {
    "GITHUB_REPOSITORY": "hoangnguyen2003/edgar-moe",
    "GITHUB_WORKFLOW": "forward-production.yml",
    "GITHUB_REF": "main",
    "GITHUB_DEVICE": "cpu",
}


def validate_scheduler_config(path: Path) -> list[str]:
    """Return safety violations without reading or printing secret values."""
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [f"configuration file is missing: {path}"]
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return [f"configuration is not valid TOML ({type(exc).__name__})"]

    if not isinstance(document, dict):
        return ["configuration root must be a TOML table"]
    errors: list[str] = []
    errors.extend(_validate_identity(document))
    errors.extend(_validate_triggers(document))
    errors.extend(_validate_vars(document))
    errors.extend(_validate_no_secret_sections(document))
    return errors


def _validate_identity(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if document.get("name") != EXPECTED_NAME:
        errors.append(f"name must be {EXPECTED_NAME!r}")
    if document.get("main") != EXPECTED_MAIN:
        errors.append(f"main must be {EXPECTED_MAIN!r}")
    compatibility_date = document.get("compatibility_date")
    if not isinstance(compatibility_date, str) or not re.fullmatch(
        r"20\d{2}-\d{2}-\d{2}", compatibility_date
    ):
        errors.append("compatibility_date must be an ISO date")
    return errors


def _validate_triggers(document: dict[str, Any]) -> list[str]:
    triggers = document.get("triggers")
    if not isinstance(triggers, dict) or triggers.get("crons") != [EXPECTED_CRON]:
        return [f"triggers.crons must contain only {EXPECTED_CRON!r}"]
    return []


def _validate_vars(document: dict[str, Any]) -> list[str]:
    variables = document.get("vars")
    if not isinstance(variables, dict):
        return ["vars must define the non-secret GitHub dispatch configuration"]
    if variables == EXPECTED_VARS:
        return []

    errors: list[str] = []
    missing = sorted(set(EXPECTED_VARS) - set(variables))
    unexpected = sorted(set(variables) - set(EXPECTED_VARS))
    wrong = sorted(
        key for key in set(EXPECTED_VARS) & set(variables) if variables[key] != EXPECTED_VARS[key]
    )
    if missing:
        errors.append(f"vars missing required key(s): {', '.join(missing)}")
    if unexpected:
        errors.append(f"vars contain unexpected key(s): {', '.join(unexpected)}")
    if wrong:
        errors.append(f"vars contain unexpected value(s): {', '.join(wrong)}")
    return errors


def _validate_no_secret_sections(document: dict[str, Any]) -> list[str]:
    """Keep secrets in Cloudflare's secret store, never in the config file."""
    forbidden = {"GITHUB_TOKEN", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"}
    variables = document.get("vars")
    if isinstance(variables, dict):
        leaked = sorted(forbidden & set(variables))
        if leaked:
            return [f"secret(s) must not appear in vars: {', '.join(leaked)}"]
    for key in ("secrets", "secret", "kv_namespaces", "r2_buckets", "d1_databases"):
        if key in document:
            return [f"configuration must not declare {key}; keep deployment credentials external"]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "config",
        nargs="?",
        type=Path,
        default=Path("ops/scheduler/wrangler.toml.example"),
        help="scheduler TOML file to validate",
    )
    args = parser.parse_args()
    errors = validate_scheduler_config(args.config)
    if errors:
        print("scheduler configuration contract failed", file=sys.stderr)
        print(f"{len(errors)} validation error(s) detected", file=sys.stderr)
        return 1
    print("scheduler configuration contract passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
