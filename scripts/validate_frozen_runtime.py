"""Cross-check the reviewed frozen-v1 runtime bundle and identity bindings."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path
from typing import Any

import orjson
import yaml

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_LINE = re.compile(r"^(?P<digest>[0-9a-f]{64})  (?P<path>[^\r\n]+)$")
_EXPECTED_MODEL_ID = "edgar-moe-frozen-v1"
_EXPECTED_VERSION = "1.0.0-frozen"
_EXPECTED_FILES = {
    "ops/frozen/frozen-model.pt": "frozen-model.pt",
    "ops/frozen/locked-test.json": "locked-test.json",
}


def validate_frozen_runtime(
    config_path: Path = Path("config/forward.yaml"),
    *,
    frozen_root: Path | None = None,
) -> list[str]:
    """Return non-sensitive errors for the frozen-v1 identity contract."""
    config_path = config_path.resolve()
    repo_root = config_path.parent.parent
    bundle = (frozen_root or repo_root / "ops/frozen").resolve()
    errors: list[str] = []

    config = _read_yaml_object(config_path, errors, "forward config")
    if config is None:
        return errors
    _validate_config_identity(config, errors)

    manifest = _read_manifest(bundle / "SHA256SUMS", errors)
    for relative_path, filename in _EXPECTED_FILES.items():
        candidate = repo_root / relative_path
        if candidate.parent != bundle:
            errors.append(f"frozen manifest root does not match {relative_path}")
            continue
        if candidate.is_symlink():
            errors.append(f"frozen runtime artifact must not be a symlink: {filename}")
        elif not candidate.is_file():
            errors.append(f"missing frozen runtime artifact: {filename}")
        else:
            observed = _sha256_file(candidate)
            if manifest.get(relative_path) != observed:
                errors.append(f"frozen manifest hash mismatch: {filename}")

    for relative_path in _EXPECTED_FILES:
        if relative_path not in manifest:
            errors.append(f"frozen manifest is missing: {relative_path}")
    unexpected = sorted(set(manifest) - set(_EXPECTED_FILES))
    for relative_path in unexpected:
        errors.append(f"frozen manifest contains unexpected file: {relative_path}")

    model_digest = str(config.get("artifact_sha256", ""))
    if not _SHA256.fullmatch(model_digest):
        errors.append("forward config artifact_sha256 is not a lowercase SHA-256 digest")
    else:
        observed_model = repo_root / "ops/frozen/frozen-model.pt"
        if observed_model.is_file() and _sha256_file(observed_model) != model_digest:
            errors.append("forward config artifact_sha256 does not match frozen-model.pt")

    locked_path = repo_root / "ops/frozen/locked-test.json"
    locked_payload = _read_json_object(locked_path, errors, "locked-test artifact")
    if locked_payload is None:
        return errors
    _validate_locked_identity(locked_payload, config, errors)
    return errors


def _validate_config_identity(config: dict[str, Any], errors: list[str]) -> None:
    if config.get("model_id") != _EXPECTED_MODEL_ID:
        errors.append(f"forward config model_id must remain {_EXPECTED_MODEL_ID}")
    if config.get("version") != _EXPECTED_VERSION:
        errors.append(f"forward config version must remain {_EXPECTED_VERSION}")
    for field, filename in (
        ("model_path", "frozen-model.pt"),
        ("locked_result_path", "locked-test.json"),
    ):
        value = config.get(field)
        if not isinstance(value, str) or Path(value).name != filename:
            errors.append(f"forward config {field} must reference {filename}")
    for field in ("training_dataset_id", "selection_hash", "locked_test_hash"):
        if not isinstance(config.get(field), str) or not str(config[field]).strip():
            errors.append(f"forward config {field} is missing")


def _validate_locked_identity(
    payload: dict[str, Any], config: dict[str, Any], errors: list[str]
) -> None:
    stored_hash = str(payload.get("locked_test_hash", ""))
    unsigned = dict(payload)
    unsigned.pop("locked_test_hash", None)
    computed_hash = hashlib.sha256(
        orjson.dumps(unsigned, option=orjson.OPT_SORT_KEYS)
    ).hexdigest()
    if not _SHA256.fullmatch(stored_hash) or stored_hash != computed_hash:
        errors.append("locked-test artifact content hash is invalid")
    expected = {
        "locked_test_hash": str(config.get("locked_test_hash", "")),
        "model_sha256": str(config.get("artifact_sha256", "")),
        "dataset_id": str(config.get("training_dataset_id", "")),
        "selection_hash": str(config.get("selection_hash", "")),
    }
    for field, expected_value in expected.items():
        if str(payload.get(field, "")) != expected_value:
            errors.append(f"locked-test artifact identity mismatch: {field}")


def _read_yaml_object(path: Path, errors: list[str], label: str) -> dict[str, Any] | None:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        errors.append(f"{label} is not readable")
        return None
    if not isinstance(payload, dict):
        errors.append(f"{label} must be a YAML object")
        return None
    return payload


def _read_json_object(path: Path, errors: list[str], label: str) -> dict[str, Any] | None:
    try:
        payload = orjson.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, orjson.JSONDecodeError):
        errors.append(f"{label} is not readable")
        return None
    if not isinstance(payload, dict):
        errors.append(f"{label} must be a JSON object")
        return None
    return payload


def _read_manifest(path: Path, errors: list[str]) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        errors.append("frozen SHA256SUMS is not readable")
        return {}
    manifest: dict[str, str] = {}
    for line_number, line in enumerate(lines, 1):
        match = _MANIFEST_LINE.fullmatch(line)
        if match is None:
            errors.append(f"frozen SHA256SUMS has an invalid line: {line_number}")
            continue
        relative_path = match.group("path")
        if relative_path in manifest:
            errors.append(f"frozen SHA256SUMS contains a duplicate path: {relative_path}")
        manifest[relative_path] = match.group("digest")
    return manifest


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/forward.yaml"))
    parser.add_argument("--frozen-root", type=Path, default=None)
    args = parser.parse_args()
    errors = validate_frozen_runtime(args.config, frozen_root=args.frozen_root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("Frozen v1 runtime bundle validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
