"""Verify isolated restore endpoints using bounded, read-only observations.

Backend IP addresses are connection observations, not provider endpoint IDs.
No URLs, passwords, provider identities, backend addresses, or driver errors
are included in the retained report.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

from edgar_moe.forward.endpoint import RestoreIdentityError as RestoreIdentityError
from edgar_moe.forward.endpoint import endpoint as endpoint

DATABASE_VARIABLES = {
    "source": "SOURCE_DATABASE_URL",
    "target": "TARGET_DATABASE_URL",
    "source_auditor": "SOURCE_AUDITOR_DATABASE_URL",
    "target_auditor": "TARGET_AUDITOR_DATABASE_URL",
}


def observe(database_url: str, expected: tuple[str, int, str, str], *, empty: bool) -> int:
    """Authenticate and check actual database/role, never arbitrary SQL or rows."""
    with psycopg.connect(database_url, connect_timeout=10, autocommit=True) as connection:
        connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        connection.execute("SET LOCAL statement_timeout='10s'")
        connection.execute("SET LOCAL lock_timeout='1s'")
        row = connection.execute(
            "SELECT current_database(), current_user, session_user, "
            "current_setting('server_version_num')::int"
        ).fetchone()
        if row is None or row[:3] != (expected[2], expected[3], expected[3]):
            raise RestoreIdentityError("observed_database_or_role_mismatch")
        if empty:
            count = connection.execute(
                "SELECT count(*) FROM pg_catalog.pg_class c "
                "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' "
                "AND c.relkind IN ('r','p','v','m','f')"
            ).fetchone()
            if count != (0,):
                raise RestoreIdentityError("isolated_target_not_empty")
        major = int(row[3]) // 10000
        if not 10 <= major <= 99:
            raise RestoreIdentityError("invalid_server_version")
        connection.execute("ROLLBACK")
        return major


def client_major(client: str) -> int:
    """Observe only a fixed client version, never driver diagnostics."""
    result = subprocess.run([client, "--version"], capture_output=True, text=True, timeout=5)
    match = re.fullmatch(
        rf"{re.escape(client)} \(PostgreSQL\) (\d+)\.\d+[^\r\n]*\n?", result.stdout
    )
    if result.returncode or match is None:
        raise RestoreIdentityError("invalid_client_version")
    return int(match[1])


def verify(environment: Mapping[str, str]) -> dict[str, Any]:
    """Fail closed before provider observations if configured endpoints disagree."""
    report: dict[str, Any] = {
        "schema_version": 1,
        "status": "not_ready",
        "reason": None,
        "checked_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source_target_distinct": False,
        "auditor_urls_aligned": False,
        "target_table_count": None,
        "server_majors": {},
        "client_majors": {},
    }
    if environment.get("CONFIRM_ISOLATED_TARGET") != "I_UNDERSTAND_ISOLATED_TARGET":
        report["reason"] = "explicit_confirmation_required"
        return report
    identities: dict[str, tuple[str, int, str, str]] = {}
    for label, variable in DATABASE_VARIABLES.items():
        try:
            identities[label] = endpoint(environment.get(variable, ""))
        except RestoreIdentityError:
            report["reason"] = f"{label}_invalid_database_url"
            return report
    # Require a different hostname, not merely another database/port on the
    # production endpoint. A matching backend IP cannot establish isolation.
    if identities["source"][0] == identities["target"][0]:
        report["reason"] = "source_target_endpoint_not_distinct"
        return report
    report["source_target_distinct"] = True
    for label in ("source", "target"):
        if identities[label][:3] != identities[f"{label}_auditor"][:3]:
            report["reason"] = f"{label}_auditor_endpoint_mismatch"
            return report
        if identities[label][3] == identities[f"{label}_auditor"][3]:
            report["reason"] = f"{label}_writer_auditor_role_conflict"
            return report
    report["auditor_urls_aligned"] = True
    for label, variable in DATABASE_VARIABLES.items():
        try:
            report["server_majors"][label] = observe(
                environment[variable], identities[label], empty=label == "target"
            )
        except RestoreIdentityError as error:
            report["reason"] = f"{label}_{error}"
            return report
        except Exception:
            report["reason"] = f"{label}_connection_or_observation_failed"
            return report
    try:
        report["client_majors"] = {
            client: client_major(client) for client in ("pg_dump", "pg_restore")
        }
    except Exception:
        report["reason"] = "client_version_observation_failed"
        return report
    dump_major = report["client_majors"]["pg_dump"]
    if (
        dump_major != report["client_majors"]["pg_restore"]
        or dump_major < report["server_majors"]["source"]
        or dump_major > report["server_majors"]["target"]
    ):
        report["reason"] = "incompatible_postgresql_client_or_target_version"
        return report
    report.update(status="ready", reason=None, target_table_count=0)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = verify(os.environ)
    try:
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except OSError:
        print('{"status":"not_ready","reason":"report_write_failed"}')
        return 2
    print(json.dumps({"status": report["status"], "reason": report["reason"]}))
    return 0 if report["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
