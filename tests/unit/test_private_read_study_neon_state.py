from __future__ import annotations

import importlib.util
import io
import json
import sys
from datetime import UTC, datetime, timedelta

import pytest

SPEC = importlib.util.spec_from_file_location(
    "study290_neon_state", "scripts/capture_private_read_study_neon_state.py"
)
assert SPEC and SPEC.loader
neon = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = neon
SPEC.loader.exec_module(neon)


def environment():
    return {
        "STUDY_NEON_METADATA_KEY": "sentinel-secret-project-key",
        "STUDY_NEON_PROJECT_ID": "invented-project-290",
        "STUDY_NEON_ENDPOINT_ID": "ep-invented-290",
    }


def test_metadata_capture_is_one_fixed_get_and_discards_privileged_fields(monkeypatch):
    calls = []
    raw = {
        "endpoint": {
            "id": "ep-invented-290",
            "project_id": "invented-project-290",
            "current_state": "idle",
            "pending_state": None,
            "disabled": False,
            "private-provider-field": "sentinel-private-response",
        }
    }

    def opened(self, request, **kwargs):
        calls.append(request)
        return io.BytesIO(json.dumps(raw).encode())

    monkeypatch.setattr(
        neon.urllib.request, "build_opener", lambda *a: type("Opener", (), {"open": opened})()
    )
    report = neon.capture(environment())
    assert report["current_state"] == "idle" and report["database_connections"] == 0
    assert len(calls) == 1 and calls[0].get_method() == "GET"
    assert calls[0].full_url == (
        "https://console.neon.tech/api/v2/projects/invented-project-290/endpoints/ep-invented-290"
    )
    assert "sentinel" not in json.dumps(report) and "ep-invented-290" not in json.dumps(report)


@pytest.mark.parametrize(
    "override",
    [
        {"project_id": "foreign-project-290"},
        {"pending_state": "active"},
        {"current_state": "unknown"},
        {"disabled": True},
    ],
)
def test_wrong_targets_and_inflight_transitions_are_not_treated_as_idle(monkeypatch, override):
    raw = {
        "endpoint": {
            "id": "ep-invented-290",
            "project_id": "invented-project-290",
            "current_state": "idle",
            "pending_state": None,
            "disabled": False,
        }
        | override
    }
    monkeypatch.setattr(
        neon.urllib.request,
        "build_opener",
        lambda *a: type(
            "Opener", (), {"open": lambda *a, **kw: io.BytesIO(json.dumps(raw).encode())}
        )(),
    )
    with pytest.raises(neon.Refusal):
        neon.capture(environment())


def states():
    now = datetime.now(UTC)
    before = {
        "scope": "portfolio_study290_neon_endpoint_state",
        "status": "passed",
        "observed_at": (now - timedelta(seconds=60)).isoformat(),
        "capture_started_at": (now - timedelta(seconds=61)).isoformat(),
        "endpoint_sha256": "a" * 64,
        "project_sha256": "b" * 64,
        "current_state": "idle",
    }
    after = before | {
        "current_state": "active",
        "capture_started_at": now.isoformat(),
        "observed_at": now.isoformat(),
    }
    observation = {
        "status": "passed",
        "observed_at": (now - timedelta(seconds=50)).isoformat(),
        "samples": [{"database_resume": "unobserved"}, {"database_resume": "unobserved"}],
    }
    return observation, before, after


def test_idle_transition_is_labeled_at_pair_scope_without_inventing_a_resume_timestamp():
    observation, before, after = states()
    report = neon.annotate(observation, before, after)
    assert report["database_lifecycle"]["cohort"] == "provider_idle_to_active_across_read_pair"
    assert report["samples"][0]["database_resume"] == "provider_idle_to_active_across_read_pair"
    assert report["samples"][1]["database_resume"] == "unobserved"


def test_failed_read_or_missing_transition_does_not_fabricate_successful_resume():
    observation, before, after = states()
    observation["status"] = "degraded"
    report = neon.annotate(observation, before, after)
    assert report["database_lifecycle"]["status"] == "unobserved"
    assert all(s["database_resume"] == "unobserved" for s in report["samples"])


@pytest.mark.parametrize(
    "change",
    [
        {"endpoint_sha256": "c" * 64},
        {"observed_at": (datetime.now(UTC) + timedelta(days=1)).isoformat()},
    ],
)
def test_cross_endpoint_and_future_state_receipts_do_not_join(change):
    observation, before, after = states()
    before.update(change)
    with pytest.raises(neon.Refusal):
        neon.annotate(observation, before, after)


def test_correlation_discards_unrecognized_provider_payload_fields():
    observation, before, after = states()
    before["provider-extra"] = "sentinel-private"
    after["provider-extra"] = "sentinel-private"
    assert "sentinel" not in json.dumps(neon.annotate(observation, before, after))
