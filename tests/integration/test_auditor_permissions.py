"""Real PostgreSQL grant regressions; only an explicitly isolated test DB is allowed."""

from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from scripts import verify_postgres_auditor as audit


@pytest.fixture
def auditor_database() -> Iterator[tuple[psycopg.Connection[tuple[object, ...]], str, str]]:
    admin_url = os.environ.get("EDGAR_MOE_TEST_AUDITOR_POSTGRES_URL", "")
    if not admin_url:
        pytest.skip("dedicated PostgreSQL auditor fixture is not configured")
    settings = conninfo_to_dict(admin_url)
    assert settings.get("dbname", "").endswith("_auditor_permission_test")
    host = settings.get("host", "")
    assert host in {"127.0.0.1", "localhost", "::1"} or host.startswith("/private/tmp/")
    role = "auditor_fixture_" + uuid4().hex
    with psycopg.connect(admin_url, autocommit=True) as admin:
        assert admin.execute("SELECT to_regclass('public.forward_runs')").fetchone() == (None,)
        admin.execute("CREATE TABLE public.forward_runs (run_id text)")
        admin.execute("CREATE TABLE public.forward_artifacts (artifact_id text)")
        admin.execute("CREATE TABLE public.unrelated_fixture (id integer)")
        admin.execute("CREATE SEQUENCE public.auditor_fixture_sequence")
        admin.execute(
            "CREATE FUNCTION public.auditor_fixture_function() RETURNS integer LANGUAGE sql SECURITY DEFINER AS 'SELECT 1'"
        )
        admin.execute("REVOKE ALL ON FUNCTION public.auditor_fixture_function() FROM PUBLIC")
        admin.execute(
            psycopg.sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'synthetic-fixture-only'").format(
                psycopg.sql.Identifier(role)
            )
        )
        admin.execute(
            psycopg.sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
                psycopg.sql.Identifier(role)
            )
        )
        admin.execute(
            psycopg.sql.SQL(
                "GRANT SELECT ON public.forward_runs, public.forward_artifacts TO {}"
            ).format(psycopg.sql.Identifier(role))
        )
        dsn = make_conninfo(admin_url, user=role, password="synthetic-fixture-only")
        try:
            yield admin, role, dsn
        finally:
            admin.execute("DROP FUNCTION public.auditor_fixture_function()")
            admin.execute("DROP SEQUENCE public.auditor_fixture_sequence")
            admin.execute(
                "DROP TABLE public.forward_runs, public.forward_artifacts, public.unrelated_fixture"
            )
            admin.execute(psycopg.sql.SQL("DROP OWNED BY {}").format(psycopg.sql.Identifier(role)))
            admin.execute(psycopg.sql.SQL("DROP ROLE {}").format(psycopg.sql.Identifier(role)))


@pytest.mark.parametrize(
    ("grant", "code"),
    [
        ("", ""),
        ("GRANT INSERT (run_id) ON public.forward_runs TO {}", "table_write_or_grant_allowed"),
        ("GRANT UPDATE (run_id) ON public.forward_runs TO {}", "table_write_or_grant_allowed"),
        (
            "GRANT SELECT (run_id) ON public.forward_runs TO {} WITH GRANT OPTION",
            "table_write_or_grant_allowed",
        ),
        ("GRANT SELECT ON public.unrelated_fixture TO {}", "unrelated_table_read_allowed"),
        (
            "GRANT USAGE ON SEQUENCE public.auditor_fixture_sequence TO {}",
            "sequence_mutation_allowed",
        ),
        (
            "GRANT EXECUTE ON FUNCTION public.auditor_fixture_function() TO {}",
            "security_definer_execution_allowed",
        ),
        ("GRANT CREATE ON SCHEMA public TO {}", "schema_create_allowed"),
        ("ALTER ROLE {} CREATEDB", "elevated_role_attributes"),
        ("GRANT pg_read_all_data TO {}", "role_membership_not_allowed"),
        ("GRANT REFERENCES (run_id) ON public.forward_runs TO {}", "table_write_or_grant_allowed"),
        ("GRANT DELETE ON public.forward_runs TO {}", "table_write_or_grant_allowed"),
        ("GRANT MAINTAIN ON public.forward_runs TO {}", "table_write_or_grant_allowed"),
        ("REVOKE SELECT ON public.forward_runs FROM {}", "required_select_missing"),
    ],
)
def test_actual_postgres_grants_fail_closed_without_mutations(
    auditor_database: tuple[psycopg.Connection[tuple[object, ...]], str, str],
    grant: str,
    code: str,
) -> None:
    admin, role, dsn = auditor_database
    if "MAINTAIN" in grant:
        version = admin.execute("SHOW server_version_num").fetchone()
        assert version is not None
        if int(str(version[0])) < 170000:
            pytest.skip("MAINTAIN privilege requires PostgreSQL 17+")
    assert audit.audit_auditor_role(dsn)["status"] == "passed"
    if grant:
        admin.execute(psycopg.sql.SQL(grant).format(psycopg.sql.Identifier(role)))
        with pytest.raises(audit.AuditorPermissionError, match=f"^{code}$"):
            audit.audit_auditor_role(dsn)
    assert admin.execute("SELECT count(*) FROM public.forward_runs").fetchone() == (0,)
    assert admin.execute("SELECT count(*) FROM public.forward_artifacts").fetchone() == (0,)


def test_non_inherited_membership_is_still_rejected(
    auditor_database: tuple[psycopg.Connection[tuple[object, ...]], str, str],
) -> None:
    admin, role, dsn = auditor_database
    admin.execute(psycopg.sql.SQL("ALTER ROLE {} NOINHERIT").format(psycopg.sql.Identifier(role)))
    admin.execute(
        psycopg.sql.SQL("GRANT pg_write_all_data TO {}").format(psycopg.sql.Identifier(role))
    )
    with pytest.raises(audit.AuditorPermissionError, match="^role_membership_not_allowed$"):
        audit.audit_auditor_role(dsn)


def test_public_column_write_grant_is_still_rejected(
    auditor_database: tuple[psycopg.Connection[tuple[object, ...]], str, str],
) -> None:
    admin, _, dsn = auditor_database
    admin.execute("GRANT UPDATE (run_id) ON public.forward_runs TO PUBLIC")
    with pytest.raises(audit.AuditorPermissionError, match="^table_write_or_grant_allowed$"):
        audit.audit_auditor_role(dsn)
