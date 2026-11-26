from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
import urllib.error
from datetime import UTC, datetime, timedelta
from email.message import Message

import pytest

SPEC = importlib.util.spec_from_file_location(
    "portfolio_study_probe", "scripts/observe_private_read_study.py"
)
assert SPEC and SPEC.loader
probe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = probe
SPEC.loader.exec_module(probe)


def environment():
    return {
        "STUDY_DEPLOYMENT_URL": "https://isolated-fixture.vercel.app",
        "STUDY_DEPLOYMENT_HOST_SHA256": hashlib.sha256(b"isolated-fixture.vercel.app").hexdigest(),
        "STUDY_POLICY_SHA256": "a" * 64,
        "STUDY_STARTS_AT": "2026-11-06T00:00:00Z",
        "STUDY_EXPIRES_AT": "2026-11-13T00:00:00Z",
        "STUDY_BEARER_TOKEN": "b" * 64,
        "STUDY_PLATFORM_BYPASS": "synthetic-platform-token-only",
    }


@pytest.mark.parametrize(
    ("attempt", "now", "eligible"),
    [
        ("1", datetime(2026, 11, 5, 23, 59, tzinfo=UTC), False),
        ("1", datetime(2026, 11, 6, tzinfo=UTC), True),
        ("1", datetime(2026, 11, 12, 23, 59, tzinfo=UTC), True),
        ("1", datetime(2026, 11, 13, tzinfo=UTC), False),
        ("2", datetime(2026, 11, 6, tzinfo=UTC), False),
        (None, datetime(2026, 11, 6, tzinfo=UTC), False),
    ],
)
def test_workflow_window_check_only_admits_first_attempts_within_the_window(
    monkeypatch, capsys, attempt, now, eligible
):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(probe, "datetime", FrozenDatetime)
    monkeypatch.setattr(sys, "argv", ["probe", "--check-window"])
    monkeypatch.setenv("STUDY_STARTS_AT", environment()["STUDY_STARTS_AT"])
    monkeypatch.setenv("STUDY_EXPIRES_AT", environment()["STUDY_EXPIRES_AT"])
    if attempt is None:
        monkeypatch.delenv("GITHUB_RUN_ATTEMPT", raising=False)
    else:
        monkeypatch.setenv("GITHUB_RUN_ATTEMPT", attempt)
    monkeypatch.setattr(probe, "observe", lambda *a, **kw: pytest.fail("No provider reads"))

    assert probe.main() == 0
    assert capsys.readouterr().out == f"eligible={str(eligible).lower()}\n"


def test_workflow_window_check_refuses_invalid_configuration_without_output(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["probe", "--check-window"])
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("STUDY_STARTS_AT", "2026-11-06T00:00:01Z")
    monkeypatch.setenv("STUDY_EXPIRES_AT", "2026-11-13T00:00:01Z")

    assert probe.main() == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "study_configuration_refused\n"


class Response(io.BytesIO):
    status = 200

    def __init__(self, body, *, cache="MISS"):
        super().__init__(json.dumps(body).encode())
        self.headers = Message()
        self.headers["cache-control"] = "private, no-store"
        self.headers["x-vercel-cache"] = cache
        self.headers["x-study-policy-sha256"] = "a" * 64
        self.headers["x-study-read-timing"] = (
            ";".join(f"{key}=123" for key in sorted(probe.TIMINGS)) + ";process=first"
        )


def test_header_absent_at_the_edge_is_not_mistaken_for_an_origin_policy_failure(monkeypatch):
    response = Response({"available": True})
    monkeypatch.setattr(
        probe.urllib.request,
        "build_opener",
        lambda *a: type("Opener", (), {"open": lambda *a, **kw: response})(),
    )
    report = probe.request(probe.Configuration.load(environment()), "status")
    assert report["status"] == "passed"
    assert report["origin_timing"]["process"] == "first"
    assert report["platform_start_type"] == report["database_resume"] == "unobserved"
    assert "available" not in json.dumps(report)
    assert "synthetic-platform-token-only" not in json.dumps(report)


def test_cache_hits_do_not_replay_origin_timing_and_violate_private_no_store(monkeypatch):
    response = Response({"available": True}, cache="HIT")
    monkeypatch.setattr(
        probe.urllib.request,
        "build_opener",
        lambda *a: type("Opener", (), {"open": lambda *a, **kw: response})(),
    )
    report = probe.request(probe.Configuration.load(environment()), "status")
    assert report["status"] == "failed"
    assert report["reason"] == "private_cache_boundary_changed"
    assert probe.timing_header(response.headers["x-study-read-timing"], "HIT") is None


