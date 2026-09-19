"""Safe, provider-neutral local restore rehearsal for the forward registry.

The production restore procedure is PostgreSQL/provider-specific.  This module
provides a deliberately smaller SQLite rehearsal that is useful on a laptop or
in CI: it copies a consistent database snapshot into a new file, compares every
forward-registry table count, and exercises the same read-only service methods
used by the API.  It never deletes or mutates the source or an existing target.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from edgar_moe.forward.database import RegistryDatabase
from edgar_moe.forward.registry import ForwardRegistry

REGISTRY_TABLES: tuple[str, ...] = (
    "forward_datasets",
    "forward_models",
    "forward_runs",
    "forward_forecasts",
    "forward_labels",
    "forward_artifacts",
    "forward_data_quality_checks",
    "forward_audit_events",
)


class RestoreRehearsalError(RuntimeError):
    """Raised when a local restore rehearsal cannot be completed safely."""


def rehearse_sqlite_restore(source: Path, destination: Path) -> dict[str, Any]:
    """Copy ``source`` to a new SQLite target and verify the registry read path.

    The destination must not already exist.  The resulting report intentionally
    contains only file names, hashes, counts, and aggregate read-path values;
    it does not expose database URLs, row payloads, or forecast identifiers.
    """

    source_path = _resolve_existing_file(source, "source")
    destination_path = _resolve_new_file(destination, "destination")
    if source_path == destination_path:
        raise RestoreRehearsalError("source and destination must be different files")

    started = time.perf_counter()
    try:
        _backup_sqlite(source_path, destination_path)
        source_counts = _table_counts(source_path)
        restored_counts = _table_counts(destination_path)
        read_probe = _probe_read_path(destination_path)
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as error:
        raise RestoreRehearsalError(f"local restore rehearsal failed: {error}") from error

    elapsed_seconds = round(time.perf_counter() - started, 3)
    counts_match = source_counts == restored_counts
    return {
        "schema_version": 1,
        "rehearsal_type": "local_sqlite_restore",
        "scope": "development_only",
        "status": "passed" if counts_match else "failed",
        "observed_at": datetime.now(UTC).isoformat(),
        "elapsed_seconds": elapsed_seconds,
        "source": _file_identity(source_path),
        "destination": _file_identity(destination_path),
        "destination_retained": True,
        "source_counts": source_counts,
        "restored_counts": restored_counts,
        "counts_match": counts_match,
        "read_path": read_probe,
        "limitations": [
            "This rehearsal does not establish PostgreSQL/provider backup recoverability.",
            "This rehearsal does not run the Go evidence auditor or verify external objects.",
            "Use docs/restore-rehearsal.md for production recovery evidence.",
        ],
    }


def _resolve_existing_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise RestoreRehearsalError(f"{label} must be an existing regular file: {path}")
    _reject_broad_path(resolved, label)
    return resolved


def _resolve_new_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    _reject_broad_path(resolved, label)
    if resolved.exists():
        raise RestoreRehearsalError(
            f"{label} already exists; choose a new isolated path (nothing was overwritten): {path}"
        )
    if not resolved.parent.is_dir():
        raise RestoreRehearsalError(f"{label} parent directory does not exist: {resolved.parent}")
    return resolved


def _reject_broad_path(path: Path, label: str) -> None:
    if path in {Path("/"), Path.home()}:
        raise RestoreRehearsalError(f"refusing unsafe {label} path: {path}")


def _backup_sqlite(source: Path, destination: Path) -> None:
    source_uri = f"file:{quote(source.as_posix(), safe='/')}?mode=ro"
    try:
        with (
            sqlite3.connect(source_uri, uri=True) as source_connection,
            sqlite3.connect(destination) as destination_connection,
        ):
            source_connection.backup(destination_connection)
    except sqlite3.Error as error:
        raise RestoreRehearsalError(f"SQLite backup failed: {error}") from error


def _table_counts(path: Path) -> dict[str, int]:
    source_uri = f"file:{quote(path.as_posix(), safe='/')}?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        missing = [table for table in REGISTRY_TABLES if table not in tables]
        if missing:
            raise RestoreRehearsalError(
                f"{path.name} is missing forward-registry tables: {', '.join(missing)}"
            )
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in REGISTRY_TABLES
        }


def _probe_read_path(path: Path) -> dict[str, Any]:
    database = RegistryDatabase(f"sqlite:///{path.as_posix()}")
    try:
        registry = ForwardRegistry(database, actor="restore-rehearsal")
        status = registry.status()
        runs = registry.list_runs(limit=1)
        forecasts = registry.list_forecasts(limit=1)
        quality_checks = registry.list_quality_checks(limit=1)
        performance = registry.performance()
    finally:
        database.dispose()

    return {
        "status": "passed",
        "registry_health": {
            "health_status": status["health_status"],
            "model_count": status["model_count"],
            "run_count": status["run_count"],
            "forecast_count": status["forecast_count"],
            "matured_count": status["matured_count"],
        },
        "sample_rows": {
            "runs": len(runs),
            "forecasts": len(forecasts["items"]),
            "quality_checks": len(quality_checks),
        },
        "forecast_total": forecasts["total"],
        "performance": {
            "forecast_count": performance["forecast_count"],
            "matured_count": performance["matured_count"],
            "coverage": performance["coverage"],
        },
    }


def _file_identity(path: Path) -> dict[str, Any]:
    return {
        "name": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
