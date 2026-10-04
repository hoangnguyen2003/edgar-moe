"""Checks only a disposable, already restored synthetic registry."""

import os

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from edgar_moe.forward.reader_role import READER_TABLES


def test_scoped_archive_restores_registry_without_unrelated_relations():
    url = os.environ.get("EDGAR_MOE_TEST_REGISTRY_DUMP_URL", "")
    if not url:
        pytest.skip("disposable restored registry is not configured")
    settings = conninfo_to_dict(url)
    assert settings.get("dbname", "").endswith("_restored")
    host = settings.get("host", "")
    assert host in {"127.0.0.1", "localhost", "::1"} or host.startswith("/private/tmp/")
    with psycopg.connect(url) as connection:
        tables = connection.execute(
            """SELECT n.nspname,c.relname FROM pg_class c
               JOIN pg_namespace n ON n.oid=c.relnamespace
               WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema'
               AND c.relkind IN ('r','p','v','m','f')"""
        ).fetchall()
        assert set(tables) == {("public", table) for table in (*READER_TABLES, "alembic_version")}
        assert connection.execute(
            "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal"
        ).fetchone() == (16,)
        assert connection.execute(
            "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public'"
        ).fetchone() == (2,)
        # Failure is safe even if regression permits TRUNCATE: the transaction
        # and any cascaded changes are rolled back by this exception context.
        with pytest.raises(psycopg.errors.RestrictViolation), connection.transaction():
            connection.execute("TRUNCATE public.forward_runs CASCADE")
        with pytest.raises(psycopg.errors.RestrictViolation), connection.transaction():
            connection.execute("DELETE FROM public.forward_runs")
