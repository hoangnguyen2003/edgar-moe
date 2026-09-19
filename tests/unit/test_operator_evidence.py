from __future__ import annotations

import copy
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import orjson
import pytest

from edgar_moe.forward.operator_evidence import (
    OperatorEvidenceError,
    operator_readiness,
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


def _complete_draft(*, observed_at: str = "2026-09-18T00:00:00Z") -> dict[str, object]:
    check_ids = [
        "database_least_privilege",
        "restore_rehearsal",
        "partial_write_reconciliation",
    ]
    artifacts = [
        {
            "name": f"{check_id}.json",
            "sha256": "0" * 64,
            "size_bytes": 12,
            "retention_days": 30,
        }
        for check_id in check_ids
    ]
    return {
        "schema_version": 1,
        "packet_id": "provider-audit-2026-09-19",
        "captured_at": observed_at,
        "operator": "maintainer",
        "provider": "provider-name",
        "target": "isolated-target",
        "scope": check_ids,
        "checks": [
            {
                "check_id": check_id,
                "status": "passed",
                "observed_at": observed_at,
                "evidence_refs": [f"{check_id}.json"],
            }
            for check_id in check_ids
        ],
        "artifacts": artifacts,
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


def test_operator_readiness_blocks_incomplete_required_checks() -> None:
    packet = prepare_operator_evidence_packet(_draft())

    summary = operator_readiness(
        packet,
        now=datetime(2026, 9, 19, tzinfo=UTC),
    )

    assert summary["status"] == "blocked"
    assert summary["blocked_checks"] == [
        "database_least_privilege",
        "restore_rehearsal",
        "partial_write_reconciliation",
    ]


def test_operator_readiness_requires_fresh_passed_checks() -> None:
    packet = prepare_operator_evidence_packet(_complete_draft())
    ready = operator_readiness(
        packet,
        now=datetime(2026, 9, 19, tzinfo=UTC),
    )
    stale = operator_readiness(
        packet,
        now=datetime(2026, 10, 20, tzinfo=UTC),
        max_age=timedelta(days=30),
    )

    assert ready["status"] == "ready"
    assert stale["status"] == "stale"
    assert stale["stale_checks"] == [
        "database_least_privilege",
        "restore_rehearsal",
        "partial_write_reconciliation",
    ]


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
    assert not list(tmp_path.glob(".*.staging-*"))

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


def test_cli_reports_operator_readiness_without_exposing_packet_contents(tmp_path: Path) -> None:
    packet_path = tmp_path / "packet.json"
    packet_path.write_bytes(
        orjson.dumps(prepare_operator_evidence_packet(_complete_draft()))
    )

    ready = subprocess.run(
        [
            sys.executable,
            "scripts/check_operator_readiness.py",
            "--packet",
            str(packet_path),
            "--max-age-days",
            "3650",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    summary = json.loads(ready.stdout)
    assert summary["status"] == "ready"
    assert "isolated-target" not in ready.stdout

    incomplete_path = tmp_path / "incomplete.json"
    incomplete_path.write_bytes(
        orjson.dumps(prepare_operator_evidence_packet(_draft()))
    )
    blocked = subprocess.run(
        [
            sys.executable,
            "scripts/check_operator_readiness.py",
            "--packet",
            str(incomplete_path),
        ],
        capture_output=True,
        text=True,
    )
    assert blocked.returncode == 1
    assert json.loads(blocked.stdout)["status"] == "blocked"
