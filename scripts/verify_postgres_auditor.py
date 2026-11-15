"""Inspect the evidence auditor's effective grants without any mutation probes.

Only bounded catalog queries and LIMIT 0 reads run in a read-only transaction.
Output uses fixed codes and allowlisted table names, never driver error text,
provider identities, object names discovered in catalogs, or credential values.
"""

from __future__ import annotations

import argparse
import json
import os

import psycopg

from edgar_moe.forward.auditor_role import (
    AUDITOR_TABLES as AUDITOR_TABLES,
)
from edgar_moe.forward.auditor_role import (
    PROFILES,
    AuditorPermissionError,
)
from edgar_moe.forward.auditor_role import (
    inspect_permissions as inspect_permissions,
)
from edgar_moe.forward.reader_role import READER_TABLES as READER_TABLES


def audit_auditor_role(database_url: str, *, profile: str = "evidence") -> dict[str, object]:
    if profile not in PROFILES:
        raise AuditorPermissionError("invalid_permission_profile")
    if not database_url.strip():
        raise AuditorPermissionError("database_not_configured")
    with psycopg.connect(database_url, connect_timeout=10) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        # Transaction-local settings work with transaction poolers and cannot
        # leak into the next borrower. Avoid unsupported startup options.
        connection.execute("SET LOCAL statement_timeout = '10s'")
        connection.execute("SET LOCAL lock_timeout = '1s'")
        connection.execute("SET LOCAL search_path = pg_catalog")
        return inspect_permissions(connection, profile=profile)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=PROFILES, default="evidence")
    args = parser.parse_args(argv)
    try:
        report = audit_auditor_role(
            os.environ.get("AUDITOR_DATABASE_URL", ""), profile=args.profile
        )
    except AuditorPermissionError as error:
        report = {"schema_version": 1, "status": "failed", "error_code": str(error)}
    except (psycopg.Error, ValueError):
        # Never print exception messages: drivers can embed URLs and passwords.
        report = {
            "schema_version": 1,
            "status": "incomplete",
            "error_code": "database_inspection_failed",
        }
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
