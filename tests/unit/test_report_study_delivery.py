import json
from datetime import UTC, datetime, timedelta

import pytest

from scripts import report_study_delivery as report

START = report.trial_start("2026-11-26T05:00:00.000Z")


def run(run_id, slot=0, *, attempt=1, conclusion="success"):
    return {
        "id": run_id,
        "event": "workflow_dispatch",
        "head_branch": "main",
        "head_repository": {"full_name": report.REPOSITORY},
        "head_sha": "a" * 40,
        "run_attempt": attempt,
        "status": "completed",
        "conclusion": conclusion,
        "created_at": (
            START + slot * report.HOUR + report.OFFSET + timedelta(seconds=2)
        ).isoformat(),
    }


def probe(item, *, conclusion="success"):
    return {
        "name": "probe",
        "id": item["id"] + 100,
        "run_id": item["id"],
        "conclusion": conclusion,
        "started_at": (report.utc(item["created_at"]) + timedelta(seconds=3)).isoformat(),
    }


def log(item, slot=0, **override):
    stamp = START + slot * report.HOUR + report.OFFSET
    receipt = {
        "scope": report.SCOPE,
        "run_id": item["id"],
        "starts_at": START.strftime(report.FORMAT),
        "scheduled_at": stamp.strftime(report.FORMAT),
        "observed_at": (stamp + timedelta(seconds=8)).isoformat(),
        "hourly_slot": slot,
        **report.COUNTERS,
        **override,
    }
    return (
        f"2026-11-26T05:17:08.0000000Z study290_external_delivery {json.dumps(receipt)}\n".encode()
    )


def test_report_reconciles_slots_without_guessing_failed_run_identity(monkeypatch):
    root = report.RUNS
    items = [
        run(1),
        run(2),
        run(3, 1, conclusion="failure"),
        run(4, 1, attempt=2),
        run(5, 2),
        run(6, 2),
    ]
    responses = {
        f"{root}/1/attempts/1/jobs?per_page=10": [probe(items[0])],
        f"{root}/2/attempts/1/jobs?per_page=10": [probe(items[1])],
        f"{root}/5/attempts/1/jobs?per_page=10": [
            {"name": "check-credentials", "conclusion": "success"},
            probe(items[4], conclusion="skipped"),
        ],
        f"{root}/6/attempts/1/jobs?per_page=10": [probe(items[5])],
    }

    def get(*args):
        if args[1] == "--method":
            assert "event=workflow_dispatch" in args and "branch=main" in args
            return json.dumps({"total_count": len(items), "workflow_runs": items}).encode()
        endpoint = args[1]
        if endpoint in responses:
            jobs = responses[endpoint]
            return json.dumps({"total_count": len(jobs), "jobs": jobs}).encode()
        if endpoint.endswith("/101/logs"):
            return log(items[0], optional_private="must never be retained")
        if endpoint.endswith("/102/logs"):
            return log(items[1])
        if endpoint.endswith("/106/logs"):
            return log(items[5], 2)
        pytest.fail(f"Unexpected lookup {endpoint}")

    monkeypatch.setattr(report, "gh", get)
    result = report.collect(START, START + timedelta(hours=2, minutes=20))
    assert result["counts"] == {"confirmed": 1, "duplicate": 1, "missing": 1, "pending": 21}
    assert result["setup_checks_excluded"] == 1
    assert result["slots"][0]["receipts"][0]["github_queue_seconds"] == 3
    assert result["slots"][2]["receipts"][0]["scheduled_to_github_seconds"] == 2
    assert result["slots"][2]["receipts"][0]["job_to_receipt_seconds"] == 3
    assert "must never be retained" not in json.dumps(result)
    assert result["unverified_runs"] == [
        {"run_id": 3, "reason": "run_without_success"},
        {"run_id": 4, "reason": "rerun_excluded"},
    ]


@pytest.mark.parametrize(
    "override",
    [
        {"application_requests": 1},
        {"measurement_pairs": True},
        {"run_id": 9000},
        {"hourly_slot": 24},
        {"observed_at": "2026-11-26T06:00:00+00:00"},
        {"starts_at": "2026-11-25T05:00:00.000Z"},
    ],
)
def test_receipt_refuses_wrong_scope_counts_identity_and_time(override):
    item = run(1)
    with pytest.raises(ValueError):
        report.parse_receipt(item, probe(item), log(item, **override), START)


def test_elapsed_window_reports_missing_instead_of_inventing_observations(monkeypatch):
    monkeypatch.setattr(report, "gh", lambda *_args: b'{"total_count":0,"workflow_runs":[]}')
    result = report.collect(START, datetime(2026, 11, 27, 5, tzinfo=UTC))
    assert result["status"] == "incomplete"
    assert result["counts"] == {"confirmed": 0, "duplicate": 0, "missing": 24, "pending": 0}
    assert result["measurement_pairs"] == 0


def test_truncated_actions_inventory_fails_instead_of_reporting_missing(monkeypatch):
    monkeypatch.setattr(report, "gh", lambda *_args: b'{"total_count":1,"workflow_runs":[]}')
    with pytest.raises(report.Refusal, match="run_inventory"):
        report.collect(START, START + timedelta(minutes=20))
