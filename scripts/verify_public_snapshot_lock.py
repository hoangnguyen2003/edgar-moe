"""Verify the content-addressed identity of the immutable public v1 snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

_EXPECTED_SNAPSHOT_PATH = "data/demo/snapshot.json"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_LOCK_FIELDS = {
    "schema_version",
    "path",
    "sha256",
    "data_mode",
    "as_of",
    "selection_hash",
    "locked_test_hash",
}


def validate_public_snapshot_lock(
    lock_path: Path = Path("config/public_snapshot.lock.json"),
    *,
    repo_root: Path | None = None,
) -> list[str]:
    """Return redacted errors when the committed public snapshot changes."""
    root = (repo_root or Path.cwd()).resolve()
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return [f"public snapshot lock is not readable JSON: {lock_path}"]
    if not isinstance(lock, dict):
        return ["public snapshot lock must be a JSON object"]

    errors: list[str] = []
    missing = sorted(_REQUIRED_LOCK_FIELDS.difference(lock))
    if missing:
        errors.append("public snapshot lock is missing fields: " + ", ".join(missing))
    if lock.get("schema_version") != 1:
        errors.append("public snapshot lock schema_version must be 1")
    relative_path = lock.get("path")
    if relative_path != _EXPECTED_SNAPSHOT_PATH:
        errors.append(f"public snapshot lock path must be {_EXPECTED_SNAPSHOT_PATH}")
        return errors

    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        errors.append("public snapshot lock path escapes the repository root")
        return errors
    if not candidate.is_file():
        errors.append(f"public snapshot is missing: {relative_path}")
        return errors

    expected_sha = str(lock.get("sha256", ""))
    if not _SHA256.fullmatch(expected_sha):
        errors.append("public snapshot lock sha256 must be a lowercase SHA-256 digest")
    else:
        observed_sha = _sha256_file(candidate)
        if observed_sha != expected_sha:
            errors.append("public snapshot bytes do not match the committed SHA-256 lock")

    try:
        snapshot = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        errors.append("public snapshot is not readable JSON")
        return errors
    metadata = snapshot.get("metadata") if isinstance(snapshot, dict) else None
    if not isinstance(metadata, dict):
        errors.append("public snapshot metadata must be an object")
        return errors

    if metadata.get("data_mode") != "authenticated_locked_test":
        errors.append("public snapshot data_mode must remain authenticated_locked_test")
    if metadata.get("research_only") is not True:
        errors.append("public snapshot research_only must remain true")
    for field in ("data_mode", "as_of", "selection_hash", "locked_test_hash"):
        if lock.get(field) != metadata.get(field):
            errors.append(f"public snapshot metadata does not match lock field: {field}")
    _validate_public_provenance(root, lock, errors)
    return errors


def _validate_public_provenance(root: Path, lock: dict[str, object], errors: list[str]) -> None:
    """Cross-check the published identity without requiring raw source data."""
    manifest_path = root / "public" / "data-provenance.json"
    if not manifest_path.is_file():
        return
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        errors.append("public provenance manifest is not readable JSON")
        return
    snapshot = payload.get("snapshot") if isinstance(payload, dict) else None
    if not isinstance(snapshot, dict):
        errors.append("public provenance snapshot must be an object")
        return
    for field in (
        "path",
        "data_mode",
        "as_of",
        "sha256",
        "selection_hash",
        "locked_test_hash",
    ):
        if snapshot.get(field) != lock.get(field):
            errors.append(f"public provenance does not match lock field: {field}")
    if snapshot.get("research_only") is not True:
        errors.append("public provenance snapshot research_only must remain true")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=Path("config/public_snapshot.lock.json"))
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    errors = validate_public_snapshot_lock(args.lock)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    lock = json.loads(args.lock.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {"status": "verified", "path": lock["path"], "sha256": lock["sha256"]},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
