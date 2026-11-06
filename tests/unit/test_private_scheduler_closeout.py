from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import sys
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path("scripts").resolve()))
spec = importlib.util.spec_from_file_location(
    "scheduler_closeout", "scripts/closeout_private_scheduler.py"
)
assert spec and spec.loader
closeout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(closeout)
NOW = datetime(2026, 11, 11, 12, tzinfo=UTC)


def record(day, identifier):
    start = datetime.fromisoformat(day + "T07:17:00+00:00")
    recorded = start + timedelta(hours=1)
    opening = start.replace(hour=14, minute=30)
    if opening.weekday() == 5:
        opening += timedelta(days=2)
    return {
        "run": {
            "id": identifier,
            "event": "workflow_dispatch",
            "head_branch": "main",
            "run_attempt": 1,
            "head_repository": {"full_name": closeout.REPO},
            "path": ".github/workflows/" + closeout.WORKFLOW,
            "created_at": (start + timedelta(seconds=10)).isoformat(),
            "head_sha": "a" * 40,
            "conclusion": "success",
        },
        "receipt": {
            "scope_id": closeout.SCOPE_ID,
            "scope_sha256": closeout.SCOPE_SHA256,
            "scheduled_at": start.isoformat(),
            "status": "recorded",
            "origin_signature_verified": True,
            "source_cutoff_rule_unchanged": True,
            "filing_coverage_configuration_unchanged": True,
            "actual_forecasts_recorded": True,
            "forecast_margin_proven": True,
            "existing_records_unchanged": True,
            "forecasts_inserted": 2,
            "pre_open_margin_seconds": (opening - recorded).total_seconds(),
            "recorded_at_utc": recorded.isoformat(),
            "target_regular_open_utc": opening.isoformat(),
            "new_storage_upper_bound_bytes": 1234,
            "private_evidence_sha256": "b" * 64,
            "cutoff": day,
        },
    }


def records():
    return [record(day, 100 + i) for i, day in enumerate(closeout.DATES)]


def test_only_three_real_positive_margin_receipts_can_satisfy_acceptance():
    report = closeout.evaluate(records(), NOW)
    assert report["eligible_to_close"] is True and report["scope_finished"] is True
    assert report["provider_requests"] == report["forecast_or_database_requests"] == 0
    body = closeout.closeout_body(report)
    assert "TUE-SAT" in body and "New York cutoff" in body
    assert all(str(100 + i) in body for i in range(3))
    partial = closeout.evaluate(records()[:2], NOW)
    assert partial["eligible_to_close"] is False
    with pytest.raises(ValueError):
        closeout.closeout_body(partial)


@pytest.mark.parametrize(
    "field,value",
    [
        ("forecasts_inserted", 0),
        ("forecasts_inserted", True),
        ("pre_open_margin_seconds", 5399),
        ("pre_open_margin_seconds", True),
        ("status", "no_eligible_forecasts"),
        ("existing_records_unchanged", False),
        ("origin_signature_verified", False),
        ("source_cutoff_rule_unchanged", False),
        ("filing_coverage_configuration_unchanged", False),
        ("private_evidence_sha256", "invalid"),
        ("new_storage_upper_bound_bytes", 1000000001),
        ("cutoff", "2026-11-04"),
    ],
)
def test_zero_short_margin_unbound_or_changed_coverage_remains_open(field, value):
    rows = records()
    rows[0]["receipt"][field] = value
    assert closeout.evaluate(rows, NOW)["eligible_to_close"] is False


@pytest.mark.parametrize("mutation", ["branch", "event", "attempt", "time", "recording", "margin"])
def test_forged_provenance_or_inconsistent_clock_fails_closed(mutation):
    rows = records()
    r = rows[0]
    if mutation == "branch":
        r["run"]["head_branch"] = "untrusted"
    elif mutation == "event":
        r["run"]["event"] = "schedule"
    elif mutation == "attempt":
        r["run"]["run_attempt"] = 2
    elif mutation == "time":
        r["run"]["created_at"] = "2026-11-07T10:00:00Z"
    elif mutation == "recording":
        r["receipt"]["recorded_at_utc"] = "2026-11-07T12:00:00Z"
    else:
        r["receipt"]["pre_open_margin_seconds"] += 100
    with pytest.raises(ValueError):
        closeout.evaluate(rows, NOW)


