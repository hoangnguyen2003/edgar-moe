from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import sys
import urllib.error
from email.message import Message

import pytest

SPEC = importlib.util.spec_from_file_location(
    "portfolio_study_trace", "scripts/observe_private_read_study.py"
)
assert SPEC and SPEC.loader
probe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = probe
SPEC.loader.exec_module(probe)


def target():
    return probe.ProviderTrace(
        "sentinel-provider-token",
        "synthetic-user-id",
        "team_synthetic290",
        "prj_synthetic290",
        "dpl_synthetic290",
    )


REQUEST = "fixture-1793935800000-synthetic"


def trace(*, cold=True):
    t = target()
    resource = {
        "vercel.project_id": t.project_id,
        "vercel.owner_id": t.team_id,
        "vercel.deployment_id": t.deployment_id,
    }

    def span(identity, parent, name, service):
        return {
            "spanId": identity,
            "parentSpanId": parent,
            "name": name,
            "timestamp": "2026-11-06T03:27:15.813Z",
            "deploymentId": t.deployment_id,
            "durationMs": 2.5,
            "serviceName": service,
            "resourceAttributes": resource | {"service.name": service},
            "attributes": {"Authorization": "sentinel-private-header"},
        }

    root = span("a" * 16, "", "GET /v1/status", "vercel.edge-network")
    root.update(isRoot=True, requestId=REQUEST)
    invoke = span("b" * 16, root["spanId"], "Invoke Function", "vercel.serverless-runtime")
    spans = [root, invoke]
    if cold:
        spans += [
            span(c * 16, invoke["spanId"], name, "vercel.serverless-runtime")
            for c, name in zip("cde", ("Start VM", "Spawn Python", "Init User Code"), strict=True)
        ]
    return {
        "rootSpanId": root["spanId"],
        "spans": spans,
        "meta": {"partial": False, "spanCount": len(spans)},
    }


@pytest.mark.parametrize("cold", [True, False])
def test_provider_startup_spans_distinguish_cold_and_warm_without_leaking_payload(cold):
    report = probe.summarize_trace(trace(cold=cold), target(), REQUEST, "status")
    assert report["classification"] == ("cold" if cold else "warm")
    assert report["vm_start_observed"] is cold
    assert report["request_sha256"] == hashlib.sha256(REQUEST.encode()).hexdigest()
    raw = json.dumps(report)
    assert "sentinel" not in raw and REQUEST not in raw and target().project_id not in raw
    assert "sentinel" not in repr(target())


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d["meta"].update(partial=True),
        lambda d: d["meta"].pop("partial"),
        lambda d: d["meta"].update(spanCount=200),
        lambda d: d["spans"][0].update(requestId="different-request"),
        lambda d: d["spans"][0].update(name="GET /v1/performance"),
        lambda d: d["spans"][0]["resourceAttributes"].update(
            **{"vercel.project_id": "prj_foreign"}
        ),
        lambda d: d["spans"][1].update(deploymentId="dpl_foreign"),
        lambda d: d["spans"][1].update(durationMs=float("nan")),
        lambda d: d["spans"][1].update(parentSpanId="b" * 16),
        lambda d: d["spans"][1].update(parentSpanId="f" * 16),
        lambda d: d["spans"][2].update(parentSpanId="a" * 16),
        lambda d: d["spans"].append(copy.deepcopy(d["spans"][2])),
    ],
)
def test_incomplete_mixed_target_cyclic_and_ambiguous_traces_cannot_become_warm(mutation):
    document = trace()
    mutation(document)
    with pytest.raises(probe.Refusal):
        probe.summarize_trace(document, target(), REQUEST, "status")


def test_application_span_named_like_startup_does_not_forge_a_provider_cold_start():
    document = trace(cold=False)
    fake = copy.deepcopy(trace()["spans"][2])
    fake["serviceName"] = "application"
    fake["resourceAttributes"]["service.name"] = "application"
    document["spans"].append(fake)
    document["meta"]["spanCount"] += 1
    report = probe.summarize_trace(document, target(), REQUEST, "status")
    assert report["classification"] == "warm"


def test_ingestion_retry_only_repeats_metadata_get_and_never_application_read(monkeypatch):
    calls, waits = [], []

    def api(self, url, body=None):
        calls.append((url, body))
        if len(calls) < 3:
            raise probe.Refusal("trace_not_ready")
        return trace()

    monkeypatch.setattr(probe.ProviderTrace, "api", api)
    monkeypatch.setattr(probe.time, "sleep", waits.append)
    result = target().capture(REQUEST, "status")
    assert result["classification"] == "cold" and waits == [10, 10]
    assert len(calls) == 3 and all(body is None for _, body in calls)
    assert all(url.startswith("https://vercel.com/api/observability-api/") for url, _ in calls)


