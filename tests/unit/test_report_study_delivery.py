import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from scripts import report_study_delivery as report

START = report.trial_start("2026-11-26T05:00:00.000Z")


def run(run_id, slot=0, *, attempt=1, conclusion="success"):
    return {
        "id": run_id,
        "event": "workflow_dispatch",
        "head_branch": "main",
        "head_repository": {"full_name": report.REPOSITORY},
        "head_sha": "a" * 40,
        "workflow_id": 789,
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
        run(6, 2, attempt=3, conclusion="failure"),
    ]
    originals = {
        f"{root}/4/attempts/1": run(4, 1, conclusion="failure"),
        f"{root}/6/attempts/1": run(6, 2),
    }
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
        if endpoint in originals:
            return json.dumps(originals[endpoint]).encode()
        if endpoint in responses:
            jobs = responses[endpoint]
            return json.dumps({"total_count": len(jobs), "jobs": jobs}).encode()
        if endpoint.endswith("/logs"):
            assert args[2:] == ("--allow-escape-sequences",)
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
    assert result["reruns_excluded"] == [
        {"run_id": 4, "latest_attempt": 2},
        {"run_id": 6, "latest_attempt": 3},
    ]
    assert result["unverified_runs"] == [
        {"run_id": 3, "reason": "run_without_success"},
        {"run_id": 4, "reason": "run_without_success"},
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


@pytest.mark.parametrize("minutes", [0, 9, 10, 30])
def test_closeout_gate_has_no_api_access_and_waits_for_grace(monkeypatch, capsys, minutes):
    class Clock(datetime):
        @classmethod
        def now(cls, tz):
            return START + timedelta(days=1, minutes=minutes)

    monkeypatch.setattr(report, "datetime", Clock)
    monkeypatch.setattr(report, "gh", lambda *_args: pytest.fail("unexpected API access"))
    monkeypatch.setattr(
        sys, "argv", ["report", "--starts-at", START.strftime(report.FORMAT), "--check-closeout"]
    )
    assert report.main() == 0
    assert capsys.readouterr().out == (
        f"starts_at={START.strftime(report.FORMAT)}\n" if minutes >= 10 else ""
    )


@pytest.mark.parametrize(
    ("created", "now", "ready", "delay"),
    [
        ("04:16:59", "04:17:10", False, []),
        ("04:17:00", "04:17:00", True, [3180]),
        ("04:17:22", "04:17:30", True, [3150]),
        ("04:17:22", "05:10:00", True, []),
        ("04:17:22", "04:17:21", False, []),
        ("05:00:00", "05:10:00", False, []),
    ],
)
def test_backup_waits_only_for_the_final_slot(monkeypatch, capsys, created, now, ready, delay):
    class Clock(datetime):
        @classmethod
        def now(cls, tz):
            return datetime.fromisoformat(f"2026-11-27T{now}Z")

    sleeps = []
    monkeypatch.setattr(report, "datetime", Clock)
    monkeypatch.setattr(report, "sleep", sleeps.append)
    monkeypatch.setattr(report, "gh", lambda *_args: pytest.fail("unexpected API access"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "report",
            "--starts-at",
            START.strftime(report.FORMAT),
            "--wait-for-closeout-after-receipt",
            f"2026-11-27T{created}Z",
        ],
    )
    assert report.main() == 0
    assert sleeps == delay
    assert capsys.readouterr().out == (
        f"starts_at={START.strftime(report.FORMAT)}\n" if ready else ""
    )


def test_receipt_triggered_closeout_targets_only_the_main_finalizer():
    path = Path(".github/workflows/trigger-study-delivery-closeout.yml")
    workflow = yaml.safe_load(path.read_text())
    assert workflow[True] == {
        "workflow_run": {
            "workflows": ["Hosted study external delivery preflight"],
            "types": ["completed"],
            "branches": ["main"],
        }
    }
    wait = workflow["jobs"]["wait-for-expiry"]
    dispatch = workflow["jobs"]["dispatch"]
    assert wait["permissions"] == {"contents": "read"}
    assert wait["steps"][0]["with"] == {
        "ref": "${{ github.sha }}",
        "persist-credentials": False,
    }
    assert "github.event.workflow_run.head_repository.full_name == github.repository" in wait["if"]
    assert "github.event.workflow_run.run_attempt == 1" in wait["if"]
    assert dispatch["needs"] == "wait-for-expiry"
    assert "outputs.starts_at == vars.ISSUE290_EXTERNAL_DELIVERY_STARTS_AT" in dispatch["if"]
    assert dispatch["permissions"] == {"actions": "write"}
    assert "workflow=finalize-study-delivery.yml" in dispatch["steps"][0]["run"]
    assert "secrets." not in path.read_text()


def test_closeout_workflow_keeps_reporting_and_shutdown_credentials_separate():
    workflow = yaml.safe_load(Path(".github/workflows/finalize-study-delivery.yml").read_text())
    manager = yaml.safe_load(Path(".github/workflows/manage-study-delivery.yml").read_text())
    reconcile, stop, disable = (
        workflow["jobs"][job] for job in ("reconcile", "stop-cron", "disable-finalizer")
    )
    assert workflow["concurrency"]["group"] in manager["concurrency"]["group"]
    assert reconcile["permissions"] == {"actions": "read", "contents": "read"}
    assert "secrets." not in json.dumps(reconcile)
    assert "needs.reconcile.outputs.starts_at != ''" in stop["if"]
    stop_env = stop["steps"][-1]["env"]
    assert stop_env["STUDY_DELIVERY_STAGE"] == "STOP_EXPIRED"
    assert stop_env["STUDY_DELIVERY_STARTS_AT"] == "${{ needs.reconcile.outputs.starts_at }}"
    assert {key for key, val in stop_env.items() if "secrets." in val} == {
        "STUDY_DELIVERY_CLOUDFLARE_ACCOUNT_ID",
        "STUDY_DELIVERY_CLOUDFLARE_TOKEN",
    }
    assert disable["permissions"] == {"actions": "write"}
    assert disable["needs"] == "stop-cron"
    assert "gh workflow disable finalize-study-delivery.yml" in disable["steps"][0]["run"]