def test_old_scope_duplicate_or_failed_runs_cannot_complete_new_scope():
    rows = records()
    old = copy.deepcopy(rows[0])
    old["receipt"]["scope_id"] = "older-scope"
    assert closeout.evaluate(rows[1:] + [old], NOW)["eligible_to_close"] is False
    assert closeout.evaluate(rows + [rows[0]], NOW)["eligible_to_close"] is False
    rows[0]["run"]["conclusion"] = "failure"
    assert closeout.evaluate(rows, NOW)["eligible_to_close"] is False


def test_archive_hash_and_flat_inventory_are_required():
    raw = json.dumps(records()[0]["receipt"]).encode()

    def archive(payload, extra=None):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as z:
            z.writestr("receipt.json", payload)
            z.writestr(
                "SHA256SUMS",
                hashlib.sha256(raw).hexdigest() + "  reports/private-scheduler/receipt.json\n",
            )
            if extra:
                z.writestr(extra, b"unsafe")
        return stream.getvalue()

    assert closeout.receipt(archive(raw))["status"] == "recorded"
    for content in (archive(b"{}"), archive(raw, "../escape")):
        with pytest.raises(ValueError):
            closeout.receipt(content)


def test_publish_permission_and_secrets_are_separated_from_receipt_collection():
    doc = yaml.safe_load(Path(".github/workflows/closeout-private-scheduler.yml").read_text())
    assert doc["permissions"] == {"contents": "read", "actions": "read"}
    assert "secrets." not in json.dumps(doc)
    publish = doc["jobs"]["close-verified-issue"]
    assert (
        publish["permissions"] == {"issues": "write"}
        and "outputs.eligible == 'true'" in publish["if"]
    )
    assert "'156'" in publish["steps"][0]["run"]
    stop = doc["jobs"]["stop-finished-scope"]
    assert stop["permissions"] == {"actions": "write"}
    assert "closeout-private-scheduler.yml" in stop["steps"][0]["run"]
    assert "observe-private-read-study" not in json.dumps(doc)


def test_future_recording_and_weekend_entry_cannot_be_used_as_margin_proof():
    rows = records()
    with pytest.raises(ValueError):
        closeout.evaluate(rows, datetime(2026, 11, 7, 7, 18, tzinfo=UTC))
    r = rows[0]["receipt"]
    r["target_regular_open_utc"] = "2026-11-07T14:30:00+00:00"
    r["pre_open_margin_seconds"] = (
        datetime.fromisoformat(r["target_regular_open_utc"])
        - datetime.fromisoformat(r["recorded_at_utc"])
    ).total_seconds()
    with pytest.raises(ValueError):
        closeout.evaluate(rows, NOW)


def test_candidate_report_and_comment_are_operational_and_redacted(tmp_path):
    from scripts.validate_redacted_artifacts import validate_redacted_artifacts

    report = closeout.evaluate(records(), NOW)
    (tmp_path / "report.json").write_text(json.dumps(report))
    (tmp_path / "closeout.md").write_text(closeout.closeout_body(report))
    assert validate_redacted_artifacts(tmp_path) == []


def test_overdue_missing_delivery_is_flagged_without_replaying_requests():
    before = datetime(2026, 11, 6, 12, tzinfo=UTC)
    assert closeout.evaluate([], before)["attention_required"] is False
    late = datetime(2026, 11, 7, 11, tzinfo=UTC)
    report = closeout.evaluate([], late)
    assert report["attention_required"] is True
    assert report["overdue_missing_dates"] == ["2026-11-07"]
    assert report["eligible_to_close"] is False
    assert report["provider_requests"] == report["forecast_or_database_requests"] == 0
