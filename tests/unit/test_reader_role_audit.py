from __future__ import annotations

import importlib.util
from pathlib import Path

import psycopg

from edgar_moe.forward.models import Base
from edgar_moe.forward.reader_role import FORBIDDEN_TABLE_PRIVILEGES, READER_TABLES

_SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "verify_postgres_reader.py"
_SPEC = importlib.util.spec_from_file_location("verify_postgres_reader", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_reader_contract_covers_every_forward_registry_table() -> None:
    expected = {
        table.name
        for table in Base.metadata.sorted_tables
        if table.name.startswith("forward_")
    }
    assert set(READER_TABLES) == expected


def test_reader_contract_forbids_mutating_and_privileged_table_operations() -> None:
    assert set(FORBIDDEN_TABLE_PRIVILEGES) == {
        "INSERT",
        "UPDATE",
        "DELETE",
        "TRUNCATE",
        "REFERENCES",
        "TRIGGER",
    }


def test_reader_audit_redacts_psycopg_exception_text() -> None:
    error = psycopg.OperationalError(
        "connection failed for postgresql://reader:super-secret@example.test/registry"
    )

    message = _MODULE._safe_error_message(error)

    assert message == "OperationalError"
    assert "super-secret" not in message


def test_reader_audit_redacts_non_driver_error_details() -> None:
    error = _MODULE.ReaderRoleAuditError(
        "reader failed at https://provider.example/db token=super-secret"
    )

    message = _MODULE._safe_error_message(error)

    assert "provider.example" not in message
    assert "super-secret" not in message
    assert "<redacted>" in message