@pytest.mark.parametrize(
    "alteration",
    [
        {"STUDY_DEPLOYMENT_URL": "http://isolated-fixture.vercel.app"},
        {"STUDY_DEPLOYMENT_URL": "https://owner:password@isolated-fixture.vercel.app"},
        {"STUDY_DEPLOYMENT_URL": "https://foreign.vercel.app"},
        {"STUDY_DEPLOYMENT_URL": "https://isolated-fixture.vercel.app/?token=secret"},
        {"STUDY_EXPIRES_AT": "2026-11-14T00:00:00Z"},
    ],
)
def test_credential_destination_and_seven_day_scope_cannot_drift(alteration):
    with pytest.raises(probe.Refusal):
        probe.Configuration.load(environment() | alteration)


def test_http_failures_discard_response_bodies_and_credentials(monkeypatch):
    error = urllib.error.HTTPError(
        "https://private.invalid",
        401,
        "private message",
        Message(),
        io.BytesIO(b"sentinel-secret-provider-body"),
    )

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(
        probe.urllib.request, "build_opener", lambda *a: type("Opener", (), {"open": fail})()
    )
    report = probe.request(probe.Configuration.load(environment()), "status")
    assert report["http_status"] == 401
    assert "sentinel" not in json.dumps(report)
    assert "private.invalid" not in json.dumps(report)


def test_failed_first_read_is_not_retried_or_followed_by_a_warm_sample(monkeypatch):
    calls = []

    def failed(config, route):
        calls.append(route)
        return {"status": "failed"}

    monkeypatch.setattr(probe, "request", failed)
    monkeypatch.setattr(probe.time, "sleep", lambda *a: pytest.fail("Unexpected warm-pair wait"))
    config = probe.Configuration.load(environment())
    report = probe.observe(config, now=datetime(2026, 11, 6, tzinfo=UTC))
    assert calls == ["status"]
    assert report["status"] == "degraded"
    assert report["seven_day_evidence_proven"] is False
    with pytest.raises(probe.Refusal):
        probe.observe(config, now=config.expires_at + timedelta(seconds=1))


def test_signed_hour_cannot_be_spent_in_the_next_slot(monkeypatch):
    config = probe.Configuration.load(environment())
    stamp = "2026-11-06T00:17:00.000Z"
    next_hour = config.starts_at + timedelta(hours=1)
    calls = []

    def request(_config, route):
        calls.append(route)
        return {"status": "passed"}

    clock = iter([next_hour - timedelta(seconds=20), next_hour])

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return next(clock)

    monkeypatch.setattr(probe, "request", request)
    monkeypatch.setattr(probe.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(probe, "datetime", Clock)
    with pytest.raises(probe.Refusal, match="study_observation_slot_refused"):
        probe.observe(config, now=next_hour, scheduled_at=stamp)
    assert calls == []

    report = probe.observe(config, now=next_hour - timedelta(seconds=20), scheduled_at=stamp)
    assert calls == ["status"]
    assert report["scheduled_at"] == stamp
    assert len(report["samples"]) == 1
    assert report["status"] == "degraded"


def test_missing_origin_timing_cannot_be_counted_as_successful_measurement(monkeypatch):
    response = Response({"available": True})
    del response.headers["x-study-read-timing"]
    monkeypatch.setattr(
        probe.urllib.request,
        "build_opener",
        lambda *a: type("Opener", (), {"open": lambda *a, **kw: response})(),
    )
    report = probe.request(probe.Configuration.load(environment()), "status")
    assert report["status"] == "failed" and report["reason"] == "origin_timing_refused"


def test_minute_aligned_window_still_requires_exactly_seven_days():
    env = environment() | {
        "STUDY_STARTS_AT": "2026-11-06T08:15:00Z",
        "STUDY_EXPIRES_AT": "2026-11-13T08:15:00Z",
    }
    assert probe.Configuration.load(env).starts_at.minute == 15
    with pytest.raises(probe.Refusal):
        probe.Configuration.load(env | {"STUDY_EXPIRES_AT": "2026-11-13T08:16:00Z"})
