"""Build a content-addressed operator packet from retained redacted files."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import orjson
from validate_redacted_artifacts import validate_redacted_artifacts

from edgar_moe.forward.operator_evidence import (
    OperatorEvidenceError,
    prepare_operator_evidence_packet,
)

_DEFAULT_MAX_BYTES = 4 * 1024 * 1024


def _safe_artifact_path(root: Path, name: str) -> Path:
    """Resolve one declared artifact without allowing path escape or symlinks."""
    relative = Path(name)
    if (
        relative.is_absolute()
        or not name.strip()
        or "\\" in name
        or ":" in name
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise OperatorEvidenceError(f"artifact name is not a safe relative path: {name}")

    candidate = root / relative
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise OperatorEvidenceError(f"artifact escapes evidence root: {name}") from error

    current = candidate
    while current != root:
        if current.is_symlink():
            raise OperatorEvidenceError(f"artifact path contains a symlink: {name}")
        current = current.parent
    if not candidate.is_file():
        raise OperatorEvidenceError(f"declared artifact is not a regular file: {name}")
    return candidate


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _build_packet(draft: dict[str, object], root: Path, *, max_bytes: int) -> dict[str, Any]:
    if not root.is_dir() or root.is_symlink():
        raise OperatorEvidenceError("artifact root must be a real directory")
    root = root.resolve()

    findings = validate_redacted_artifacts(root, max_bytes=max_bytes)
    if findings:
        raise OperatorEvidenceError("retained artifacts failed redaction validation")

    raw_artifacts = draft.get("artifacts")
    if not isinstance(raw_artifacts, list):
        raise OperatorEvidenceError("packet draft artifacts must be a list")

    declared: dict[str, dict[str, object]] = {}
    for raw_artifact in raw_artifacts:
        if not isinstance(raw_artifact, dict):
            raise OperatorEvidenceError("each packet artifact must be an object")
        name = raw_artifact.get("name")
        if not isinstance(name, str):
            raise OperatorEvidenceError("each packet artifact must declare a name")
        if name in declared:
            raise OperatorEvidenceError(f"duplicate packet artifact: {name}")
        path = _safe_artifact_path(root, name)
        sha256, size_bytes = _hash_file(path)
        artifact = dict(raw_artifact)
        artifact["sha256"] = sha256
        artifact["size_bytes"] = size_bytes
        declared[name] = artifact

    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    undeclared = actual - set(declared)
    if undeclared:
        raise OperatorEvidenceError(
            "artifact root contains undeclared files: " + ", ".join(sorted(undeclared))
        )

    packet_draft = dict(draft)
    packet_draft["artifacts"] = [declared[name] for name in declared]
    return cast(dict[str, Any], prepare_operator_evidence_packet(packet_draft))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="redacted packet draft")
    parser.add_argument(
        "--artifact-root",
        type=Path,
        required=True,
        help="directory containing exactly the declared retained artifacts",
    )
    parser.add_argument("--output", type=Path, required=True, help="content-addressed packet")
    parser.add_argument("--max-bytes", type=int, default=_DEFAULT_MAX_BYTES)
    args = parser.parse_args()
    try:
        payload = orjson.loads(args.input.read_bytes())
        if not isinstance(payload, dict):
            raise OperatorEvidenceError("packet draft must be a JSON object")
        packet = _build_packet(payload, args.artifact_root, max_bytes=args.max_bytes)
        if args.output.exists() or args.output.is_symlink():
            raise OperatorEvidenceError(f"refusing to overwrite existing packet: {args.output}")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        staging = args.output.with_name(f".{args.output.name}.staging-{uuid4().hex}")
        try:
            staging.write_bytes(
                orjson.dumps(packet, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS)
            )
            os.link(staging, args.output)
        finally:
            staging.unlink(missing_ok=True)
    except (OSError, TypeError, ValueError) as error:
        print(f"Operator evidence packet build failed: {type(error).__name__}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "status": "written",
                "packet": str(args.output),
                "packet_sha256": packet["packet_sha256"],
                "artifact_count": len(packet["artifacts"]),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
