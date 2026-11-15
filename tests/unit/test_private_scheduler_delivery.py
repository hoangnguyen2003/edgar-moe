from __future__ import annotations

import copy
import hashlib
import hmac
import io
import json
import sys
import zipfile
from datetime import timedelta
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path("scripts").resolve()))
import private_scheduler_scope as scope
import verify_private_scheduler_delivery as delivery

PROBE = delivery.PROBE_TIME
NOW = PROBE + timedelta(days=1)
KEY = "12" * 32


def context():
    return {
        "GITHUB_REPOSITORY": delivery.REPO,
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_RUN_ID": "42",
        "GITHUB_SHA": "a" * 40,
        "SCHEDULER_SIGNATURE_KEY": KEY,
    }


def inputs():
    stamp = PROBE.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return {
        "device": "cpu",
        "mode": "delivery_probe",
        "scheduled_at": stamp,
        "signature": hmac.new(
            KEY.encode(), scope.message(stamp, "delivery_probe"), hashlib.sha256
        ).hexdigest(),
    }


def run():
    return {
        "id": 42,
        "status": "completed",
        "conclusion": "success",
        "event": "workflow_dispatch",
        "head_branch": "main",
        "run_attempt": 1,
        "path": ".github/workflows/" + scope.WORKFLOW,
        "head_repository": {"full_name": delivery.REPO},
        "head_sha": "a" * 40,
        "created_at": (PROBE + timedelta(seconds=5)).isoformat(),
    }


def proof():
    return delivery.record_probe(inputs(), context(), PROBE + timedelta(seconds=10))


def archive(value=None, *, broken_hash=False, extra=False):
    raw = json.dumps(proof() if value is None else value).encode()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("receipt.json", raw)
        digest = "0" * 64 if broken_hash else hashlib.sha256(raw).hexdigest()
        z.writestr("SHA256SUMS", digest + "  reports/scheduler-delivery/receipt.json\n")
        if extra:
            z.writestr("unexpected.txt", "unexpected")
    return out.getvalue()


def test_delivery_only_origin_proof_never_admits_a_provider_cycle():
    r = proof()
    assert r["origin_signature_verified"] is True
    assert all(r[k] == 0 for k in delivery.ZERO_FIELDS)
    assert r["forecast_margin_proven"] is r["issue_complete"] is False
    with pytest.raises(ValueError):
        scope.admit(inputs(), key=KEY, now=PROBE, event="workflow_dispatch", ref="refs/heads/main")
    forged = inputs() | {"mode": "forecast"}
    with pytest.raises(ValueError):
        scope.admit(forged, key=KEY, now=PROBE, event="workflow_dispatch", ref="refs/heads/main")
    assert delivery.validate_probe(run(), r, NOW)["status"] == "passed"


@pytest.mark.parametrize(
    "change",
    [
        {"GITHUB_REF": "refs/heads/other"},
        {"GITHUB_EVENT_NAME": "schedule"},
        {"GITHUB_REPOSITORY": "other/repo"},
        {"GITHUB_RUN_ATTEMPT": "2"},
        {"GITHUB_SHA": "bad"},
        {"GITHUB_RUN_ID": "0"},
        {"SCHEDULER_SIGNATURE_KEY": "ab" * 32},
    ],
)
def test_foreign_rerun_or_unsigned_probe_context_cannot_record_evidence(change):
    with pytest.raises(ValueError):
        delivery.record_probe(inputs(), context() | change, PROBE + timedelta(seconds=10))


@pytest.mark.parametrize("name", delivery.SOURCE_CREDENTIALS)
def test_source_credentials_are_forbidden_in_delivery_only_job(name):
    with pytest.raises(ValueError, match="context_refused"):
        delivery.record_probe(inputs(), context() | {name: "synthetic-secret"}, PROBE)


@pytest.mark.parametrize(
    "change",
    [
        {"scope_id": "expired-scope"},
        {"scope_sha256": "b" * 64},
        {"mode": "forecast"},
        {"origin_signature_verified": False},
        {"provider_requests": 1},
        {"provider_requests": False},
        {"forecasts_invoked": 1},
        {"forecast_margin_proven": True},
        {"issue_complete": True},
        {"source_commit": "b" * 40},
        {"runtime_inputs_sha256": "b" * 64},
        {"run_id": True},
        {"run_attempt": True},
        {"schema_version": True},
        {"run_id": 43},
        {"extra": "ignored"},
    ],
)
def test_mutated_scope_runtime_counts_and_claims_refuse(change):
    with pytest.raises(ValueError):
        delivery.validate_probe(run(), proof() | change, NOW)


