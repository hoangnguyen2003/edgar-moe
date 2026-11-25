import hmac
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from scripts.observe_external_study_delivery import receipt

START = "2026-11-25T05:00:00.000Z"
STAMP = "2026-11-25T05:17:00.000Z"
NOW = datetime(2026, 11, 25, 5, 18, tzinfo=UTC)


def environment():
    key = "a" * 64
    message = f"edgar-moe/study-external-delivery/v1\n{START}\n{STAMP}".encode()
    return {
        "GITHUB_RUN_ID": "42",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_RUN_ATTEMPT": "1",
        "STUDY_DELIVERY_STARTS_AT": START,
        "STUDY_DELIVERY_SCHEDULED_AT": STAMP,
        "STUDY_DELIVERY_SIGNING_KEY": key,
        "STUDY_DELIVERY_SIGNATURE": hmac.digest(key.encode(), message, "sha256").hex(),
    }


def test_signed_receipt_is_source_free():
    result = receipt(environment(), NOW)
    assert result["hourly_slot"] == 0 and result["run_id"] == 42
    assert result["application_requests"] == result["provider_requests"] == 0
    assert result["measurement_pairs"] == 0


@pytest.mark.parametrize(
    "override",
    [
        {"GITHUB_RUN_ATTEMPT": "2"},
        {"GITHUB_EVENT_NAME": "schedule"},
        {"STUDY_DELIVERY_SCHEDULED_AT": "2026-11-25T06:17:00.000Z"},
        {"STUDY_DELIVERY_STARTS_AT": "2026-11-24T05:00:00.000Z"},
        {"STUDY_DELIVERY_SIGNATURE": "b" * 64},
    ],
)
def test_wrong_dispatches_are_refused(override):
    with pytest.raises(ValueError, match="refused"):
        receipt(environment() | override, NOW)


def test_receiver_refuses_a_late_job():
    with pytest.raises(ValueError, match="window refused"):
        receipt(environment(), datetime(2026, 11, 25, 6, tzinfo=UTC))


def test_workflow_only_reads_source_and_receives_one_scoped_secret():
    workflow = yaml.safe_load(Path(".github/workflows/study-external-delivery.yml").read_text())
    assert set(workflow[True]) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    probe = workflow["jobs"]["probe"]
    assert probe["environment"] == "hosted-study-delivery"
    env = probe["steps"][1]["env"]
    assert [name for name, value in env.items() if "secrets." in value] == [
        "STUDY_DELIVERY_SIGNING_KEY"
    ]
