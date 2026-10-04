"""Shared read-only effective-grant inspection, without mutation probes."""

from __future__ import annotations

from typing import Any

from psycopg import Connection, sql

from edgar_moe.forward.reader_role import READER_TABLES

AUDITOR_TABLES = ("forward_runs", "forward_artifacts")
PROFILES = ("evidence", "empty-restore-target", "registry-reader")
_USER_SCHEMA = "n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'"


class AuditorPermissionError(RuntimeError):
    """A fixed, credential-free contract failure code."""


def _require(connection: Connection[Any], query: str, code: str) -> None:
    row = connection.execute(query).fetchone()
    if row is None or row[0] is not True:
        raise AuditorPermissionError(code)


def inspect_permissions(
    connection: Connection[Any], *, profile: str = "evidence"
) -> dict[str, object]:
    """Inspect current-session grants; caller must start a read-only snapshot."""
    if profile not in PROFILES:
        raise AuditorPermissionError("invalid_permission_profile")
    tables = (
        AUDITOR_TABLES
        if profile == "evidence"
        else READER_TABLES
        if profile == "registry-reader"
        else ()
    )
    # A logical registry dump also contains Alembic's non-sensitive migration
    # revision table. Permit its SELECT grant, but do not require/read it.
    allowed_tables = tables + (("alembic_version",) if profile == "registry-reader" else ())
    # These names are internal constants, never SQL supplied by an operator.
    allowed_names = ", ".join(f"'{table}'" for table in allowed_tables) or "''"
    _require(
        connection,
        "SELECT current_setting('transaction_read_only') = 'on'",
        "transaction_not_read_only",
    )
    _require(
        connection,
        "SELECT current_user = session_user",
        "session_role_mismatch",
    )
    _require(
        connection,
        """SELECT rolcanlogin AND NOT (rolsuper OR rolcreatedb OR rolcreaterole
                   OR rolreplication OR rolbypassrls)
           FROM pg_catalog.pg_roles WHERE rolname = current_user""",
        "elevated_role_attributes",
    )
    _require(
        connection,
        """SELECT NOT EXISTS (
           SELECT 1 FROM pg_catalog.pg_roles
           WHERE rolname <> current_user AND pg_has_role(current_user, oid, 'MEMBER'))""",
        "role_membership_not_allowed",
    )
    _require(
        connection,
        """SELECT has_database_privilege(current_user, current_database(), 'CONNECT')
           AND NOT has_database_privilege(current_user, current_database(), 'CREATE')
           AND has_schema_privilege(current_user, 'public', 'USAGE')""",
        "database_or_schema_access_invalid",
    )
    _require(
        connection,
        f"""SELECT NOT EXISTS (SELECT 1 FROM pg_catalog.pg_namespace n
            WHERE {_USER_SCHEMA} AND has_schema_privilege(current_user, n.oid, 'CREATE'))""",
        "schema_create_allowed",
    )
    version = connection.execute("SHOW server_version_num").fetchone()
    if version is None:
        raise AuditorPermissionError("server_version_unavailable")
    # MAINTAIN was added in Postgres 17; asking older servers for it is invalid.
    forbidden = "INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER"
    if int(version[0]) >= 170000:
        forbidden += ", MAINTAIN"
    _require(
        connection,
        f"""SELECT NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE {_USER_SCHEMA} AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
            AND NOT (n.nspname = 'public' AND c.relname IN ({allowed_names}))
            AND has_any_column_privilege(current_user, c.oid, 'SELECT'))""",
        "unrelated_table_read_allowed",
    )
    _require(
        connection,
        f"""SELECT NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE {_USER_SCHEMA} AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
            AND (has_table_privilege(current_user, c.oid, '{forbidden}')
                 OR has_any_column_privilege(current_user, c.oid, 'INSERT, UPDATE, REFERENCES')
                 OR has_table_privilege(current_user, c.oid, 'SELECT WITH GRANT OPTION')
                 OR EXISTS (SELECT 1 FROM pg_catalog.pg_attribute a
                     WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
                     AND has_column_privilege(current_user, c.oid, a.attnum,
                                              'SELECT WITH GRANT OPTION'))))""",
        "table_write_or_grant_allowed",
    )
    _require(
        connection,
        f"""SELECT NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE {_USER_SCHEMA} AND c.relkind = 'S'
            AND has_sequence_privilege(current_user, c.oid, 'USAGE, UPDATE'))""",
        "sequence_mutation_allowed",
    )
    _require(
        connection,
        f"""SELECT NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_proc p
            JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
            WHERE {_USER_SCHEMA} AND p.prosecdef
            AND has_function_privilege(current_user, p.oid, 'EXECUTE'))""",
        "security_definer_execution_allowed",
    )
    if profile == "empty-restore-target":
        _require(
            connection,
            f"""SELECT NOT EXISTS (
                SELECT 1 FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                WHERE {_USER_SCHEMA} AND c.relkind IN ('r', 'p', 'v', 'm', 'f'))""",
            "restore_target_not_empty",
        )
    for table in tables:
        row = connection.execute(
            "SELECT has_table_privilege(current_user, %s, 'SELECT')",
            (f"public.{table}",),
        ).fetchone()
        if row is None or row[0] is not True:
            raise AuditorPermissionError("required_select_missing")
        connection.execute(sql.SQL("SELECT 1 FROM public.{} LIMIT 0").format(sql.Identifier(table)))
    return {
        "schema_version": 1,
        "status": "passed",
        "identity_redacted": True,
        "profile": profile,
        "select_tables": list(tables),
        "allowed_select_tables": list(allowed_tables),
        "future_table_grants_verified": False,
        "transaction": "read_only_repeatable_read",
        "mutation_probes": False,
        "scope": "current_database_non_system_schema_grants",
        "checks": [
            "role_attributes",
            "no_role_membership",
            "database_connect_without_create",
            "public_schema_usage",
            "no_schema_create",
            "no_table_or_column_writes",
            "no_select_grant_option",
            "no_sequence_mutation",
            "no_security_definer_execution",
            "no_unrelated_table_reads",
            "empty_restore_target" if profile == "empty-restore-target" else "required_table_reads",
        ],
    }
