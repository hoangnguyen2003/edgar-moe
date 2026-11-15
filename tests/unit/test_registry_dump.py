from subprocess import CompletedProcess
from unittest.mock import MagicMock

import pytest
from sqlalchemy.engine import make_url

from scripts import export_registry_dump as exporter


def source_rows():
    functions = [
        (name, statement.split("$$")[1].strip(), False, None, "plpgsql", True, "f")
        for name, statement in zip(
            exporter.FUNCTION_NAMES, exporter.append_only_statements("postgresql")[:2], strict=True
        )
    ]
    triggers = [
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
        for table in exporter.READER_TABLES
    ] + [
        (table, f"{table}_no_truncate", "forward_reject_mutation", "public", 34, "O", True, 0)
        for table in exporter.READER_TABLES
    ]
    return [
        [(table,) for table in exporter.DUMP_TABLES],
        [(exporter.REVIEWED_REVISION,)],
        functions,
        triggers,
    ]


def fake_connection(rows=None, external_fk=False, unsupported_column=False):
    connection = MagicMock()
    results = [MagicMock() for _ in range(10)]
    for result, data in zip(results[3:7], rows or source_rows(), strict=True):
        result.fetchall.return_value = data
    results[7].fetchone.return_value = (external_fk,)
    results[8].fetchone.return_value = (unsupported_column,)
    results[9].fetchone.return_value = ("synthetic-snapshot",)
    connection.execute.side_effect = results
    return connection


def test_dump_command_contains_only_exact_allowlisted_public_tables(tmp_path):
    command = exporter.dump_command(tmp_path / "registry.dump")
    assert command[0] == "pg_dump"
    assert [arg for arg in command if arg.startswith("--table=")] == [
        f"--table=public.{table}" for table in exporter.DUMP_TABLES
    ]
    assert "--strict-names" in command
    assert "--no-owner" in command and "--no-privileges" in command
    assert not any("://" in arg or "*" in arg for arg in command)


def test_reviewed_source_is_inspected_read_only():
    connection = fake_connection()
    exporter.inspect_source(connection)
    assert connection.execute.call_args_list[0].args == (
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
    )


@pytest.mark.parametrize(
    "change,code",
    [
        ("missing_table", "required_registry_tables_missing_or_unsupported"),
        ("old_revision", "unsupported_registry_revision"),
        ("changed_function", "registry_function_drift"),
        ("missing_function", "registry_function_drift"),
        ("security_definer", "registry_function_drift"),
        ("extra_trigger", "registry_trigger_drift"),
        ("disabled_trigger", "registry_trigger_drift"),
        ("external_fk", "registry_external_foreign_key"),
        ("column_dependency", "registry_unsupported_column_dependency"),
    ],
)
def test_source_drift_fails_closed(change, code):
    rows = source_rows()
    if change == "missing_table":
        rows[0].pop()
    elif change == "old_revision":
        rows[1] = [("old",)]
    elif change == "changed_function":
        rows[2][0] = (rows[2][0][0], "BEGIN RETURN NEW; END", *rows[2][0][2:])
    elif change == "missing_function":
        rows[2].pop()
    elif change == "security_definer":
        rows[2][0] = (*rows[2][0][:2], True, *rows[2][0][3:])
    elif change == "extra_trigger":
        rows[3].append(("alembic_version", "extra", "unrelated", "public", 27, "O", True, 0))
    elif change == "disabled_trigger":
        rows[3][0] = (*rows[3][0][:5], "D", *rows[3][0][6:])
    connection = fake_connection(rows, change == "external_fk", change == "column_dependency")
    with pytest.raises(exporter.RegistryDumpError, match=f"^{code}$"):
        exporter.inspect_source(connection)


