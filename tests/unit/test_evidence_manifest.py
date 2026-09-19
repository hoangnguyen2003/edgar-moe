from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from edgar_moe.forward.evidence_manifest import (
    EvidenceManifestError,
    build_evidence_manifest,
    verify_evidence_manifest,
)


def test_manifest_records_hashes_and_missing_files(tmp_path: Path) -> None:
    present = tmp_path / "diagnostic.json"
    present.write_text('{"status":"ready"}\n', encoding="utf-8")

    manifest = build_evidence_manifest(
        ["diagnostic.json", "missing.json"],
        root=tmp_path,
        context={"run_id": "123", "secret": "must-not-appear"},
    )

    assert manifest["summary"] == {
        "declared_count": 2,
        "present_count": 1,
        "missing_count": 1,
        "invalid_count": 0,
    }
    assert manifest["context"] == {"run_id": "123"}
    verify_evidence_manifest(manifest, root=tmp_path)


def test_manifest_verification_rejects_changed_bytes(tmp_path: Path) -> None:
    path = tmp_path / "diagnostic.json"
    path.write_text("before", encoding="utf-8")
    manifest = build_evidence_manifest([path], root=tmp_path)
    path.write_text("after", encoding="utf-8")

    with pytest.raises(EvidenceManifestError, match="mismatch"):
        verify_evidence_manifest(manifest, root=tmp_path)


def test_manifest_rejects_path_escape(tmp_path: Path) -> None:
    with pytest.raises(EvidenceManifestError, match="escapes root"):
        build_evidence_manifest(["../outside.json"], root=tmp_path)


def test_manifest_hash_rejects_metadata_tampering(tmp_path: Path) -> None:
    path = tmp_path / "diagnostic.json"
    path.write_text("evidence", encoding="utf-8")
    manifest = build_evidence_manifest([path], root=tmp_path)
    manifest["context"]["run_id"] = "tampered"

    with pytest.raises(EvidenceManifestError, match="does not match"):
        verify_evidence_manifest(manifest)


def test_manifest_cli_writes_and_verifies(tmp_path: Path) -> None:
    path = tmp_path / "diagnostic.json"
    path.write_text(json.dumps({"status": "ready"}), encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    subprocess.run(
        [
            sys.executable,
            "scripts/write_forward_evidence_manifest.py",
            "--root",
            str(tmp_path),
            "--output",
            str(manifest_path),
            "--path",
            "diagnostic.json",
            "--run-id",
            "123",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    result = subprocess.run(
        [
            sys.executable,
            "scripts/verify_forward_evidence_manifest.py",
            "--root",
            str(tmp_path),
            "--manifest",
            str(manifest_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout)["status"] == "verified"
