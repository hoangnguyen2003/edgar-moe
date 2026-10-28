"""CI-only owner setup/cleanup for a disposable local PostgreSQL 18 container.

The minimal reader receives only its SELECT-role password through private stdin.
The Docker ID is runner-provided, never an HTTP request or cloud target.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
HOST = "ep-invented-ci-fixture.c-4.ap-southeast-1.aws.neon.tech"
DATABASE = "study290_ci_linux_tls_fixture"
READER = "study290_reader_ci_linux_tls_fixture"


def check_scope(bundle, output, container):
    if (
        sys.platform != "linux"
        or os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_OS") != "Linux"
        or not re.fullmatch(r"[a-f0-9]{12,64}", container)
        or bundle.resolve() != bundle
        or output.resolve() != output
        or not bundle.is_relative_to(ROOT / "data/artifacts")
        or not output.is_relative_to(ROOT / "data/artifacts")
        or output.exists()
    ):
        raise ValueError("fresh_isolated_linux_ci_fixture_required")


def capture(connection, contract):
    # pg_get_* and regclass text depend on search_path. Baseline and postflight
    # must use the exact reader controls, not the owner's default session.
    from snapshot_kernel import CONTROL_SQL

    connection.execute("BEGIN")
    try:
        for command in CONTROL_SQL:
            connection.execute(command)
        return (
            connection.execute(contract["schema"]).fetchone()[0],
            connection.execute(contract["fixture"]).fetchall(),
        )
    finally:
        connection.execute("ROLLBACK")


def run(bundle, output, container):
    check_scope(bundle, output, container)
    sys.path.insert(0, str(bundle))
    from psycopg import connect, sql
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateIndex, CreateTable

    from edgar_moe.forward.immutability import append_only_statements
    from edgar_moe.forward.models import Base

    os.umask(0o077)
    output.mkdir(mode=0o700, parents=True)
    temporary = Path(tempfile.mkdtemp(prefix="study-ci-tls-"))
    report = {
        "scope": "isolated_linux_ci_loopback_tls",
        "status": "failed",
        "provider_requests": 0,
        "hosted_days": 0,
        "target_is_a_neon_service": False,
        "empty_schema_fixture": True,
        "service_lifetime_managed_by_actions": True,
    }
    created_database = created_reader = False
    admin = None

    def command(args):
        result = subprocess.run(args, capture_output=True, timeout=20)
        if result.returncode:
            raise ValueError("ci_setup_command_failed")
        return result.stdout

    def owner(database="postgres"):
        return connect(
            host="127.0.0.1",
            hostaddr="127.0.0.1",
            port=5432,
            dbname=database,
            user="postgres",
            password="local-ci-only-password",
            sslmode="disable",
            connect_timeout=3,
            application_name="study_ci_fixture_owner",
            autocommit=True,
        )

    try:
        image = json.loads(command(["docker", "inspect", container]).decode())[0]
        report["container_image_id"] = image["Image"]
        admin = owner()
        assert 180000 <= int(admin.execute("SHOW server_version_num").fetchone()[0]) < 190000
        assert not admin.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname NOT IN ('postgres','template0','template1'))"
        ).fetchone()[0]
        # New per-run CA and hostname-bound server certificate; private keys are
        # never uploaded. An unrelated second CA tests trust-chain rejection.
        for name in ["ca", "other-ca"]:
            command(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-days",
                    "1",
                    "-subj",
                    "/CN=isolated-ci-" + name,
                    "-keyout",
                    str(temporary / (name + ".key")),
                    "-out",
                    str(temporary / (name + ".crt")),
                ]
            )
        command(
            [
                "openssl",
                "req",
                "-new",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-subj",
                "/CN=" + HOST,
                "-keyout",
                str(temporary / "server.key"),
                "-out",
                str(temporary / "server.csr"),
            ]
        )
        (temporary / "server.ext").write_text("subjectAltName=DNS:" + HOST + "\n")
        command(
            [
                "openssl",
                "x509",
                "-req",
                "-in",
                str(temporary / "server.csr"),
                "-CA",
                str(temporary / "ca.crt"),
                "-CAkey",
                str(temporary / "ca.key"),
                "-CAcreateserial",
                "-days",
                "1",
                "-extfile",
                str(temporary / "server.ext"),
                "-out",
                str(temporary / "server.crt"),
            ]
        )
        command(["docker", "exec", "-u", "root", container, "mkdir", "-p", "/tmp/study-ci-tls"])
        for name in ["server.crt", "server.key"]:
            command(
                ["docker", "cp", str(temporary / name), container + ":/tmp/study-ci-tls/" + name]
            )
        command(
            [
                "docker",
                "exec",
                "-u",
                "root",
                container,
                "chown",
                "-R",
                "postgres:postgres",
                "/tmp/study-ci-tls",
            ]
        )
        command(
            [
                "docker",
                "exec",
                "-u",
                "root",
                container,
                "chmod",
                "0600",
                "/tmp/study-ci-tls/server.key",
            ]
        )
        admin.execute("ALTER SYSTEM SET ssl_cert_file='/tmp/study-ci-tls/server.crt'")
        admin.execute("ALTER SYSTEM SET ssl_key_file='/tmp/study-ci-tls/server.key'")
        admin.execute("ALTER SYSTEM SET ssl='on'")
        admin.execute("SELECT pg_reload_conf()")
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(DATABASE)))
        created_database = True
        with owner(DATABASE) as writer:
            for table in Base.metadata.sorted_tables:
                writer.execute(str(CreateTable(table).compile(dialect=postgresql.dialect())))
                for index in sorted(table.indexes, key=lambda value: value.name):
                    writer.execute(str(CreateIndex(index).compile(dialect=postgresql.dialect())))
            writer.execute(
                "CREATE TABLE public.alembic_version(version_num varchar(32) PRIMARY KEY)"
            )
            writer.execute("INSERT INTO public.alembic_version VALUES('20260921_0002')")
            for statement in append_only_statements("postgresql"):
                writer.execute(statement)
        import secrets

        password = secrets.token_urlsafe(32)
        admin.execute("SET password_encryption='scram-sha-256'")
        admin.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 2 PASSWORD {}"
            ).format(sql.Identifier(READER), sql.Literal(password))
        )
        created_reader = True
        admin.execute(
            sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(DATABASE))
        )
        admin.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(DATABASE), sql.Identifier(READER)
            )
        )
        admin.execute(
            sql.SQL("ALTER ROLE {} SET default_transaction_read_only='on'").format(
                sql.Identifier(READER)
            )
        )
        with owner(DATABASE) as writer:
            writer.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
            writer.execute(
                sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(READER))
            )
            writer.execute(
                sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(
                    sql.Identifier(READER)
                )
            )
            contract = json.loads((bundle / "contract.json").read_text())
            schema, rows = capture(writer, contract)
        # Wait only for local server configuration reload, before the measured
        # reader. This is not a reader retry or a hosted resumption sample.
        for attempt in range(20):
            try:
                with connect(
                    host=HOST,
                    hostaddr="127.0.0.1",
                    port=5432,
                    dbname=DATABASE,
                    user=READER,
                    password=password,
                    sslmode="verify-full",
                    sslrootcert=str(temporary / "ca.crt"),
                    channel_binding="require",
                    connect_timeout=1,
                ):
                    break
            except Exception:
                if attempt == 19:
                    raise ValueError("local_tls_setup_failed") from None
                time.sleep(0.1)
        report["tls_setup_connection_attempts"] = attempt + 1
        import snapshot_kernel

        checked = snapshot_kernel.SqlContract(**contract)
        target = {
            "scope": "separate-study290-snapshot-v1",
            "kind": "neon-hosted",
            "host": HOST,
            "port": 5432,
            "database": DATABASE,
            "role": READER,
            "profile_sha256": hashlib.sha256(
                b"empty-linux-ci-abi-fixture-not-representative"
            ).hexdigest(),
            "schema_sha256": schema,
            "fixture": rows,
            "sql_contract_sha256": checked.sha256,
            "sample_now": "2026-10-27T00:00:00+00:00",
        }
        config = {
            "scope": "isolated_linux_ci_loopback_tls",
            "hostaddr": "127.0.0.1",
            "port": 5432,
            "target": target,
            "reader_password": password,
            "ca_file": str(temporary / "ca.crt"),
            "other_ca_file": str(temporary / "other-ca.crt"),
        }
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("PG")
            and not any(
                fragment in k.upper()
                for fragment in [
                    "SECRET",
                    "TOKEN",
                    "API_KEY",
                    "DATABASE_URL",
                    "POSTGRES_URL",
                    "NEON",
                    "ALPACA",
                    "AWS_",
                    "CLOUDFLARE",
                    "FRED",
                    "VERCEL",
                    "EDGAR_MOE_",
                ]
            )
        }
        result = subprocess.run(
            [sys.executable, "-I", str(Path(__file__).with_name("probe.py")), str(bundle)],
            input=json.dumps(config),
            text=True,
            capture_output=True,
            env=env,
            timeout=30,
        )
        probe = json.loads(result.stdout)
        (output / "reader-result.json").write_text(
            json.dumps(probe, indent=2, sort_keys=True) + "\n"
        )
        assert result.returncode == 0 and probe["status"] == "passed"
        assert len(probe["reads"]) == len(probe["refusals"]) == 4
        with owner(DATABASE) as writer:
            assert capture(writer, contract) == (schema, rows)
            assert (
                writer.execute(
                    "SELECT count(*) FROM pg_stat_activity WHERE usename=%s", (READER,)
                ).fetchone()[0]
                == 0
            )
        report.update(
            status="passed",
            full_schema_and_row_digests_unchanged=True,
            reader_sessions_remaining=0,
            minimal_reader_owner_credential_supplied=False,
            actual_neon_tls_verified=False,
        )
    except Exception as error:
        report["error_class"] = type(error).__name__
        raise
    finally:
        try:
            if admin is not None:
                if created_database:
                    admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(DATABASE)))
                    report["new_database_dropped"] = True
                if created_reader:
                    admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(READER)))
                    report["new_reader_dropped"] = True
                admin.close()
        finally:
            shutil.rmtree(temporary)
            report["temporary_host_key_directory_removed"] = not temporary.exists()
            (output / "fixture-result.json").write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n"
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--container", required=True)
    args = parser.parse_args()
    run(args.bundle, args.output, args.container)
