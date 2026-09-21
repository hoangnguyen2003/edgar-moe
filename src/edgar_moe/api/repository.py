from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from threading import RLock
from typing import Any, cast

import orjson

_EXPECTED_SNAPSHOT_PATH = "data/demo/snapshot.json"
_REQUIRED_LOCK_FIELDS = {
    "schema_version",
    "path",
    "sha256",
    "data_mode",
    "as_of",
    "selection_hash",
    "locked_test_hash",
}


class SnapshotNotFoundError(FileNotFoundError):
    pass


class SnapshotIntegrityError(ValueError):
    """Raised when a snapshot does not match its reviewed immutable lock."""


class SnapshotRepository:
    """Thread-safe read-through repository for immutable exported research snapshots."""

    def __init__(self, path: str | Path, *, lock_path: str | Path | None = None) -> None:
        self.path = Path(path)
        self.lock_path = Path(lock_path) if lock_path is not None else None
        self._lock = RLock()
        self._mtime_ns: int | None = None
        self._lock_mtime_ns: int | None = None
        self._snapshot: dict[str, Any] | None = None
        self._snapshot_sha256: str | None = None

    def load(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                raise SnapshotNotFoundError(
                    f"Snapshot not found at {self.path}. Run `edgar-moe demo` first."
                )
            modified = self.path.stat().st_mtime_ns
            lock_modified = self._lock_mtime_ns_for_cache()
            if (
                self._snapshot is None
                or modified != self._mtime_ns
                or lock_modified != self._lock_mtime_ns
            ):
                raw = self.path.read_bytes()
                payload = orjson.loads(raw)
                self._validate_shape(payload)
                if self.lock_path is not None:
                    self._verify_lock(raw, payload)
                self._snapshot = payload
                self._mtime_ns = modified
                self._lock_mtime_ns = lock_modified
                self._snapshot_sha256 = hashlib.sha256(raw).hexdigest()
            return self._snapshot

    def _lock_mtime_ns_for_cache(self) -> int | None:
        if self.lock_path is None:
            return None
        try:
            return self.lock_path.stat().st_mtime_ns
        except OSError as error:
            raise SnapshotIntegrityError("public snapshot lock is unavailable") from error

    def _verify_lock(self, raw_snapshot: bytes, snapshot: dict[str, Any]) -> None:
        assert self.lock_path is not None
        try:
            lock = orjson.loads(self.lock_path.read_bytes())
        except (OSError, orjson.JSONDecodeError) as error:
            raise SnapshotIntegrityError("public snapshot lock is not readable") from error
        if not isinstance(lock, dict) or set(lock) != _REQUIRED_LOCK_FIELDS:
            raise SnapshotIntegrityError("public snapshot lock schema is invalid")
        if lock.get("schema_version") != 1 or lock.get("path") != _EXPECTED_SNAPSHOT_PATH:
            raise SnapshotIntegrityError("public snapshot lock identity is invalid")

        repository_root = self.lock_path.resolve().parent.parent
        expected_snapshot = (repository_root / _EXPECTED_SNAPSHOT_PATH).resolve()
        if self.path.resolve() != expected_snapshot:
            raise SnapshotIntegrityError("public snapshot path does not match its lock")

        expected_sha = lock.get("sha256")
        if (
            not isinstance(expected_sha, str)
            or len(expected_sha) != 64
            or expected_sha != expected_sha.lower()
            or any(character not in "0123456789abcdef" for character in expected_sha)
            or hashlib.sha256(raw_snapshot).hexdigest() != expected_sha
        ):
            raise SnapshotIntegrityError("public snapshot bytes do not match their lock")

        metadata = snapshot.get("metadata")
        if not isinstance(metadata, dict) or metadata.get("research_only") is not True:
            raise SnapshotIntegrityError("public snapshot metadata is not research-only")
        for field in ("data_mode", "as_of", "selection_hash", "locked_test_hash"):
            if lock.get(field) != metadata.get(field):
                raise SnapshotIntegrityError("public snapshot metadata does not match its lock")

    def frozen_identity(self) -> dict[str, Any]:
        """Return the public, content-addressed identity of the frozen snapshot."""
        snapshot = self.load()
        metadata = snapshot.get("metadata")
        if not isinstance(metadata, dict):
            raise ValueError("Invalid snapshot; metadata must be an object")
        if self._snapshot_sha256 is None:
            raise ValueError("Invalid snapshot; content hash is unavailable")
        required = ("data_mode", "as_of", "selection_hash", "locked_test_hash")
        if any(not isinstance(metadata.get(field), str) for field in required):
            raise ValueError("Invalid snapshot; frozen identity metadata is incomplete")
        if metadata.get("research_only") is not True:
            raise ValueError("Invalid snapshot; frozen snapshot must be research-only")
        return {
            "path": "data/demo/snapshot.json",
            "sha256": self._snapshot_sha256,
            "data_mode": metadata["data_mode"],
            "as_of": metadata["as_of"],
            "selection_hash": metadata["selection_hash"],
            "locked_test_hash": metadata["locked_test_hash"],
            "research_only": True,
        }

    def summary(self) -> dict[str, Any]:
        snapshot = self.load()
        return {
            "metadata": snapshot["metadata"],
            "summary": snapshot["summary"],
            "predictive_metrics": snapshot["predictive_metrics"],
            "portfolio_scenarios": snapshot["portfolio_scenarios"],
        }

    def experiments(self) -> list[dict[str, Any]]:
        return list(self.load()["experiments"])

    def event_page(
        self,
        *,
        ticker: str | None = None,
        form: str | None = None,
        direction: str | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        cursor: str | None = None,
        limit: int = 25,
        query: str | None = None,
    ) -> dict[str, Any]:
        rows = self.load()["events"]
        needle = query.strip().lower() if query else ""
        filtered = []
        for row in rows:
            if ticker and row["ticker"].upper() != ticker.upper():
                continue
            if form and row["form"] != form:
                continue
            if direction and row["direction"] != direction:
                continue
            if needle and needle not in f"{row['ticker']} {row['company_name']}".lower():
                continue
            if from_date or to_date:
                event_date = date.fromisoformat(row["entry_date"])
                if from_date and event_date < from_date:
                    continue
                if to_date and event_date > to_date:
                    continue
            filtered.append(row)
        offset = _decode_cursor(cursor)
        items = filtered[offset : offset + limit]
        next_offset = offset + len(items)
        return {
            "items": items,
            "next_cursor": str(next_offset) if next_offset < len(filtered) else None,
            "total": len(filtered),
        }

    def event(self, accession_number: str) -> dict[str, Any] | None:
        for row in self.load()["events"]:
            if row["accession_number"] == accession_number:
                return cast(dict[str, Any], row)
        for row in self.load()["latest_signals"]:
            if row["accession_number"] == accession_number:
                return cast(dict[str, Any], row)
        return None

    def latest_signals(self) -> list[dict[str, Any]]:
        return list(self.load()["latest_signals"])

    def equity_curve(self, cost_bps: int) -> dict[str, Any]:
        snapshot = self.load()
        key = f"cost_{cost_bps}bps"
        if key not in snapshot["equity_curves"]:
            raise KeyError(f"Unsupported cost scenario: {cost_bps}")
        metrics = next(
            item for item in snapshot["portfolio_scenarios"] if item["cost_bps"] == cost_bps
        )
        return {"cost_bps": cost_bps, "points": snapshot["equity_curves"][key], "metrics": metrics}

    def methodology(self) -> dict[str, Any]:
        return dict(self.load()["methodology"])

    def freshness(self) -> dict[str, Any]:
        return dict(self.load()["freshness"])

    @staticmethod
    def _validate_shape(payload: Any) -> None:
        required = {
            "metadata",
            "summary",
            "predictive_metrics",
            "portfolio_scenarios",
            "experiments",
            "equity_curves",
            "events",
            "latest_signals",
            "methodology",
            "freshness",
        }
        if not isinstance(payload, dict) or (missing := required.difference(payload)):
            raise ValueError(f"Invalid snapshot; missing keys: {sorted(missing)}")


def _decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        value = int(cursor)
    except ValueError as error:
        raise ValueError("Cursor must be a non-negative integer") from error
    if value < 0:
        raise ValueError("Cursor must be a non-negative integer")
    return value
