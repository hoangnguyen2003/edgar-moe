"""Check restored PostgreSQL schema read-only, retaining no raw diagnostics."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy.engine import make_url


def verify_schema(database_url: str) -> dict[str, object]:
    report: dict[str, object] = {
        "schema_version": 1,
        "status": "failed",
        "scope": "restored_schema_consistency",
        "raw_diagnostics_retained": False,
        "read_only": True,
    }
    try:
        url = make_url(database_url)
        if (
            not database_url
            or url.get_backend_name() != "postgresql"
            or any(
                key
                not in {
                    "sslmode",
                    "sslrootcert",
                    "sslcert",
                    "sslkey",
                    "channel_binding",
                    "connect_timeout",
                    "application_name",
                }
                for key in url.query
            )
        ):
            raise ValueError
    except Exception:
        return {**report, "reason": "postgresql_target_required"}
    environment = dict(os.environ)
    environment["EDGAR_MOE_REGISTRY_DATABASE_URL"] = database_url
    environment["PGOPTIONS"] = (
        "-c default_transaction_read_only=on -c statement_timeout=10000 -c lock_timeout=1000"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "check"],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {**report, "reason": "schema_check_timeout"}
    except Exception:
        return {**report, "reason": "schema_check_failed"}
    if result.returncode != 0:
        return {**report, "reason": "schema_check_failed"}
    return {**report, "status": "passed", "reason": None}


def main() -> int:
    report = verify_schema(os.environ.get("EDGAR_MOE_REGISTRY_DATABASE_URL", ""))
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