def test_export_uses_environment_for_credentials_and_reviewed_functions(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    connection = fake_connection()
    connect = MagicMock()
    connect.return_value.__enter__.return_value = connection
    monkeypatch.setattr(exporter.psycopg, "connect", connect)
    run = MagicMock(return_value=CompletedProcess([], 0))
    monkeypatch.setattr(exporter.subprocess, "run", run)
    monkeypatch.setenv("PGSERVICE", "untrusted-inherited-service")
    url = "postgresql://fixture:synthetic-fixture-only@127.0.0.1/fixture?sslmode=disable"
    functions = tmp_path / "functions.sql"
    report = exporter.export_dump(url, tmp_path / "registry.dump", functions)
    command = run.call_args.args[0]
    assert not any("synthetic-fixture-only" in arg for arg in command)
    assert run.call_args.kwargs["env"]["PGPASSWORD"] == "synthetic-fixture-only"
    assert "PGSERVICE" not in run.call_args.kwargs["env"]
    assert "--snapshot=synthetic-snapshot" in command
    assert functions.stat().st_mode & 0o777 == 0o600
    assert functions.read_text().startswith("SET search_path = public, pg_catalog;")
    assert "SECURITY DEFINER" not in functions.read_text()
    assert "synthetic-fixture-only" not in functions.read_text()
    assert report["scope"] == "public_registry_only"


def test_failed_dump_does_not_create_function_sidecar(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    connect = MagicMock()
    connect.return_value.__enter__.return_value = fake_connection()
    monkeypatch.setattr(exporter.psycopg, "connect", connect)
    monkeypatch.setattr(exporter.subprocess, "run", MagicMock(return_value=CompletedProcess([], 1)))
    with pytest.raises(exporter.RegistryDumpError, match="^registry_pg_dump_failed$"):
        exporter.export_dump(
            "postgresql://fixture@127.0.0.1/fixture", tmp_path / "dump", tmp_path / "functions"
        )
    assert not (tmp_path / "functions").exists()


def test_outputs_are_private_and_never_overwritten(tmp_path):
    tmp_path.chmod(0o755)
    with pytest.raises(exporter.RegistryDumpError, match="^output_directory_not_private$"):
        exporter.export_dump(
            "postgresql://fixture@127.0.0.1/fixture", tmp_path / "dump", tmp_path / "functions"
        )
    tmp_path.chmod(0o700)
    existing = tmp_path / "dump"
    existing.write_text("keep")
    with pytest.raises(exporter.RegistryDumpError, match="^output_already_exists$"):
        exporter.export_dump(
            "postgresql://fixture@127.0.0.1/fixture", existing, tmp_path / "functions"
        )
    assert existing.read_text() == "keep"


def test_cli_redacts_driver_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        exporter, "export_dump", MagicMock(side_effect=ValueError("private-url-password"))
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "export",
            "--output",
            str(tmp_path / "dump"),
            "--functions-output",
            str(tmp_path / "functions"),
        ],
    )
    assert exporter.main() == 1
    assert "private-url-password" not in capsys.readouterr().out


def test_neon_export_uses_only_direct_alias_and_preserves_credentials_and_tls(
    tmp_path, monkeypatch
):
    tmp_path.chmod(0o700)
    original = make_url(
        "postgresql://fixture:synthetic%40secret@ep-fixture-pooler.example.neon.tech:5432/fixture?sslmode=verify-full&sslrootcert=/fixture/ca.pem&channel_binding=require"
    )
    direct = exporter.direct_export_url(original)
    assert direct.host == "ep-fixture.example.neon.tech"
    assert direct.set(host=original.host) == original
    connect = MagicMock()
    connect.return_value.__enter__.return_value = fake_connection()
    monkeypatch.setattr(exporter.psycopg, "connect", connect)
    run = MagicMock(return_value=CompletedProcess([], 0))
    monkeypatch.setattr(exporter.subprocess, "run", run)
    exporter.export_dump(
        original.render_as_string(hide_password=False), tmp_path / "dump", tmp_path / "functions"
    )
    assert make_url(connect.call_args.args[0]) == direct
    settings = run.call_args.kwargs["env"]
    assert settings["PGHOST"] == direct.host
    assert settings["PGPASSWORD"] == original.password
    assert settings["PGSSLROOTCERT"] == original.query["sslrootcert"]
    assert settings["PGSSLMODE"] == "verify-full"
    assert settings["PGCHANNELBINDING"] == "require"


@pytest.mark.parametrize(
    "host",
    [
        "ep-fixture-pooler.example.invalid",
        "other-pooler.example.neon.tech",
        "ep-fixture.example.neon.tech",
        "127.0.0.1",
    ],
)
def test_export_never_rewrites_arbitrary_hosts(host):
    configured = make_url(f"postgresql://fixture@{host}/fixture")
    assert exporter.direct_export_url(configured) == configured


@pytest.mark.parametrize(
    "stderr,code",
    [
        (
            "aborting because of server version mismatch: synthetic-secret",
            "registry_pg_dump_version_mismatch",
        ),
        (
            "unsupported startup parameter: options synthetic-secret",
            "registry_pg_dump_startup_options_rejected",
        ),
        ("other synthetic-secret", "registry_pg_dump_failed"),
    ],
)
def test_export_classifies_known_failures_without_raw_diagnostics(
    tmp_path, monkeypatch, stderr, code
):
    tmp_path.chmod(0o700)
    connect = MagicMock()
    connect.return_value.__enter__.return_value = fake_connection()
    monkeypatch.setattr(exporter.psycopg, "connect", connect)
    monkeypatch.setattr(
        exporter.subprocess, "run", MagicMock(return_value=CompletedProcess([], 1, stderr=stderr))
    )
    with pytest.raises(exporter.RegistryDumpError, match=f"^{code}$"):
        exporter.export_dump(
            "postgresql://fixture@127.0.0.1/fixture", tmp_path / "dump", tmp_path / "functions"
        )
    assert not (tmp_path / "functions").exists()
