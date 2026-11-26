from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import subprocess
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


def test_signed_delivery_mode_counts_only_evidence_bound_to_the_nominated_hour():
    start = START.replace(minute=0)
    end = start + timedelta(days=7)
    env = environment() | {
        "STUDY_STARTS_AT": start.isoformat(),
        "STUDY_EXPIRES_AT": end.isoformat(),
        "STUDY_DELIVERY_MODE": "cloudflare",
    }
    manual = record(0)
    signed = record(1)
    signed["run"]["event"] = "workflow_dispatch"
    signed["receipt"]["scheduled_at"] = "2026-11-06T09:17:00.000Z"
    report = final.summarize([manual, signed], env, end, baseline())
    assert report["observed_pairs"] == 1
    assert 0 in report["missing_slots"] and 1 not in report["missing_slots"]
    signed["receipt"]["scheduled_at"] = "2026-11-06T08:17:00.000Z"
    with pytest.raises(final.Refusal, match="signed_observation_slot_refused"):
        final.summarize([signed], env, end, baseline())


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


def test_successful_reads_do_not_hide_platform_or_workflow_failures_or_missing_days():
    row = record(62)
    row["run"]["conclusion"] = "failure"
    row["receipt"]["status"] = "degraded"
    row["receipt"]["samples"][1].update(
        platform_start_type="unobserved",
        provider_trace={"status": "refused", "reason": "private-error"},
    )
    report = final.summarize([record(1), row], environment(), END, baseline())
    assert report["http_or_contract_errors"] == 0
    assert report["platform_trace_status_counts"] == {"passed": 3, "refused": 1}
    assert report["workflow_conclusion_counts"] == {"unobserved": 1, "failure": 1}
    assert report["degraded_observation_pairs"] == 1
    assert report["observed_utc_date_count"] == 2
    assert report["pair_start_span_seconds"] == 61 * 3600
    assert report["window_elapsed"] and report["status"] == "incomplete"
    assert "private-error" not in json.dumps(report)
    assert "degraded pairs: 1" in final.markdown(report)


def test_no_observations_has_no_invented_span_and_unknown_metadata_is_not_echoed():
    report = final.summarize([], environment(), END, baseline())
    assert report["pair_start_span_seconds"] is None
    assert report["first_pair_started_at"] is report["last_pair_started_at"] is None
    assert report["observed_utc_date_count"] == 0
    row = record(0)
    row["run"]["conclusion"] = "private-workflow-state"
    row["receipt"]["samples"][1].update(
        platform_start_type="unobserved", provider_trace={"status": "private-trace-state"}
    )
    report = final.summarize([row], environment(), END, baseline())
    assert report["workflow_conclusion_counts"] == {"unobserved": 1}
    assert report["platform_trace_status_counts"] == {"passed": 1, "unobserved": 1}
    assert "private-" not in json.dumps(report)


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


@pytest.mark.parametrize("first_artifact", [True, False])
def test_collector_uses_first_attempt_status_and_artifact(tmp_path, monkeypatch, first_artifact):
    row = record(0)
    initial = row["run"] | {
        "conclusion": "failure",
        "created_at": START.isoformat(),
        "workflow_id": 42,
    }
    latest = initial | {"run_attempt": 2, "conclusion": "success"}
    raw = json.dumps(row["receipt"]).encode()
    archive = zip_bytes(
        {
            "observation.json": raw,
            "SHA256SUMS": (
                hashlib.sha256(raw).hexdigest() + "  reports/private-read-study/observation.json\n"
            ).encode(),
        }
    )
    artifacts = [{"name": "private-read-study-1000-2", "id": 11, "expired": False}]
    if first_artifact:
        artifacts.append({"name": "private-read-study-1000-1", "id": 12, "expired": False})

    prefix = f"repos/{final.REPOSITORY}/actions"

    def fetch(command, endpoint, *args):
        assert command == "api"
        if endpoint == "--method":
            assert f"{prefix}/workflows/{final.WORKFLOW}/runs" in args
            return json.dumps({"workflow_runs": [latest]}).encode()
        if endpoint == f"{prefix}/runs/1000/attempts/1":
            return json.dumps(initial).encode()
        if endpoint == f"{prefix}/runs/1000/artifacts?per_page=100":
            return json.dumps({"artifacts": artifacts}).encode()
        if endpoint == f"{prefix}/artifacts/12/zip" and first_artifact:
            return archive
        pytest.fail(f"Unexpected request {endpoint}")

    monkeypatch.setattr(final, "gh", fetch)
    records, missing = final.collect(environment(), tmp_path)
    if first_artifact:
        assert not missing
        assert records[0]["run"]["conclusion"] == "failure"
        assert records[0]["run"]["run_attempt"] == 1
        assert (tmp_path / "1000" / "observation.json").read_bytes() == raw
    else:
        assert not records and not list(tmp_path.iterdir())
        assert missing == [
            {"run_id": 1000, "conclusion": "failure", "reason": "retained_artifact_missing"}
        ]


