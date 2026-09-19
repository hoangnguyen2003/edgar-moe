"""Deterministic comparison of source and isolated-restore reports."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class RestoreComparisonError(ValueError):
    """Raised when a restore comparison input is malformed."""


def read_json_report(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RestoreComparisonError(f"cannot read JSON report: {path}") from error
    if not isinstance(payload, dict):
        raise RestoreComparisonError(f"JSON report must be an object: {path}")
    return payload


def parse_counts(payload: dict[str, Any], path: Path) -> dict[str, int]:
    result: dict[str, int] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or isinstance(value, bool) or not isinstance(value, int):
            raise RestoreComparisonError(f"count report must map names to integers: {path}")
        result[key] = value
    return result


def parse_findings(payload: dict[str, Any], path: Path) -> dict[str, dict[str, Any]]:
    raw = payload.get("findings", [])
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise RestoreComparisonError(f"audit findings must be a list of objects: {path}")
    findings: dict[str, dict[str, Any]] = {}
    for item in raw:
        key = json.dumps(item, sort_keys=True, separators=(",", ":"))
        findings[key] = item
    return findings


def compare_reports(
    source_counts: dict[str, int],
    restored_counts: dict[str, int],
    source_audit: dict[str, Any],
    restored_audit: dict[str, Any],
) -> dict[str, Any]:
    """Compare reports and return a redacted, machine-readable result."""
    count_mismatches = {
        key: {"source": source_counts.get(key, 0), "restored": restored_counts.get(key, 0)}
        for key in sorted(set(source_counts) | set(restored_counts))
        if source_counts.get(key, 0) != restored_counts.get(key, 0)
    }
    source_findings = parse_findings(source_audit, Path("source-audit"))
    restored_findings = parse_findings(restored_audit, Path("restored-audit"))
    new_findings = [
        restored_findings[key] for key in sorted(restored_findings.keys() - source_findings.keys())
    ]
    resolved_findings = [
        source_findings[key] for key in sorted(source_findings.keys() - restored_findings.keys())
    ]
    source_status = str(source_audit.get("status", "incomplete"))
    restored_status = str(restored_audit.get("status", "incomplete"))
    status = (
        "passed"
        if not count_mismatches
        and not new_findings
        and source_status != "incomplete"
        and restored_status != "incomplete"
        else "failed"
    )
    report = {
        "schema_version": 2,
        "status": status,
        "observed_at": datetime.now(UTC).isoformat(),
        "counts_match": not count_mismatches,
        "count_mismatches": count_mismatches,
        "source_audit_status": source_status,
        "restored_audit_status": restored_status,
        "new_findings": new_findings,
        "resolved_findings": resolved_findings,
    }
    report["report_hash"] = _content_hash(report)
    return report


def verify_restore_comparison_report(payload: dict[str, Any]) -> None:
    """Raise when a serialized restore comparison was changed after creation."""
    expected = str(payload.get("report_hash", ""))
    if len(expected) != 64:
        raise RestoreComparisonError("restore comparison is missing a SHA-256 report_hash")
    unsigned = dict(payload)
    unsigned.pop("report_hash", None)
    observed = _content_hash(unsigned)
    if observed != expected:
        raise RestoreComparisonError("restore comparison report_hash does not match content")


def _content_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()