@pytest.mark.parametrize(
    "change",
    [
        {"event": "schedule"},
        {"head_branch": "other"},
        {"run_attempt": 2},
        {"run_attempt": True},
        {"id": True},
        {"id": 43},
        {"conclusion": "failure"},
        {"status": "in_progress"},
        {"head_repository": {"full_name": "other/repo"}},
        {"head_sha": "b" * 40},
        {"path": ".github/workflows/other.yml"},
    ],
)
def test_exact_successful_main_workflow_metadata_is_required(change):
    with pytest.raises(ValueError):
        delivery.validate_probe(run() | change, proof(), NOW)


@pytest.mark.parametrize(
    "change",
    [
        {"scheduled_at": (PROBE - timedelta(days=1)).isoformat()},
        {"recorded_at_utc": (PROBE - timedelta(seconds=1)).isoformat()},
        {"recorded_at_utc": (PROBE + timedelta(minutes=91)).isoformat()},
        {"recorded_at_utc": "2026-11-17T07:18:00+01:00"},
    ],
)
def test_stale_foreign_and_inconsistent_clocks_refuse(change):
    with pytest.raises(ValueError):
        delivery.validate_probe(run(), proof() | change, NOW)


@pytest.mark.parametrize("kind", ["hash", "extra", "oversized", "duplicate_json"])
def test_archive_mutations_are_refused(kind):
    if kind == "oversized":
        raw = b"x" * 131073
    elif kind == "duplicate_json":
        out = io.BytesIO()
        payload = b'{"status":"passed","status":"passed"}'
        with zipfile.ZipFile(out, "w") as z:
            z.writestr("receipt.json", payload)
            z.writestr(
                "SHA256SUMS",
                hashlib.sha256(payload).hexdigest() + "  reports/scheduler-delivery/receipt.json",
            )
        raw = out.getvalue()
    else:
        raw = archive(broken_hash=kind == "hash", extra=kind == "extra")
    with pytest.raises(ValueError):
        delivery.parse_archive(raw)


def responses():
    return [
        {"workflow_runs": [run()]},
        run(),
        {
            "artifacts": [
                {
                    "id": 10,
                    "name": "scheduler-delivery-probe-42-1",
                    "expired": False,
                    "size_in_bytes": len(archive()),
                }
            ]
        },
        archive(),
    ]


def request_fixture(values):
    calls = []
    sequence = iter(values)

    def request(path, **kwargs):
        calls.append((path, kwargs))
        return next(sequence)

    return request, calls


def test_future_forecast_requires_exact_artifact_in_four_bounded_metadata_reads():
    request, calls = request_fixture(responses())
    r = delivery.require_probe(NOW, request)
    assert r["status"] == "passed" and r["probe_run_id"] == 42
    assert r["actions_metadata_requests"] == len(calls) == 4
    assert calls[-1][0].endswith("/artifacts/10/zip")
    assert all(path.startswith("repos/" + delivery.REPO + "/actions/") for path, _ in calls)


@pytest.mark.parametrize("rows", [[], [run(), run()], [run() | {"conclusion": "failure"}]])
def test_missing_duplicate_or_failed_delivery_stops_before_artifact_reads(rows):
    request, calls = request_fixture([{"workflow_runs": rows}])
    with pytest.raises(ValueError, match="missing_or_duplicate"):
        delivery.require_probe(NOW, request)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"expired": True},
        {"name": "other-artifact"},
        {"id": True},
        {"size_in_bytes": 131073},
    ],
)
def test_wrong_or_expired_artifact_stops_before_archive_download(change):
    values = copy.deepcopy(responses())
    values[2]["artifacts"][0].update(change)
    request, calls = request_fixture(values)
    with pytest.raises(ValueError, match="artifact_binding"):
        delivery.require_probe(NOW, request)
    assert len(calls) == 3


def test_workflow_separates_probe_credentials_and_places_delivery_gate_before_dependencies():
    doc = yaml.safe_load(Path(".github/workflows/forward-private-scheduler.yml").read_text())
    probe = doc["jobs"]["delivery-probe"]
    assert "inputs.mode == 'delivery_probe'" in probe["if"]
    env_names = set().union(*(s.get("env", {}) for s in probe["steps"]))
    assert env_names == {"SCHEDULER_SIGNATURE_KEY"}
    assert all("uv sync" not in s.get("run", "") for s in probe["steps"])
    forecast = doc["jobs"]["observe"]
    assert "inputs.mode == 'forecast'" in forecast["if"]
    steps = forecast["steps"]
    gate = next(
        i
        for i, s in enumerate(steps)
        if "verify_private_scheduler_delivery.py --require" in s.get("run", "")
    )
    dependency = next(i for i, s in enumerate(steps) if "uv sync" in s.get("run", ""))
    provider = next(i for i, s in enumerate(steps) if "ALPACA_API_KEY" in s.get("env", {}))
    assert gate < dependency < provider
    assert doc["permissions"]["actions"] == "read"
