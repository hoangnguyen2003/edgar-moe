"""Export only the reviewed public registry, never unrelated relation data.

Table-filtered pg_dump omits function dependencies. A separate, code-reviewed
SQL file supplies the two pinned append-only functions before pg_restore runs.
Source trigger/function drift is rejected, not silently repaired. Raw dumps,
SQL and driver diagnostics belong in private temporary storage, not artifacts.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import psycopg
from sqlalchemy.engine import URL, make_url

from edgar_moe.forward.immutability import append_only_statements
from edgar_moe.forward.reader_role import READER_TABLES

DUMP_TABLES = (*READER_TABLES, "alembic_version")
FUNCTION_NAMES = ("forward_reject_mutation", "forward_protect_run")
REVIEWED_REVISION = "20260921_0002"


class RegistryDumpError(RuntimeError):
    """Fixed, credential-free refusal code."""


def direct_export_url(url: URL) -> URL:
    """Use the documented Neon direct alias for session/snapshot-based export.

    Only the pooler suffix changes; database, role, password, port and TLS options
    remain intact. Never change arbitrary provider hostnames or stored secrets.
    """
    host = url.host or ""
    first, separator, rest = host.partition(".")
    if host.endswith(".neon.tech") and first.startswith("ep-") and first.endswith("-pooler"):
        return url.set(host=first.removesuffix("-pooler") + separator + rest)
    return url


def inspect_source(connection: psycopg.Connection[Any]) -> None:
    """Refuse unsupported schema/dependencies in a bounded read-only snapshot."""
    connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    connection.execute("SET LOCAL statement_timeout = '10s'")
    connection.execute("SET LOCAL lock_timeout = '1s'")
    tables = connection.execute(
        """SELECT c.relname FROM pg_catalog.pg_class c
           JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = 'public' AND c.relname = ANY(%s)
           AND c.relkind = 'r' AND NOT c.relispartition""",
        (list(DUMP_TABLES),),
    ).fetchall()
    if {row[0] for row in tables} != set(DUMP_TABLES):
        raise RegistryDumpError("required_registry_tables_missing_or_unsupported")
    revisions = connection.execute("SELECT version_num FROM public.alembic_version").fetchall()
    if revisions != [(REVIEWED_REVISION,)]:
        raise RegistryDumpError("unsupported_registry_revision")
    functions = connection.execute(
        """SELECT p.proname, p.prosrc, p.prosecdef, p.proconfig, l.lanname,
                  p.prorettype = 'pg_catalog.trigger'::regtype, p.prokind
           FROM pg_catalog.pg_proc p
           JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
           JOIN pg_catalog.pg_language l ON l.oid = p.prolang
           WHERE n.nspname = 'public' AND p.proname = ANY(%s) AND p.pronargs = 0""",
        (list(FUNCTION_NAMES),),
    ).fetchall()
    expected_bodies = {
        name: statement.split("$$")[1].strip()
        for name, statement in zip(
            FUNCTION_NAMES, append_only_statements("postgresql")[:2], strict=True
        )
    }
    if len(functions) != 2 or any(
        body.strip() != expected_bodies[name]
        or security_definer
        or settings is not None
        or language != "plpgsql"
        or not returns_trigger
        or kind != "f"
        for name, body, security_definer, settings, language, returns_trigger, kind in functions
    ):
        raise RegistryDumpError("registry_function_drift")
    triggers = connection.execute(
        """SELECT c.relname, t.tgname, p.proname, pn.nspname, t.tgtype,
                  t.tgenabled, t.tgqual IS NULL, t.tgnargs
           FROM pg_catalog.pg_trigger t
           JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid
           JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
           JOIN pg_catalog.pg_proc p ON p.oid = t.tgfoid
           JOIN pg_catalog.pg_namespace pn ON pn.oid = p.pronamespace
           WHERE n.nspname = 'public' AND c.relname = ANY(%s) AND NOT t.tgisinternal""",
        (list(DUMP_TABLES),),
    ).fetchall()
    expected_triggers = {
        (
            table,
            f"{table}_append_only",
            "forward_protect_run" if table == "forward_runs" else "forward_reject_mutation",
            "public",
            27,
            "O",
            True,
            0,
        )
        for table in READER_TABLES
    } | {
        (table, f"{table}_no_truncate", "forward_reject_mutation", "public", 34, "O", True, 0)
        for table in READER_TABLES
    }
    if set(triggers) != expected_triggers:
        raise RegistryDumpError("registry_trigger_drift")
    external_fk = connection.execute(
        """SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint fk
           JOIN pg_catalog.pg_class c ON c.oid = fk.conrelid
           JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
           JOIN pg_catalog.pg_class target ON target.oid = fk.confrelid
           JOIN pg_catalog.pg_namespace tn ON tn.oid = target.relnamespace
           WHERE fk.contype = 'f' AND n.nspname = 'public' AND c.relname = ANY(%s)
           AND NOT (tn.nspname = 'public' AND target.relname = ANY(%s)))""",
        (list(DUMP_TABLES), list(DUMP_TABLES)),
    ).fetchone()
    if external_fk != (False,):
        raise RegistryDumpError("registry_external_foreign_key")
    unsupported_column = connection.execute(
        """SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_attribute a
           JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
           JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
           JOIN pg_catalog.pg_type typ ON typ.oid = a.atttypid
           JOIN pg_catalog.pg_namespace tn ON tn.oid = typ.typnamespace
           WHERE n.nspname = 'public' AND c.relname = ANY(%s)
           AND a.attnum > 0 AND NOT a.attisdropped
           AND (tn.nspname <> 'pg_catalog' OR a.attidentity <> '' OR a.attgenerated <> ''
                OR a.atthasdef))""",
        (list(DUMP_TABLES),),
    ).fetchone()
    if unsupported_column != (False,):
        raise RegistryDumpError("registry_unsupported_column_dependency")


def dump_command(output: Path, snapshot: str | None = None) -> list[str]:
    """Exact table patterns: no wildcard, URL argument, whole-database fallback."""
    return [
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--strict-names",
        "--no-comments",
        "--no-security-labels",
        "--lock-wait-timeout=10000",
        *(f"--table=public.{table}" for table in DUMP_TABLES),
        *([f"--snapshot={snapshot}"] if snapshot else []),
        f"--file={output}",
    ]


def export_dump(database_url: str, output: Path, functions_output: Path) -> dict[str, object]:
    if not database_url:
        raise RegistryDumpError("source_database_not_configured")
    if output.resolve() == functions_output.resolve():
        raise RegistryDumpError("output_paths_conflict")
    for path in (output, functions_output):
        if path.exists() or path.is_symlink():
            raise RegistryDumpError("output_already_exists")
        if not path.parent.is_dir():
            raise RegistryDumpError("output_parent_missing")
        if path.parent.stat().st_mode & 0o077:
            raise RegistryDumpError("output_directory_not_private")
    url = direct_export_url(make_url(database_url))
    if url.get_backend_name() != "postgresql" or not url.host or not url.database:
        raise RegistryDumpError("postgresql_source_required")
    # Use driver-compatible URL for the catalog check, but never subprocess argv.
    dsn = url.set(drivername="postgresql").render_as_string(hide_password=False)
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    environment.update(
        {
            "PGHOST": url.host,
            "PGPORT": str(url.port or 5432),
            "PGDATABASE": url.database,
            "PGUSER": url.username or "",
            "PGPASSWORD": url.password or "",
            "PGCONNECT_TIMEOUT": "10",
            "PGOPTIONS": "-c default_transaction_read_only=on -c statement_timeout=600000",
        }
    )
    for key, value in url.query.items():
        names = {
            "sslmode": "PGSSLMODE",
            "sslrootcert": "PGSSLROOTCERT",
            "sslcert": "PGSSLCERT",
            "sslkey": "PGSSLKEY",
            "channel_binding": "PGCHANNELBINDING",
        }
        if key not in names or not isinstance(value, str):
            raise RegistryDumpError("unsupported_source_connection_option")
        environment[names[key]] = value
    mask = os.umask(0o077)
    try:
        with psycopg.connect(dsn, connect_timeout=10) as connection:
            inspect_source(connection)
            snapshot = connection.execute("SELECT pg_export_snapshot()").fetchone()
            if snapshot is None or not isinstance(snapshot[0], str):
                raise RegistryDumpError("source_snapshot_unavailable")
            result = subprocess.run(
                dump_command(output, snapshot[0]),
                env=environment,
                capture_output=True,
                timeout=600,
                check=False,
            )
            if result.returncode:
                detail = str(result.stderr or "").lower()
                if "server version mismatch" in detail:
                    raise RegistryDumpError("registry_pg_dump_version_mismatch")
                if "unsupported startup parameter" in detail:
                    raise RegistryDumpError("registry_pg_dump_startup_options_rejected")
                raise RegistryDumpError("registry_pg_dump_failed")
        # Fixed reviewed definitions, not arbitrary function bodies from the provider.
        with functions_output.open("x", encoding="utf-8") as handle:
            handle.write("SET search_path = public, pg_catalog;\n")
            handle.write(";\n".join(append_only_statements("postgresql")[:2]) + ";\n")
    finally:
        os.umask(mask)
    return {
        "status": "passed",
        "scope": "public_registry_only",
        "tables": list(DUMP_TABLES),
        "function_definitions": "reviewed_migration",
        "source_mutated": False,
        "shared_read_only_snapshot": True,
        "raw_dump_retained_privately": True,
        "limitations": [
            "not_a_whole_database_backup",
            "source_schema_must_remain_frozen_during_export",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--functions-output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = export_dump(
            os.environ.get("SOURCE_DATABASE_URL", ""), args.output, args.functions_output
        )
    except RegistryDumpError as error:
        report = {"status": "failed", "error_code": str(error)}
    except (OSError, ValueError, psycopg.Error, subprocess.SubprocessError):
        report = {"status": "failed", "error_code": "registry_export_failed"}
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
