from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "validate_redacted_artifacts.py"
_SPEC = importlib.util.spec_from_file_location("validate_redacted_artifacts", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

validate_redacted_artifacts = _MODULE.validate_redacted_artifacts


def test_redacted_artifact_scan_allows_content_addressed_provider_neutral_text(
    tmp_path: Path,
) -> None:
    (tmp_path / "summary.json").write_text(
        '{"status":"passed","uri":"r2://private-bucket/sha256/ab/file"}\n',
        encoding="utf-8",
    )

    assert validate_redacted_artifacts(tmp_path) == []


def test_redacted_artifact_scan_rejects_credentials_without_echoing_values(
    tmp_path: Path,
) -> None:
    (tmp_path / "error.txt").write_text(
        "connection failed for postgresql://reader:super-secret@example.test/db "
        "token=abcdef1234567890",
        encoding="utf-8",
    )

    findings = validate_redacted_artifacts(tmp_path)

    assert findings == [
        "database URL with password pattern found: error.txt",
        "URL with credentials pattern found: error.txt",
        "credential assignment pattern found: error.txt",
    ]
    assert "super-secret" not in " ".join(findings)
    assert "abcdef1234567890" not in " ".join(findings)


def test_redacted_artifact_scan_rejects_private_key_and_binary_files(tmp_path: Path) -> None:
    (tmp_path / "key.txt").write_text("-----BEGIN PRIVATE KEY-----", encoding="utf-8")
    (tmp_path / "dump.bin").write_bytes(b"\x00\xff\x00")

    findings = validate_redacted_artifacts(tmp_path)

    assert "private key header pattern found: key.txt" in findings
    assert "non-text evidence file requires review: dump.bin" in findings


def test_redacted_artifact_scan_rejects_oversized_files(tmp_path: Path) -> None:
    (tmp_path / "large.txt").write_text("0123456789", encoding="utf-8")

    assert validate_redacted_artifacts(tmp_path, max_bytes=5) == [
        "evidence file exceeds 5 bytes: large.txt"
    ]
