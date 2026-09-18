from __future__ import annotations

from edgar_moe.forward.models import Base
from edgar_moe.forward.reader_role import FORBIDDEN_TABLE_PRIVILEGES, READER_TABLES


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
