from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import psycopg
import pytest

_SPEC = importlib.util.spec_from_file_location(
    "verify_postgres_auditor", Path(__file__).parents[2] / "scripts/verify_postgres_auditor.py"
)
assert _SPEC is not None and _SPEC.loader is not None
audit = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(audit)


class Cursor:
    def __init__(self, row: tuple[object, ...] | None) -> None:
        self.row = row

    def fetchone(self) -> tuple[object, ...] | None:
        return self.row


class Connection:
    def __init__(self, reject: str = "", *, version: str = "170000") -> None:
        self.reject = reject
        self.version = version
        self.queries: list[str] = []
        self.options: dict[str, object] = {}

    def __enter__(self) -> Connection:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def execute(self, query: Any, params: object = None) -> Cursor:
        text = str(query)
        self.queries.append(text)
        if text == "SHOW server_version_num":
            return Cursor((self.version,))
        return Cursor((not (self.reject and self.reject in text),))


def test_permission_inspection_has_only_redacted_report_and_read_probes() -> None:
    connection = Connection()
    report = audit.inspect_permissions(connection)  # type: ignore[arg-type]
    assert report["status"] == "passed"
    assert report["identity_redacted"] is True
    assert report["mutation_probes"] is False
    assert report["select_tables"] == ["forward_runs", "forward_artifacts"]
    assert all(query.startswith(("SELECT", "SHOW", "Composed")) for query in connection.queries)
    assert len([query for query in connection.queries if "LIMIT 0" in query]) == 2


@pytest.mark.parametrize(
    ("reject", "code"),
    [
        ("transaction_read_only", "transaction_not_read_only"),
        ("session_user", "session_role_mismatch"),
        ("rolcanlogin", "elevated_role_attributes"),
        ("pg_has_role", "role_membership_not_allowed"),
        ("has_database_privilege", "database_or_schema_access_invalid"),
        ("has_schema_privilege(current_user, n.oid", "schema_create_allowed"),
        ("c.relname IN", "unrelated_table_read_allowed"),
        ("has_column_privilege", "table_write_or_grant_allowed"),
        ("has_sequence_privilege", "sequence_mutation_allowed"),
        ("p.prosecdef", "security_definer_execution_allowed"),
        ("%s", "required_select_missing"),
    ],
)
def test_fail_closed_on_each_contract_violation(reject: str, code: str) -> None:
    with pytest.raises(audit.AuditorPermissionError, match=f"^{code}$"):
        audit.inspect_permissions(Connection(reject))  # type: ignore[arg-type]


@pytest.mark.parametrize(("version", "maintain"), [("160000", False), ("170000", True)])
def test_maintain_privilege_is_checked_only_on_supported_servers(
    version: str, maintain: bool
) -> None:
    connection = Connection(version=version)
    audit.inspect_permissions(connection)  # type: ignore[arg-type]
    assert any("MAINTAIN" in query for query in connection.queries) is maintain


def test_live_connection_is_bounded_and_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = Connection()

    def connect(dsn: str, **kwargs: object) -> Connection:
        assert dsn == "postgresql://fixture"
        connection.options = kwargs
        return connection

    monkeypatch.setattr(audit.psycopg, "connect", connect)
    report = audit.audit_auditor_role("postgresql://fixture")
    assert report["status"] == "passed"
    assert connection.options["connect_timeout"] == 10
    assert "options" not in connection.options
    assert connection.queries[:4] == [
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
        "SET LOCAL statement_timeout = '10s'",
        "SET LOCAL lock_timeout = '1s'",
        "SET LOCAL search_path = pg_catalog",
    ]


def test_empty_url_is_not_a_success() -> None:
    with pytest.raises(audit.AuditorPermissionError, match="database_not_configured"):
        audit.audit_auditor_role("  ")


@pytest.mark.parametrize(
    "error",
    [
        psycopg.OperationalError("postgresql://reader:private-password@private-host/db"),
        ValueError("unsafe input token=private-value"),
    ],
)
def test_driver_and_input_error_details_never_enter_report(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], error: Exception
) -> None:
    def inspect(dsn: str, **kwargs: object) -> dict[str, object]:
        raise error

    monkeypatch.setattr(audit, "audit_auditor_role", inspect)
    assert audit.main([]) == 1
    output = capsys.readouterr()
    assert json.loads(output.out)["error_code"] == "database_inspection_failed"
    assert "private" not in output.out
    assert output.err == ""


def test_cli_contract_failure_has_fixed_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("AUDITOR_DATABASE_URL", raising=False)
    assert audit.main([]) == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == "database_not_configured"


def test_help_does_not_connect_to_a_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(dsn: str) -> dict[str, object]:
        raise AssertionError("help must never connect")

    monkeypatch.setattr(audit, "audit_auditor_role", forbidden)
    with pytest.raises(SystemExit) as stopped:
        audit.main(["--help"])
    assert stopped.value.code == 0


@pytest.mark.parametrize(
    ("profile", "tables", "reads"),
    [
        ("evidence", audit.AUDITOR_TABLES, 2),
        ("registry-reader", audit.READER_TABLES, 8),
        ("empty-restore-target", (), 0),
    ],
)
def test_permission_profiles_are_explicit_and_do_not_claim_future_grants(
    profile: str, tables: tuple[str, ...], reads: int
) -> None:
    connection = Connection()
    report = audit.inspect_permissions(connection, profile=profile)  # type: ignore[arg-type]
    assert report["profile"] == profile
    assert report["select_tables"] == list(tables)
    assert report["future_table_grants_verified"] is False
    assert len([query for query in connection.queries if "LIMIT 0" in query]) == reads
    assert ("empty_restore_target" in report["checks"]) == (profile == "empty-restore-target")


def test_unknown_profile_fails_before_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("invalid profile must never connect")

    monkeypatch.setattr(audit.psycopg, "connect", forbidden)
    with pytest.raises(audit.AuditorPermissionError, match="^invalid_permission_profile$"):
        audit.audit_auditor_role("postgresql://fixture", profile="arbitrary_table")
    with pytest.raises(audit.AuditorPermissionError, match="^invalid_permission_profile$"):
        audit.inspect_permissions(Connection(), profile="arbitrary_table")


def test_cli_forwards_only_allowlisted_profile(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def inspect(dsn: str, *, profile: str) -> dict[str, object]:
        assert profile == "registry-reader"
        return {"status": "passed"}

    monkeypatch.setattr(audit, "audit_auditor_role", inspect)
    assert audit.main(["--profile", "registry-reader"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "passed"
