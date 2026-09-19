from __future__ import annotations

from pathlib import Path

import pytest

from edgar_moe.forward.database import RegistryDatabase
from edgar_moe.forward.restore_rehearsal import (
    RestoreRehearsalError,
    rehearse_sqlite_restore,
)


def make_registry(path: Path) -> None:
    database = RegistryDatabase(f"sqlite:///{path}")
    database.create_schema()
    database.dispose()


def test_sqlite_restore_rehearsal_copies_schema_and_probes_read_path(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite3"
    destination = tmp_path / "restored.sqlite3"
    make_registry(source)

    report = rehearse_sqlite_restore(source, destination)

    assert report["status"] == "passed"
    assert report["scope"] == "development_only"
    assert report["counts_match"] is True
    assert all(value == 0 for value in report["source_counts"].values())
    assert report["read_path"]["status"] == "passed"
    assert destination.is_file()


def test_sqlite_restore_rehearsal_never_overwrites_destination(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite3"
    destination = tmp_path / "restored.sqlite3"
    make_registry(source)
    destination.write_bytes(b"keep me")

    with pytest.raises(RestoreRehearsalError, match="already exists"):
        rehearse_sqlite_restore(source, destination)

    assert destination.read_bytes() == b"keep me"


def test_sqlite_restore_rehearsal_rejects_missing_registry_tables(tmp_path: Path) -> None:
    source = tmp_path / "not-a-registry.sqlite3"
    source.touch()

    with pytest.raises(RestoreRehearsalError, match="missing forward-registry tables"):
        rehearse_sqlite_restore(source, tmp_path / "restored.sqlite3")