def test_permission_error_is_not_retried_or_printed(monkeypatch):
    calls = []

    def api(self, url, body=None):
        calls.append(url)
        raise probe.Refusal("trace_api_refused")

    monkeypatch.setattr(probe.ProviderTrace, "api", api)
    monkeypatch.setattr(probe.time, "sleep", lambda *_: pytest.fail("Unexpected retry"))
    with pytest.raises(probe.Refusal, match="trace_api_refused"):
        target().capture(REQUEST, "status")
    assert len(calls) == 1


def test_redirected_and_large_provider_bodies_cannot_escape(monkeypatch):
    error = urllib.error.HTTPError(
        "https://private.invalid",
        302,
        "sentinel-private-message",
        Message(),
        io.BytesIO(b"sentinel-private-body"),
    )

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(
        probe.urllib.request, "build_opener", lambda *a: type("Opener", (), {"open": fail})()
    )
    with pytest.raises(probe.Refusal) as caught:
        target().api("https://api.vercel.com/v1/projects/traces/session")
    assert str(caught.value) == "trace_api_refused"
    with pytest.raises(probe.Refusal, match="trace_destination_refused"):
        target().api("https://foreign.invalid/")


def test_trace_session_failure_prevents_any_application_request(monkeypatch):
    from datetime import UTC, datetime, timedelta

    def refuse(*args):
        raise probe.Refusal("trace_api_refused")

    monkeypatch.setattr(probe.ProviderTrace, "session_cookie", refuse)
    monkeypatch.setattr(probe, "request", lambda *a, **kw: pytest.fail("Unexpected HTTP read"))
    start = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    config = probe.Configuration(
        "https://fixture.vercel.app",
        "b" * 64,
        "bypass",
        "a" * 64,
        "b" * 64,
        start,
        start + timedelta(days=7),
    )
    with pytest.raises(probe.Refusal, match="trace_api_refused"):
        probe.observe(config, trace=target())


def test_failed_trace_retains_reads_but_discards_private_request_ids(monkeypatch):
    from datetime import UTC, datetime, timedelta

    calls = []

    def request(config, route, *, trace_cookie=None):
        calls.append((route, trace_cookie))
        return {"status": "passed", "_provider_request_id": REQUEST}

    def capture(self, request_id, route):
        assert request_id == REQUEST
        raise probe.Refusal("trace_not_ready")

    monkeypatch.setattr(probe, "request", request)
    monkeypatch.setattr(probe.time, "sleep", lambda *a: None)
    monkeypatch.setattr(probe.ProviderTrace, "session_cookie", lambda *a: "sentinel-cookie")
    monkeypatch.setattr(probe.ProviderTrace, "capture", capture)
    start = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    config = probe.Configuration(
        "https://fixture.vercel.app",
        "b" * 64,
        "bypass",
        "a" * 64,
        "b" * 64,
        start,
        start + timedelta(days=7),
    )
    report = probe.observe(config, trace=target())
    assert len(calls) == 2 and report["status"] == "degraded"
    assert report["automatic_retries"] == 0
    assert all(s["provider_trace"]["status"] == "refused" for s in report["samples"])
    assert REQUEST not in json.dumps(report) and "sentinel" not in json.dumps(report)


def test_unknown_provider_runtime_span_does_not_silently_become_warm():
    document = trace(cold=False)
    unknown = copy.deepcopy(trace()["spans"][2])
    unknown["name"] = "A New Initialization Phase"
    document["spans"].append(unknown)
    document["meta"]["spanCount"] += 1
    with pytest.raises(probe.Refusal, match="trace_runtime_schema_changed"):
        probe.summarize_trace(document, target(), REQUEST, "status")


@pytest.mark.parametrize("timestamp", ["2026-11-06T03:20:00Z", "2026-11-06T03:28:00Z"])
def test_historical_prewarming_or_outside_startup_is_not_called_request_cold(timestamp):
    document = trace()
    document["spans"][2]["timestamp"] = timestamp
    with pytest.raises(probe.Refusal, match="trace_startup_outside_request"):
        probe.summarize_trace(document, target(), REQUEST, "status")
