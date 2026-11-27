import hmac
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts import admit_study_observation as gate

START = datetime(2026, 12, 1, tzinfo=UTC)
END = START + timedelta(days=7)
NOW = START + timedelta(minutes=18)
STAMP = "2026-12-01T00:17:00.000Z"


@pytest.fixture
def environment(tmp_path):
    event_path = tmp_path / "event.json"
    env = {
        "GITHUB_REPOSITORY": gate.REPOSITORY,
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_RUN_ID": "42",
        "GITHUB_EVENT_PATH": str(event_path),
        "STUDY_POLICY_SHA256": "a" * 64,
        "STUDY_DEPLOYMENT_HOST_SHA256": "b" * 64,
        "STUDY_OBSERVATION_SIGNING_KEY": "c" * 64,
        "STUDY_CLAIM_R2_ACCOUNT_ID": "d" * 32,
        "STUDY_CLAIM_R2_ACCESS_KEY_ID": "e" * 32,
        "STUDY_CLAIM_R2_SECRET_ACCESS_KEY": "f" * 64,
        "PATH": "/usr/bin",
    }
    message = "\n".join(
        (
            gate.DOMAIN,
            "2026-12-01T00:00:00.000Z",
            "2026-12-08T00:00:00.000Z",
            "a" * 64,
            "b" * 64,
            STAMP,
        )
    ).encode()
    signature = hmac.digest(env["STUDY_OBSERVATION_SIGNING_KEY"].encode(), message, "sha256")
    event_path.write_text(
        json.dumps({"inputs": {"scheduled_at": STAMP, "signature": signature.hex()}})
    )
    return env


def test_conditional_claim_is_fixed_to_one_bucket_and_one_slot(monkeypatch, environment):
    claims = {}

    def put(args, *, env, capture_output, timeout):
        assert args[:3] == ["aws", "s3api", "put-object"]
        options = dict(zip(args[3::2], args[4::2], strict=True))
        assert options["--if-none-match"] == "*"
        assert options["--bucket"] == gate.CLAIM_BUCKET
        assert options["--key"].endswith(f"/{STAMP}.json")
        assert options["--endpoint-url"] == f"https://{'d' * 32}.r2.cloudflarestorage.com"
        assert "STUDY_OBSERVATION_SIGNING_KEY" not in env
        assert capture_output and timeout == 15 and env["AWS_MAX_ATTEMPTS"] == "1"
        key = options["--key"]
        if key in claims:
            return subprocess.CompletedProcess(
                args,
                254,
                stderr=b"An error occurred (PreconditionFailed) when calling the PutObject operation",
            )
        claims[key] = json.loads(Path(options["--body"]).read_text())
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(gate.subprocess, "run", put)
    assert gate.admit(environment, START, END, NOW) == STAMP
    assert gate.admit(environment | {"GITHUB_RUN_ID": "43"}, START, END, NOW) is None
    assert list(claims.values()) == [{"run_id": 42, "scheduled_at": STAMP}]


@pytest.mark.parametrize(
    ("override", "when"),
    [
        ({"GITHUB_RUN_ATTEMPT": "2"}, NOW),
        ({"GITHUB_EVENT_NAME": "schedule"}, NOW),
        ({"STUDY_POLICY_SHA256": "6" * 64}, NOW),
        ({"STUDY_DEPLOYMENT_HOST_SHA256": "7" * 64}, NOW),
        ({"STUDY_OBSERVATION_SIGNING_KEY": "8" * 64}, NOW),
        ({}, START + timedelta(minutes=16)),
        ({}, START + timedelta(hours=1)),
    ],
)
def test_invalid_provenance_or_slot_refuses_before_storage(
    monkeypatch, environment, override, when
):
    monkeypatch.setattr(
        gate.subprocess, "run", lambda *a, **k: pytest.fail("unexpected storage call")
    )
    with pytest.raises(gate.AdmissionError):
        gate.admit(environment | override, START, END, when)


def test_ambiguous_storage_error_refuses_and_does_not_retry(monkeypatch, environment):
    calls = []

    def put(args, **_kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 254, stderr=b"private provider error")

    monkeypatch.setattr(gate.subprocess, "run", put)
    with pytest.raises(gate.AdmissionError, match="^study_slot_claim_failed$"):
        gate.admit(environment, START, END, NOW)
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("outcomes", "error"),
    [
        ([True, False], None),
        ([True, True], "study_claim_store_duplicate_accepted"),
        ([False], "study_claim_store_setup_key_exists"),
    ],
)
def test_live_preflight_uses_a_separate_key_and_requires_duplicate_rejection(
    monkeypatch, environment, outcomes, error
):
    calls = []
    results = iter(outcomes)

    def put(_env, key, contents):
        calls.append((key, contents))
        return next(results)

    monkeypatch.setattr(gate, "put_once", put)
    env = {key: value for key, value in environment.items() if key.startswith("GITHUB_")}
    if error:
        with pytest.raises(gate.AdmissionError, match=error):
            gate.check_store(env)
    else:
        gate.check_store(env)
    assert calls == [
        ("issue290/setup/42.json", {"scope": "study_claim_store_setup", "run_id": 42})
    ] * len(outcomes)
