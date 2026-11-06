from __future__ import annotations

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

SPEC = importlib.util.spec_from_file_location(
    "study_finalizer", "scripts/finalize_private_read_study.py"
)
assert SPEC and SPEC.loader
final = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = final
SPEC.loader.exec_module(final)
START = datetime(2026, 11, 6, 8, 15, tzinfo=UTC)
END = START + timedelta(days=7)


def environment():
    return {
        "GITHUB_REPOSITORY": final.REPOSITORY,
        "STUDY_STARTS_AT": START.isoformat(),
        "STUDY_EXPIRES_AT": END.isoformat(),
        "STUDY_POLICY_SHA256": "a" * 64,
        "STUDY_DEPLOYMENT_HOST_SHA256": "b" * 64,
    }


def record(slot):
    return {
        "run": {
            "id": slot + 1000,
            "event": "schedule",
            "head_branch": "main",
            "run_attempt": 1,
            "head_sha": "c" * 40,
        },
        "receipt": {
            "scope": "separate_portfolio_study290_observation",
            "schema_version": 2,
            "host_sha256": "b" * 64,
            "policy_sha256": "a" * 64,
            "response_bodies_retained": False,
            "database_credentials_in_probe": False,
            "observed_at": (START + timedelta(hours=slot, minutes=26)).isoformat(),
            "hourly_slot": slot,
            "database_lifecycle": {"cohort": "provider_idle_to_active_across_read_pair"},
            "samples": [
                {
                    "route": final.ROUTES[slot % 4],
                    "status": "passed",
                    "phase": phase,
                    "edge_cache": "BYPASS",
                    "platform_start_type": "warm",
                    "provider_trace": {"status": "passed", "classification": "warm"},
                    "http_elapsed_us": slot * 100 + 1000,
                    "origin_timing": {
                        key: 123
                        for key in (
                            "preparation_us",
                            "observer_connect_us",
                            "reader_connect_us",
                            "integrity_us",
                            "service_us",
                            "snapshot_us",
                            "lifecycle_us",
                        )
                    }
                    | {"process": "subsequent"},
                }
                for phase in ("first", "paired")
            ],
        },
    }


def baseline():
    return json.loads(final.BASELINE.read_text())


def test_complete_test_fixture_reports_coverage_without_closing_issue_or_establishing_slo():
    report = final.summarize([record(i) for i in range(168)], environment(), END, baseline())
    assert report["status"] == "coverage_complete"
    assert report["actual_http_attempts"] == 336 and report["observed_pairs"] == 168
    assert report["missing_slots"] == [] and report["duplicate_slots"] == {}
    assert report["issue_closed"] is False and report["slo_established"] is False
    assert report["application_requests_by_finalizer"] == 0
    assert all(c["quantiles_us"]["http_elapsed_us"]["n"] == 42 for c in report["cohorts"])
    assert report["local_native_reference"]["total_samples"] == 404


def test_dispatch_is_excluded_and_missing_duplicate_and_unelapsed_windows_remain_incomplete():
    records = [record(i) for i in range(167)]
    manual = record(167)
    manual["run"]["event"] = "workflow_dispatch"
    report = final.summarize(records + [manual], environment(), END, baseline())
    assert report["status"] == "incomplete" and report["missing_slots"] == [167]
    report = final.summarize(records + [record(0)], environment(), END, baseline())
    assert report["duplicate_slots"] == {"0": [1000, 1000]}
    assert (
        final.summarize([record(i) for i in range(168)], environment(), START, baseline())["status"]
        == "incomplete"
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("policy_sha256", "d" * 64),
        ("host_sha256", "d" * 64),
        ("hourly_slot", 2),
        ("response_bodies_retained", True),
    ],
)
def test_foreign_or_mutated_receipts_cannot_enter_report(key, value):
    row = record(0)
    row["receipt"][key] = value
    with pytest.raises(final.Refusal):
        final.summarize([row], environment(), END, baseline())


def test_errors_are_counted_without_including_failed_latency_in_success_quantiles():
    row = record(0)
    row["receipt"]["samples"][0].update(status="failed", reason="http_error", origin_timing=None)
    report = final.summarize([row], environment(), END, baseline())
    assert report["http_or_contract_errors"] == 1 and report["attempt_error_rate"] == 0.5
    assert (
        sum(
            c["quantiles_us"]["http_elapsed_us"]["n"]
            for c in report["cohorts"]
            if c["quantiles_us"]["http_elapsed_us"]
        )
        == 1
    )


def zip_bytes(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return stream.getvalue()


def test_only_flat_hash_verified_artifacts_are_accepted():
    raw = b'{"synthetic":true}\n'
    files = {
        "observation.json": raw,
        "SHA256SUMS": (
            hashlib.sha256(raw).hexdigest() + "  reports/private-read-study/observation.json\n"
        ).encode(),
    }
    assert final.archive_files(zip_bytes(files)) == files
    for alteration in (
        {"../observation.json": raw},
        {"observation.json": b"mutated"},
        {"secret.txt": raw},
    ):
        with pytest.raises(final.Refusal):
            final.archive_files(zip_bytes(files | alteration))


def test_before_expiry_finalizer_performs_no_actions_query_or_download(tmp_path, monkeypatch):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return START

    for key, value in environment().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(final, "datetime", FrozenDatetime)
    monkeypatch.setattr(
        final, "gh", lambda *a: pytest.fail("No Actions reads before report window")
    )
    monkeypatch.setattr(sys, "argv", ["finalizer", "--output-dir", str(tmp_path / "not-created")])
    assert final.main() == 0
    assert not (tmp_path / "not-created").exists()


def test_finalizer_receives_no_provider_secret_and_stops_only_the_study_workflows():
    doc = yaml.safe_load(Path(".github/workflows/finalize-private-read-study.yml").read_text())
    assert doc["permissions"] == {"contents": "read", "actions": "read"}
    report = doc["jobs"]["report"]
    step = next(s for s in report["steps"] if s.get("id") == "report")
    assert set(step["env"]) == {
        "GH_TOKEN",
        "STUDY_STARTS_AT",
        "STUDY_EXPIRES_AT",
        "STUDY_POLICY_SHA256",
        "STUDY_DEPLOYMENT_HOST_SHA256",
    }
    assert "secrets." not in json.dumps(doc)
    stop = doc["jobs"]["stop-finished-study"]
    assert stop["permissions"] == {"actions": "write"}
    run = stop["steps"][0]["run"]
    assert "observe-private-read-study.yml" in run and "finalize-private-read-study.yml" in run
    assert "156" not in run


def test_elapsed_window_builds_redacted_readable_report_and_references(tmp_path, monkeypatch):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return END + timedelta(minutes=6)

    for key, value in environment().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(final, "datetime", FrozenDatetime)
    monkeypatch.setattr(final, "collect", lambda *a: ([record(0)], []))
    monkeypatch.setattr(final, "gh", lambda *a: pytest.fail("No provider or extra metadata calls"))
    output = tmp_path / "packet"
    monkeypatch.setattr(sys, "argv", ["finalizer", "--output-dir", str(output)])
    assert final.main() == 0
    report = json.loads((output / "summary.json").read_text())
    assert report["status"] == "incomplete" and len(report["missing_slots"]) == 167
    assert report["edge_hit_count"] == 0 and report["platform_counts"] == {"warm": 2}
    assert "Local native reference" in (output / "summary.md").read_text()
    assert (output / "references/cloud-reader-query-plans.json").is_file()
    from scripts.validate_redacted_artifacts import validate_redacted_artifacts

    assert validate_redacted_artifacts(output) == []
