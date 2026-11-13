from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path("scripts").resolve()))
spec = importlib.util.spec_from_file_location("scheduler_stop", "scripts/stop_private_scheduler.py")
assert spec and spec.loader
stop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stop)
NOW = datetime(2026, 11, 13, tzinfo=UTC)
CONTEXT = {"GITHUB_REF": "refs/heads/main", "GITHUB_REPOSITORY": stop.REPOSITORY}


def receipt():
    return {
        "workflow_run_id": 42,
        "run_url": f"https://api.github.com/{stop.PREFIX}/runs/42",
        "html_url": f"https://github.com/{stop.REPOSITORY}/actions/runs/42",
    }


def run(**changes):
    return {
        "id": 42,
        "head_branch": "main",
        "event": "workflow_dispatch",
        "run_attempt": 1,
        "head_repository": {"full_name": stop.REPOSITORY},
        "head_sha": "a" * 40,
        "path": ".github/workflows/" + stop.WORKFLOW,
        "status": "completed",
        "conclusion": "success",
        **changes,
    }


def call(values, **kwargs):
    requests, waits = [], []
    iterator = iter(values)

    def request(path, body=None):
        requests.append((path, body))
        value = next(iterator)
        if isinstance(value, Exception):
            raise value
        return value

    report = stop.stop_scope(
        now=NOW, context=CONTEXT, request=request, pause=waits.append, **kwargs
    )
    assert report["forecast_requests"] == 0
    assert report["cloudflare_credentials_in_coordinator"] is False
    return report, requests, waits


def test_queue_acknowledgement_is_not_confused_with_verified_stop():
    report, requests, waits = call([receipt(), run(status="queued", conclusion=None), run()])
    assert report["status"] == "passed" and report["stop_verified"]
    assert requests[0] == (
        f"{stop.PREFIX}/workflows/{stop.WORKFLOW}/dispatches",
        {"ref": "main", "inputs": {"stage": "STOP"}},
    )
    assert requests[1:] == [(f"{stop.PREFIX}/runs/42", None)] * 2
    assert report["dispatch_requests"] == 1 and report["metadata_requests"] == 2
    assert waits == [5]


@pytest.mark.parametrize(
    "conclusion", ["failure", "cancelled", "timed_out", "skipped", "private-value"]
)
def test_failed_child_run_is_not_reported_as_success(conclusion):
    report, requests, _ = call([receipt(), run(conclusion=conclusion)])
    assert report["reason"] == "stop_run_failed"
    assert report["stop_verified"] is False and len(requests) == 2
    assert "private-value" not in json.dumps(report)


@pytest.mark.parametrize(
    "change",
    [
        {"id": 43},
        {"head_branch": "other"},
        {"event": "schedule"},
        {"run_attempt": 2},
        {"run_attempt": True},
        {"head_repository": {"full_name": "other/repo"}},
        {"path": ".github/workflows/other.yml"},
        {"head_sha": "private-value"},
    ],
)
def test_a_different_run_or_rerun_cannot_acknowledge_the_dispatched_stop(change):
    report, requests, _ = call([receipt(), run(**change)])
    assert report["reason"] == "stop_run_provenance_refused"
    assert not report["stop_verified"] and len(requests) == 2


def test_foreign_context_and_premature_stop_refuse_before_any_dispatch():
    for context, now in [
        (CONTEXT | {"GITHUB_REF": "refs/heads/other"}, NOW),
        (CONTEXT | {"GITHUB_REPOSITORY": "other/repo"}, NOW),
        (CONTEXT, datetime(2026, 11, 10, tzinfo=UTC)),
    ]:
        report = stop.stop_scope(
            now=now, context=context, request=lambda *a: pytest.fail("No dispatch")
        )
        assert report["reason"] == "cleanup_context_or_date_refused"
        assert report["dispatch_requests"] == 0


@pytest.mark.parametrize(
    "replacement",
    [{}, receipt() | {"workflow_run_id": True}, receipt() | {"run_url": "https://private.invalid"}],
)
def test_dispatch_without_a_bound_receipt_is_not_repeated_or_matched_by_latest_run(replacement):
    report, requests, _ = call([replacement])
    assert report["reason"] == "stop_dispatch_receipt_refused" and not report["stop_verified"]
    assert len(requests) == 1


def test_polling_has_both_a_request_cap_and_deadline_without_repeat_dispatch():
    report, requests, waits = call(
        [receipt()] + [run(status="in_progress", conclusion=None)] * 36, clock=lambda: 0
    )
    assert report["reason"] == "stop_run_timeout" and not report["stop_verified"]
    assert len(requests) == 37 and len(waits) == 36
    ticks = iter([0, 180])
    report, requests, _ = call([receipt()], clock=lambda: next(ticks))
    assert report["reason"] == "stop_run_timeout" and len(requests) == 1


def test_actions_api_version_requests_a_single_acknowledged_dispatch_and_withholds_error_bodies(
    monkeypatch,
):
    commands = []

    def successful(command, **kwargs):
        commands.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, json.dumps(receipt()).encode(), b"")

    monkeypatch.setattr(stop.subprocess, "run", successful)
    assert (
        stop.api(
            f"{stop.PREFIX}/workflows/{stop.WORKFLOW}/dispatches",
            {"ref": "main", "inputs": {"stage": "STOP"}},
        )
        == receipt()
    )
    command, kwargs = commands[0]
    assert "X-GitHub-Api-Version: 2026-03-10" in command
    assert command[2:4] == ["--hostname", "github.com"]
    assert "--input" in command and kwargs["timeout"] == 15
    assert json.loads(kwargs["input"])["inputs"] == {"stage": "STOP"}
    monkeypatch.setattr(
        stop.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess([], 1, b"private-body", b"private-token"),
    )
    with pytest.raises(stop.Refusal, match="^actions_request_refused$"):
        stop.api("fixed/path")
