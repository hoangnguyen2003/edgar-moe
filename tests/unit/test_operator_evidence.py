from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import orjson
import pytest

from edgar_moe.forward.operator_evidence import (
    PROVIDER_EVIDENCE_PROFILES,
    OperatorEvidenceError,
    OperatorReadinessError,
    build_operator_readiness_report,
    operator_readiness,
    packet_summary,
    prepare_operator_evidence_packet,
    verify_operator_evidence_packet,
    verify_operator_readiness_report,
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
    return _complete_draft_for_checks(
        list(PROVIDER_EVIDENCE_PROFILES["p0"]), observed_at=observed_at
    )


def _complete_draft_for_checks(
    check_ids: list[str], *, observed_at: str = "2026-09-18T00:00:00Z"
) -> dict[str, object]:
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


def test_packet_summary_is_redacted_and_counts_check_statuses() -> None:
    packet = prepare_operator_evidence_packet(_complete_draft())

    summary = packet_summary(packet)

    assert summary["status"] == "verified"
    assert summary["packet_id"] == packet["packet_id"]
    assert summary["check_counts"]["passed"] == 3
    assert summary["artifact_count"] == 3
    assert "isolated-target" not in json.dumps(summary)


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
    assert summary["profile"] == "p0"
    assert summary["required_check_ids"] == list(PROVIDER_EVIDENCE_PROFILES["p0"])
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


def test_operator_readiness_profiles_expand_required_checks_without_upgrading_them() -> None:
    packet = prepare_operator_evidence_packet(_complete_draft())

    p1 = operator_readiness(
        packet,
        now=datetime(2026, 9, 19, tzinfo=UTC),
        profile="p1",
    )
    full = operator_readiness(
        packet,
        now=datetime(2026, 9, 19, tzinfo=UTC),
        profile="full",
    )

    assert p1["status"] == "blocked"
    assert p1["blocked_checks"] == list(PROVIDER_EVIDENCE_PROFILES["p1"])[3:]
    assert full["status"] == "blocked"
    assert full["blocked_checks"] == list(PROVIDER_EVIDENCE_PROFILES["full"])[3:]


def test_operator_readiness_accepts_a_complete_p1_profile() -> None:
    packet = prepare_operator_evidence_packet(
        _complete_draft_for_checks(list(PROVIDER_EVIDENCE_PROFILES["p1"]))
    )

    summary = operator_readiness(
        packet,
        now=datetime(2026, 9, 19, tzinfo=UTC),
        profile="p1",
    )

    assert summary["status"] == "ready"
    assert summary["required_check_ids"] == list(PROVIDER_EVIDENCE_PROFILES["p1"])


def test_operator_readiness_rejects_unknown_profile() -> None:
    packet = prepare_operator_evidence_packet(_complete_draft())

    with pytest.raises(ValueError, match="profile must be one of"):
        operator_readiness(packet, profile="provider-only")


def test_operator_readiness_rejects_invalid_window_and_naive_clock() -> None:
    packet = prepare_operator_evidence_packet(_complete_draft())

    with pytest.raises(ValueError, match="max_age must be positive"):
        operator_readiness(packet, max_age=timedelta(0))
    with pytest.raises(ValueError, match="now must include a UTC offset"):
        operator_readiness(packet, now=datetime(2026, 9, 19))


def test_operator_readiness_report_is_hash_pinned_and_profile_bound() -> None:
    packet = prepare_operator_evidence_packet(_complete_draft())

    report = build_operator_readiness_report(
        packet,
        now=datetime(2026, 9, 19, tzinfo=UTC),
        max_age=timedelta(days=30),
    )

    assert report["status"] == "ready"
    assert report["profile"] == "p0"
    assert report["packet_sha256"] == packet["packet_sha256"]
    assert len(report["readiness_sha256"]) == 64
    verify_operator_readiness_report(report)


def test_operator_readiness_report_rejects_tampering_and_inconsistent_status() -> None:
    packet = prepare_operator_evidence_packet(_complete_draft())
    report = build_operator_readiness_report(
        packet,
        now=datetime(2026, 9, 19, tzinfo=UTC),
    )

    tampered_hash = copy.deepcopy(report)
    tampered_hash["packet_sha256"] = "0" * 64
    with pytest.raises(OperatorReadinessError, match="content hash"):
        verify_operator_readiness_report(tampered_hash)

    mismatched_profile = copy.deepcopy(report)
    mismatched_profile["profile"] = "p1"
    with pytest.raises(OperatorReadinessError, match="required_check_ids"):
        verify_operator_readiness_report(mismatched_profile)

    tampered_status = copy.deepcopy(report)
    tampered_status["status"] = "blocked"
    tampered_status["readiness_sha256"] = "0" * 64
    with pytest.raises(OperatorReadinessError, match="status is inconsistent"):
        verify_operator_readiness_report(tampered_status)


def test_operator_readiness_report_rejects_non_integral_freshness_window() -> None:
    packet = prepare_operator_evidence_packet(_complete_draft())

    with pytest.raises(ValueError, match="whole number of seconds"):
        build_operator_readiness_report(packet, max_age=timedelta(seconds=1, microseconds=1))


def test_operator_readiness_report_retains_blocked_and_stale_decisions() -> None:
    packet = prepare_operator_evidence_packet(_complete_draft())

    blocked = build_operator_readiness_report(
        packet,
        now=datetime(2026, 9, 19, tzinfo=UTC),
        profile="p1",
    )
    stale = build_operator_readiness_report(
        packet,
        now=datetime(2026, 10, 20, tzinfo=UTC),
        max_age=timedelta(days=30),
    )

    assert blocked["status"] == "blocked"
    assert blocked["blocked_checks"] == list(PROVIDER_EVIDENCE_PROFILES["p1"])[3:]
    assert stale["status"] == "stale"
    assert stale["stale_checks"] == list(PROVIDER_EVIDENCE_PROFILES["p0"])
    verify_operator_readiness_report(blocked)
    verify_operator_readiness_report(stale)


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


def test_cli_builds_packet_from_declared_redacted_artifacts(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    artifact = artifact_root / "reader-audit.json"
    artifact.write_text('{"status":"passed"}\n', encoding="utf-8")
    draft_path = tmp_path / "draft.json"
    packet_path = tmp_path / "packet.json"
    draft_path.write_text(json.dumps(_draft(status="passed")), encoding="utf-8")

    built = subprocess.run(
        [
            sys.executable,
            "scripts/build_operator_evidence_packet.py",
            "--input",
            str(draft_path),
            "--artifact-root",
            str(artifact_root),
            "--output",
            str(packet_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    result = json.loads(built.stdout)
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    assert result["status"] == "written"
    assert result["artifact_count"] == 1
    assert packet["artifacts"] == [
        {
            "name": "reader-audit.json",
            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "size_bytes": artifact.stat().st_size,
            "retention_days": 30,
        }
    ]
    verify_operator_evidence_packet(packet)
    assert not list(tmp_path.glob(".*.staging-*"))


def test_cli_rejects_undeclared_or_sensitive_artifacts(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    (artifact_root / "reader-audit.json").write_text('{"status":"passed"}\n', encoding="utf-8")
    (artifact_root / "unexpected.json").write_text('{}\n', encoding="utf-8")
    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps(_draft(status="passed")), encoding="utf-8")
    packet_path = tmp_path / "packet.json"

    undeclared = subprocess.run(
        [
            sys.executable,
            "scripts/build_operator_evidence_packet.py",
            "--input",
            str(draft_path),
            "--artifact-root",
            str(artifact_root),
            "--output",
            str(packet_path),
        ],
        capture_output=True,
        text=True,
    )
    assert undeclared.returncode == 2
    assert not packet_path.exists()

    (artifact_root / "unexpected.json").unlink()
    (artifact_root / "reader-audit.json").write_text(
        '{"token":"secret-value"}\n', encoding="utf-8"
    )
    sensitive = subprocess.run(
        [
            sys.executable,
            "scripts/build_operator_evidence_packet.py",
            "--input",
            str(draft_path),
            "--artifact-root",
            str(artifact_root),
            "--output",
            str(packet_path),
        ],
        capture_output=True,
        text=True,
    )
    assert sensitive.returncode == 2
    assert not packet_path.exists()


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


def test_cli_selects_readiness_profile(tmp_path: Path) -> None:
    packet_path = tmp_path / "packet.json"
    packet_path.write_bytes(
        orjson.dumps(prepare_operator_evidence_packet(_complete_draft()))
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/check_operator_readiness.py",
            "--packet",
            str(packet_path),
            "--profile",
            "p1",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    summary = json.loads(result.stdout)
    assert summary["profile"] == "p1"
    assert summary["blocked_checks"] == list(PROVIDER_EVIDENCE_PROFILES["p1"])[3:]


def test_cli_builds_verifies_and_refuses_to_overwrite_readiness_report(tmp_path: Path) -> None:
    packet_path = tmp_path / "packet.json"
    report_path = tmp_path / "readiness.json"
    packet_path.write_bytes(
        orjson.dumps(prepare_operator_evidence_packet(_complete_draft()))
    )

    built = subprocess.run(
        [
            sys.executable,
            "scripts/build_operator_readiness.py",
            "--packet",
            str(packet_path),
            "--output",
            str(report_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    built_summary = json.loads(built.stdout)
    assert built_summary["status"] == "written"
    assert built_summary["readiness_status"] == "ready"
    assert not list(tmp_path.glob(".*.staging-*"))

    verified = subprocess.run(
        [
            sys.executable,
            "scripts/verify_operator_readiness.py",
            "--report",
            str(report_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(verified.stdout)["status"] == "verified"

    overwrite = subprocess.run(
        [
            sys.executable,
            "scripts/build_operator_readiness.py",
            "--packet",
            str(packet_path),
            "--output",
            str(report_path),
        ],
        capture_output=True,
        text=True,
    )
    assert overwrite.returncode == 2
