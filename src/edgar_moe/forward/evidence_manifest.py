"""Content-addressed inventory for forward-cycle diagnostic evidence."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import orjson

_CONTEXT_KEYS = (
    "workflow",
    "run_id",
    "run_attempt",
    "commit",
    "ref",
    "cutoff",
    "cycle_outcome",
    "device",
)


class EvidenceManifestError(ValueError):
    """Raised when a forward evidence manifest is malformed or changed."""


def build_evidence_manifest(
    paths: Sequence[str | Path],
    *,
    root: str | Path = ".",
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Inventory known evidence paths without reading arbitrary directories."""
    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir():
        raise EvidenceManifestError(f"manifest root must be a directory: {root}")

    entries: list[dict[str, Any]] = []
    observed_paths: set[str] = set()
    for raw_path in paths:
        absolute, relative = _resolve_under_root(raw_path, root_path)
        if relative in observed_paths:
            raise EvidenceManifestError(f"duplicate manifest path: {relative}")
        observed_paths.add(relative)
        if not absolute.exists():
            entries.append({"path": relative, "status": "missing"})
            continue
        if not absolute.is_file():
            entries.append({"path": relative, "status": "not_a_file"})
            continue
        entries.append(
            {
                "path": relative,
                "status": "present",
                "size_bytes": absolute.stat().st_size,
                "sha256": _sha256(absolute),
            }
        )

    entries.sort(key=lambda item: str(item["path"]))
    summary = {
        "declared_count": len(entries),
        "present_count": sum(item["status"] == "present" for item in entries),
        "missing_count": sum(item["status"] == "missing" for item in entries),
        "invalid_count": sum(item["status"] == "not_a_file" for item in entries),
    }
    payload: dict[str, Any] = {
        "schema_version": 1,
        "scope": "forward_cycle_evidence",
        "observed_at": datetime.now(UTC).isoformat(),
        "context": _safe_context(context or {}),
        "files": entries,
        "summary": summary,
    }
    payload["manifest_sha256"] = _content_hash(payload)
    return payload


def verify_evidence_manifest(
    manifest: Mapping[str, Any], *, root: str | Path | None = None
) -> None:
    """Verify manifest content and, when supplied, the referenced file bytes."""
    expected = str(manifest.get("manifest_sha256", ""))
    if len(expected) != 64:
        raise EvidenceManifestError("manifest is missing a SHA-256 manifest_sha256")
    unsigned = dict(manifest)
    unsigned.pop("manifest_sha256", None)
    if _content_hash(unsigned) != expected:
        raise EvidenceManifestError("manifest_sha256 does not match manifest content")

    files = manifest.get("files")
    if not isinstance(files, list):
        raise EvidenceManifestError("manifest files must be a list")
    if root is None:
        return
    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir():
        raise EvidenceManifestError(f"manifest root must be a directory: {root}")
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise EvidenceManifestError("manifest file entries must contain a path")
        absolute, _ = _resolve_under_root(str(item["path"]), root_path)
        status = item.get("status")
        if status == "present":
            if not absolute.is_file():
                raise EvidenceManifestError(f"manifest file is no longer present: {item['path']}")
            if int(item.get("size_bytes", -1)) != absolute.stat().st_size:
                raise EvidenceManifestError(f"manifest size mismatch: {item['path']}")
            if str(item.get("sha256", "")) != _sha256(absolute):
                raise EvidenceManifestError(f"manifest hash mismatch: {item['path']}")
        elif status == "missing":
            if absolute.exists():
                raise EvidenceManifestError(f"manifest missing file appeared: {item['path']}")
        elif status == "not_a_file":
            if absolute.is_file():
                raise EvidenceManifestError(f"manifest invalid path became a file: {item['path']}")
        else:
            raise EvidenceManifestError(f"unknown manifest file status: {status}")


def _resolve_under_root(raw_path: str | Path, root: Path) -> tuple[Path, str]:
    candidate = Path(raw_path).expanduser()
    absolute = (candidate if candidate.is_absolute() else root / candidate).resolve()
    try:
        relative = absolute.relative_to(root).as_posix()
    except ValueError as error:
        raise EvidenceManifestError(f"manifest path escapes root: {raw_path}") from error
    if relative in {"", "."}:
        raise EvidenceManifestError("manifest path must identify a file")
    return absolute, relative


def _safe_context(context: Mapping[str, Any]) -> dict[str, str]:
    return {
        key: str(context[key])
        for key in _CONTEXT_KEYS
        if key in context and context[key] is not None and str(context[key])
    }


def _content_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        orjson.dumps(dict(payload), option=orjson.OPT_SORT_KEYS)
    ).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
