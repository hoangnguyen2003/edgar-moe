from __future__ import annotations

from datetime import date
from pathlib import Path
from threading import RLock
from typing import Any, cast

import orjson


class SnapshotNotFoundError(FileNotFoundError):
    pass


class SnapshotRepository:
    """Thread-safe read-through repository for immutable exported research snapshots."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = RLock()
        self._mtime_ns: int | None = None
        self._snapshot: dict[str, Any] | None = None

    def load(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                raise SnapshotNotFoundError(
                    f"Snapshot not found at {self.path}. Run `edgar-moe demo` first."
                )
            modified = self.path.stat().st_mtime_ns
            if self._snapshot is None or modified != self._mtime_ns:
                payload = orjson.loads(self.path.read_bytes())
                self._validate_shape(payload)
                self._snapshot = payload
                self._mtime_ns = modified
            return self._snapshot

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
    ) -> dict[str, Any]:
        rows = self.load()["events"]
        filtered = []
        for row in rows:
            event_date = date.fromisoformat(row["entry_date"])
            if ticker and row["ticker"].upper() != ticker.upper():
                continue
            if form and row["form"] != form:
                continue
            if direction and row["direction"] != direction:
                continue
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
