"""Verify the effective privileges of the hosted API database role.

This is an operator check, not a migration. It performs harmless read probes and
attempts writes/DDL inside rolled-back transactions. It never prints the supplied
connection string and never commits a probe mutation.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import psycopg
from psycopg import Connection, sql
from psycopg.errors import InsufficientPrivilege

from edgar_moe.forward.reader_role import FORBIDDEN_TABLE_PRIVILEGES, READER_TABLES


class ReaderRoleAuditError(RuntimeError):
    """Raised when the effective database role violates the reader contract."""


def _table_ref(table_name: str) -> sql.Composed:
    return sql.SQL("{}.{}").format(sql.Identifier("public"), sql.Identifier(table_name))


def _has_table_privilege(
    connection: Connection[Any], table_name: str, privilege: str
) -> bool:
    row = connection.execute(
        "SELECT has_table_privilege(current_user, %s, %s)",
        (f"public.{table_name}", privilege),
    ).fetchone()
    return bool(row and row[0])


def _assert_denied(connection: Connection[Any], statement: sql.Composed, operation: str) -> None:
    try:
        with connection.transaction():
            connection.execute(statement)
    except InsufficientPrivilege:
        return
    except psycopg.Error as error:
        raise ReaderRoleAuditError(
            f"reader probe for {operation} failed for an unexpected database reason: "
            f"{type(error).__name__}"
        ) from error
    raise ReaderRoleAuditError(f"reader role unexpectedly permitted {operation}")


def audit_reader_role(database_url: str) -> dict[str, object]:
    """Return a redacted report after checking the effective reader privileges."""
    if not database_url.strip():
        raise ValueError("EDGAR_MOE_REGISTRY_READ_DATABASE_URL is required")

    with psycopg.connect(database_url) as connection:
        identity = connection.execute(
            """
            SELECT current_user, current_database(),
                   has_database_privilege(current_user, current_database(), 'CONNECT'),
                   has_schema_privilege(current_user, 'public', 'USAGE'),
                   has_schema_privilege(current_user, 'public', 'CREATE'),
                   r.rolsuper, r.rolcreaterole, r.rolcreatedb, r.rolinherit,
                   r.rolreplication, r.rolbypassrls
            FROM pg_roles AS r
            WHERE r.rolname = current_user
            """
        ).fetchone()
        if identity is None or not identity[2] or not identity[3]:
            raise ReaderRoleAuditError("reader role lacks database CONNECT or schema USAGE")
        if identity[4]:
            raise ReaderRoleAuditError("reader role unexpectedly has CREATE on public schema")
        if any(identity[index] for index in (5, 6, 7, 8, 9, 10)):
            raise ReaderRoleAuditError(
                "reader role has an elevated PostgreSQL role attribute; expected a "
                "non-inheriting, non-privileged login role"
            )

        for table_name in READER_TABLES:
            try:
                connection.execute(
                    sql.SQL("SELECT 1 FROM {} LIMIT 0").format(_table_ref(table_name))
                )
            except psycopg.Error as error:
                raise ReaderRoleAuditError(
                    f"reader role cannot SELECT from required table {table_name}: "
                    f"{type(error).__name__}"
                ) from error
            if not _has_table_privilege(connection, table_name, "SELECT"):
                raise ReaderRoleAuditError(f"reader role lacks SELECT on {table_name}")
            for privilege in FORBIDDEN_TABLE_PRIVILEGES:
                if _has_table_privilege(connection, table_name, privilege):
                    raise ReaderRoleAuditError(
                        f"reader role unexpectedly has {privilege} on {table_name}"
                    )

        _assert_denied(
            connection,
            sql.SQL("UPDATE {} SET run_id = run_id WHERE false").format(
                _table_ref("forward_runs")
            ),
            "UPDATE",
        )
        _assert_denied(
            connection,
            sql.SQL("DELETE FROM {} WHERE false").format(_table_ref("forward_runs")),
            "DELETE",
        )
        _assert_denied(
            connection,
            sql.SQL("CREATE TABLE {} (probe integer)").format(
                _table_ref("__edgar_moe_reader_probe")
            ),
            "DDL",
        )
        connection.rollback()

        return {
            "status": "passed",
            "role": str(identity[0]),
            "database": str(identity[1]),
            "role_attributes": {
                "superuser": bool(identity[5]),
                "createrole": bool(identity[6]),
                "createdb": bool(identity[7]),
                "inherit": bool(identity[8]),
                "replication": bool(identity[9]),
                "bypassrls": bool(identity[10]),
            },
            "select_tables": list(READER_TABLES),
            "forbidden_table_privileges": list(FORBIDDEN_TABLE_PRIVILEGES),
            "write_probes": ["UPDATE", "DELETE", "DDL"],
        }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify the SELECT-only Postgres role used by the API and auditor."
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("EDGAR_MOE_REGISTRY_READ_DATABASE_URL", ""),
        help="Reader URL; defaults to EDGAR_MOE_REGISTRY_READ_DATABASE_URL.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        report = audit_reader_role(args.database_url)
    except (ValueError, ReaderRoleAuditError, psycopg.Error) as error:
        print(f"Reader-role audit failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
