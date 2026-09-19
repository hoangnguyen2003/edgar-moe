from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from edgar_moe.forward.operator_evidence import (
    OperatorEvidenceError,
    prepare_operator_evidence_packet,
    verify_operator_evidence_packet,
)


def _draft(*, status: str = "not_run") -> dict[str, object]:
    check: dict[str, object] = {
        "check_id": "database_least_privilege",
        "status": status,
        "observed_at": "2026-09-19T00:00:00Z",
        "evidence_refs": [],
    }
    if status == "not_run":
        check["notes"] = "The provider audit has not been run yet."
    else:
        check["evidence_refs"] = ["reader-audit.json"]
    return {
        "schema_version": 1,
        "packet_id": "provider-audit-2026-09-19",
        "captured_at": "2026-09-19T00:00:00Z",
        "operator": "maintainer",
        "provider": "provider-name",
        "target": "isolated-target",
        "scope": ["database_least_privilege"],
        "checks": [check],
        "artifacts": (
            [
                {
                    "name": "reader-audit.json",
                    "sha256": "0" * 64,
                    "size_bytes": 12,
                    "retention_days": 30,
                }
            ]
            if status == "passed"
            else []
        ),
        "redaction": {
            "secrets_excluded": True,
            "provider_urls_excluded": True,
            "raw_payloads_excluded": True,
        },
    }


def test_prepare_adds_hash_and_verify_accepts_packet() -> None:
    packet = prepare_operator_evidence_packet(_draft())

    assert len(packet["packet_sha256"]) == 64
    verify_operator_evidence_packet(packet)


def test_verify_rejects_changed_packet_content() -> None:
    packet = prepare_operator_evidence_packet(_draft())
    changed = copy.deepcopy(packet)
    changed["target"] = "different-target"

    with pytest.raises(OperatorEvidenceError, match="packet_sha256"):
        verify_operator_evidence_packet(changed)


def test_prepare_rejects_supplied_digest_mismatch() -> None:
    draft = _draft()
    draft["packet_sha256"] = "0" * 64

    with pytest.raises(OperatorEvidenceError, match="packet_sha256"):
        prepare_operator_evidence_packet(draft)


def test_passed_check_requires_a_retained_artifact() -> None:
    draft = _draft(status="passed")
    draft["artifacts"] = []

    with pytest.raises(OperatorEvidenceError, match="evidence_refs"):
        prepare_operator_evidence_packet(draft)


def test_sensitive_values_are_rejected() -> None:
    draft = _draft()
    check = draft["checks"][0]
    assert isinstance(check, dict)
    check["notes"] = "provider endpoint https://example.invalid/db"

    with pytest.raises(OperatorEvidenceError, match="sensitive value"):
        prepare_operator_evidence_packet(draft)


def test_cli_writes_and_verifies_packet(tmp_path: Path) -> None:
    draft_path = tmp_path / "draft.json"
    packet_path = tmp_path / "packet.json"
    draft_path.write_text(json.dumps(_draft()), encoding="utf-8")

    written = subprocess.run(
        [
            sys.executable,
            "scripts/write_operator_evidence_packet.py",
            "--input",
            str(draft_path),
            "--output",
            str(packet_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(written.stdout)["status"] == "written"

    verified = subprocess.run(
        [
            sys.executable,
            "scripts/verify_operator_evidence_packet.py",
            "--packet",
            str(packet_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    summary = json.loads(verified.stdout)
    assert summary["status"] == "verified"
    assert summary["check_counts"]["not_run"] == 1
