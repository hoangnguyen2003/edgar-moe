from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from edgar_moe.api.repository import SnapshotIntegrityError, SnapshotRepository


def _write_locked_repository(root: Path) -> tuple[Path, Path]:
    snapshot_path = root / "data" / "demo" / "snapshot.json"
    snapshot_path.parent.mkdir(parents=True)
    snapshot = {
        "metadata": {
            "data_mode": "authenticated_locked_test",
            "as_of": "2026-07-31",
            "selection_hash": "a" * 64,
            "locked_test_hash": "b" * 64,
            "research_only": True,
        },
        "summary": {},
        "predictive_metrics": {},
        "portfolio_scenarios": [],
        "experiments": [],
        "equity_curves": {},
        "events": [],
        "latest_signals": [],
        "methodology": {},
        "freshness": {},
    }
    raw = (json.dumps(snapshot, sort_keys=True) + "\n").encode("utf-8")
    snapshot_path.write_bytes(raw)

    lock_path = root / "config" / "public_snapshot.lock.json"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "path": "data/demo/snapshot.json",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "data_mode": "authenticated_locked_test",
                "as_of": "2026-07-31",
                "selection_hash": "a" * 64,
                "locked_test_hash": "b" * 64,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return snapshot_path, lock_path


def test_locked_repository_verifies_snapshot_and_identity(tmp_path: Path) -> None:
    snapshot_path, lock_path = _write_locked_repository(tmp_path)

    repository = SnapshotRepository(snapshot_path, lock_path=lock_path)

    assert repository.load()["metadata"]["as_of"] == "2026-07-31"
    assert (
        repository.frozen_identity()["sha256"]
        == hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
    )


def test_locked_repository_rejects_snapshot_tampering_after_cache_warmup(tmp_path: Path) -> None:
    snapshot_path, lock_path = _write_locked_repository(tmp_path)
    repository = SnapshotRepository(snapshot_path, lock_path=lock_path)
    repository.load()

    snapshot_path.write_bytes(snapshot_path.read_bytes() + b" ")

    with pytest.raises(SnapshotIntegrityError):
        repository.load()


def test_locked_repository_rejects_lock_tampering_after_cache_warmup(tmp_path: Path) -> None:
    snapshot_path, lock_path = _write_locked_repository(tmp_path)
    repository = SnapshotRepository(snapshot_path, lock_path=lock_path)
    repository.load()

    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["as_of"] = "2026-08-01"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")

    with pytest.raises(SnapshotIntegrityError):
        repository.load()


def test_unlocked_fixture_repository_remains_supported(tmp_path: Path) -> None:
    snapshot_path, _ = _write_locked_repository(tmp_path)

    assert SnapshotRepository(snapshot_path).load()["events"] == []
