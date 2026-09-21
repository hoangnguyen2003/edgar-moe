from __future__ import annotations

import importlib.util
import json
from hashlib import sha256
from pathlib import Path

_SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "verify_public_snapshot_lock.py"
_SPEC = importlib.util.spec_from_file_location("verify_public_snapshot_lock", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def write_locked_fixture(root: Path) -> tuple[Path, Path]:
    snapshot_path = root / "data" / "demo" / "snapshot.json"
    snapshot_path.parent.mkdir(parents=True)
    snapshot = {
        "metadata": {
            "data_mode": "authenticated_locked_test",
            "as_of": "2026-07-31",
            "selection_hash": "a" * 64,
            "locked_test_hash": "b" * 64,
            "research_only": True,
        }
    }
    snapshot_bytes = (json.dumps(snapshot, sort_keys=True) + "\n").encode("utf-8")
    snapshot_path.write_bytes(snapshot_bytes)
    lock_path = root / "config" / "public_snapshot.lock.json"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "path": "data/demo/snapshot.json",
                "sha256": sha256(snapshot_bytes).hexdigest(),
                "data_mode": "authenticated_locked_test",
                "as_of": "2026-07-31",
                "selection_hash": "a" * 64,
                "locked_test_hash": "b" * 64,
            }
        ),
        encoding="utf-8",
    )
    return lock_path, snapshot_path


def test_checked_in_public_snapshot_lock_is_valid() -> None:
    assert _MODULE.validate_public_snapshot_lock() == []


def test_snapshot_lock_rejects_changed_bytes(tmp_path: Path) -> None:
    lock_path, snapshot_path = write_locked_fixture(tmp_path)
    snapshot_path.write_text(snapshot_path.read_text(encoding="utf-8") + " \n", encoding="utf-8")

    errors = _MODULE.validate_public_snapshot_lock(lock_path, repo_root=tmp_path)

    assert errors == ["public snapshot bytes do not match the committed SHA-256 lock"]


def test_snapshot_lock_rejects_identity_change(tmp_path: Path) -> None:
    lock_path, snapshot_path = write_locked_fixture(tmp_path)
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    payload["metadata"]["data_mode"] = "synthetic_fixture"
    snapshot_path.write_text(json.dumps(payload), encoding="utf-8")

    errors = _MODULE.validate_public_snapshot_lock(lock_path, repo_root=tmp_path)

    assert "public snapshot data_mode must remain authenticated_locked_test" in errors
    assert "public snapshot metadata does not match lock field: data_mode" in errors


def test_snapshot_lock_rejects_published_provenance_drift(tmp_path: Path) -> None:
    lock_path, _ = write_locked_fixture(tmp_path)
    public_path = tmp_path / "public" / "data-provenance.json"
    public_path.parent.mkdir(parents=True)
    public_path.write_text(
        json.dumps(
            {
                "snapshot": {
                    "path": "data/demo/snapshot.json",
                    "data_mode": "authenticated_locked_test",
                    "as_of": "2026-07-31",
                    "sha256": "c" * 64,
                    "selection_hash": "a" * 64,
                    "locked_test_hash": "b" * 64,
                    "research_only": True,
                }
            }
        ),
        encoding="utf-8",
    )

    errors = _MODULE.validate_public_snapshot_lock(lock_path, repo_root=tmp_path)

    assert "public provenance does not match lock field: sha256" in errors