def test_original_attempt_must_match_the_listed_run_identity():
    listed = record(0)["run"] | {
        "run_attempt": 2,
        "created_at": START.isoformat(),
        "workflow_id": 42,
    }
    foreign = listed | {"run_attempt": 1, "head_sha": "d" * 40}
    with pytest.raises(final.Refusal, match="first_attempt_provenance_refused"):
        final.first_attempt(listed, lambda *_: json.dumps(foreign).encode())


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
        "STUDY_DELIVERY_MODE",
    }
    assert "secrets." not in json.dumps(doc)
    stop = doc["jobs"]["stop-finished-study"]
    assert stop["permissions"] == {"actions": "write"}
    run = stop["steps"][0]["run"]
    assert "observe-private-read-study.yml" in run and "finalize-private-read-study.yml" in run
    assert "156" not in run


@pytest.mark.parametrize(
    ("observer_state", "observer_put", "expected_status", "expected_calls"),
    [
        ("disabled_manually", "deny", 0, ["read observer", "read finalizer", "stop finalizer"]),
        (
            "active",
            "allow",
            0,
            ["read observer", "stop observer", "read finalizer", "stop finalizer"],
        ),
        (
            "active",
            "race",
            0,
            ["read observer", "stop observer", "read observer", "read finalizer", "stop finalizer"],
        ),
        ("active", "deny", 1, ["read observer", "stop observer", "read observer"]),
    ],
)
def test_shutdown_handles_an_already_disabled_observer(
    tmp_path, monkeypatch, observer_state, observer_put, expected_status, expected_calls
):
    doc = yaml.safe_load(Path(".github/workflows/finalize-private-read-study.yml").read_text())
    run = doc["jobs"]["stop-finished-study"]["steps"][0]["run"]
    fake_gh = tmp_path / "gh"
    fake_gh.write_text(
        """#!/bin/bash
set -eu
base=repos/hoangnguyen2003/edgar-moe/actions/workflows
case "$*" in
  "api $base/observe-private-read-study.yml --jq .state")
    echo 'read observer' >> calls
    cat observer-state
    ;;
  "api --method PUT $base/observe-private-read-study.yml/disable")
    echo 'stop observer' >> calls
    case "$OBSERVER_PUT" in
      allow) exit 0 ;;
      race) echo disabled_manually > observer-state ;;
    esac
    exit 1
    ;;
  "api $base/finalize-private-read-study.yml --jq .state")
    echo 'read finalizer' >> calls
    echo active
    ;;
  "api --method PUT $base/finalize-private-read-study.yml/disable")
    echo 'stop finalizer' >> calls
    ;;
  *) exit 8 ;;
esac
"""
    )
    fake_gh.chmod(0o700)
    (tmp_path / "observer-state").write_text(observer_state)
    monkeypatch.setenv("PATH", str(tmp_path), prepend=":")
    monkeypatch.setenv("GITHUB_REPOSITORY", final.REPOSITORY)
    monkeypatch.setenv("OBSERVER_PUT", observer_put)

    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", run],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == expected_status, result.stderr
    assert (tmp_path / "calls").read_text().splitlines() == expected_calls


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
    assert report["window_setup_evidence"] == {"status": "missing"}
    assert "Local native reference" in (output / "summary.md").read_text()
    assert set(report["reference_sha256"]) == {final.BASELINE.name}
    assert {p.name for p in (output / "references").iterdir()} == {final.BASELINE.name}
    from scripts.validate_redacted_artifacts import validate_redacted_artifacts

    assert validate_redacted_artifacts(output) == []


def test_replacement_window_uses_the_bound_setup_and_query_plans():
    folder = final.SETUP_EVIDENCE / "restart-20261115"
    target = json.loads((folder / "observer-target.json").read_bytes())

    info, references = final.bound_setup_references(target)

    assert info["status"] == "matched" and info["query_plan_shapes"] == 15
    assert (
        info["setup_manifest_sha256"]
        == hashlib.sha256((folder / "SHA256SUMS").read_bytes()).hexdigest()
    )
    assert set(references) == {"observer-target.json", "plans.json"}
    assert references["plans.json"] == (folder / "plans.json").read_bytes()
    assert final.bound_setup_references(target | {"host_sha256": "f" * 64}) == (
        {"status": "missing"},
        {},
    )


@pytest.mark.parametrize("change", ["plan_bytes", "foreign_policy", "duplicate_window"])
def test_unverified_window_references_are_not_attached(tmp_path, monkeypatch, change):
    origin = final.SETUP_EVIDENCE / "restart-20261115"
    target = json.loads((origin / "observer-target.json").read_bytes())
    folder = tmp_path / "restart-20261115"
    folder.mkdir()
    for filename in ("observer-target.json", "plans.json", "SHA256SUMS"):
        (folder / filename).write_bytes((origin / filename).read_bytes())
    monkeypatch.setattr(final, "SETUP_EVIDENCE", tmp_path)
    plan = folder / "plans.json"
    if change == "plan_bytes":
        plan.write_bytes(plan.read_bytes() + b"\n")
    elif change == "foreign_policy":
        contents = plan.read_bytes()
        plan.write_bytes(contents.replace(target["policy_sha256"].encode(), b"f" * 64))
        manifest = folder / "SHA256SUMS"
        manifest.write_text(
            manifest.read_text().replace(
                hashlib.sha256(contents).hexdigest(), hashlib.sha256(plan.read_bytes()).hexdigest()
            )
        )
    else:
        later = tmp_path / "restart-copy"
        later.mkdir()
        (later / "observer-target.json").write_bytes((folder / "observer-target.json").read_bytes())

    expected = "ambiguous" if change == "duplicate_window" else "refused"
    assert final.bound_setup_references(target) == ({"status": expected}, {})
