import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

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
        "GITHUB_SHA": "d" * 40,
        "GITHUB_EVENT_PATH": str(event_path),
        "GH_TOKEN": "job-token",
        "STUDY_POLICY_SHA256": "a" * 64,
        "STUDY_DEPLOYMENT_HOST_SHA256": "b" * 64,
        "STUDY_OBSERVATION_SIGNING_KEY": "c" * 64,
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


@pytest.fixture
def ledger(monkeypatch):
    claims: dict[str, str] = {}
    calls: list[tuple[str, str]] = []
    has_branch = False

    def request(env, method, path, payload=None):
        nonlocal has_branch
        assert env["GH_TOKEN"] == "job-token"
        calls.append((method, path))
        if path == "git/ref/heads/ops/study-observation-claims" and method == "GET":
            return (200 if has_branch else 404), b""
        if path == "git/refs" and method == "POST":
            assert payload == {"ref": gate.CLAIM_REF, "sha": "d" * 40}
            has_branch = True
            return 201, b""
        resource = urlsplit(path)
        assert resource.path.startswith("contents/study-observation-claims/")
        if method == "PUT":
            assert set(payload) == {"branch", "message", "content"}
            assert payload["branch"] == gate.CLAIM_BRANCH
            if path in claims:
                return 422, b""
            claims[path] = payload["content"]
            return 201, b""
        assert method == "GET"
        assert parse_qs(resource.query) == {"ref": [gate.CLAIM_BRANCH]}
        body = {
            "type": "file",
            "encoding": "base64",
            "path": resource.path.removeprefix("contents/"),
            "content": claims[resource.path],
        }
        return 200, json.dumps(body).encode()

    monkeypatch.setattr(gate, "github_request", request)
    return claims, calls


def test_claim_is_fixed_to_one_branch_and_one_slot(environment, ledger):
    claims, calls = ledger
    assert gate.admit(environment, START, END, NOW) == STAMP
    assert gate.admit(environment | {"GITHUB_RUN_ID": "43"}, START, END, NOW) is None
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
    digest = hashlib.sha256(message).hexdigest()
    path = f"contents/study-observation-claims/{digest}.json"
    assert list(claims) == [path]
    assert json.loads(base64.b64decode(claims[path])) == {"run_id": 42, "scheduled_at": STAMP}
    assert calls[:2] == [("PUT", path), ("PUT", path)]
    assert len(calls) == 3


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
        gate, "github_request", lambda *a, **k: pytest.fail("unexpected storage call")
    )
    with pytest.raises(gate.AdmissionError):
        gate.admit(environment | override, START, END, when)


@pytest.mark.parametrize("status", [200, 302, 401, 403, 404, 500])
def test_ambiguous_storage_error_refuses_and_does_not_retry(monkeypatch, environment, status):
    calls = []

    def put(*args):
        calls.append(args)
        return status, b"private provider error"

    monkeypatch.setattr(gate, "github_request", put)
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
def test_preflight_requires_duplicate_rejection(monkeypatch, environment, outcomes, error):
    calls = []
    results = iter(outcomes)

    def put(_env, key, contents):
        calls.append((key, contents))
        return next(results)

    monkeypatch.setattr(gate, "put_once", put)
    monkeypatch.setattr(gate, "github_request", lambda *_: (200, b""))
    first = {"scope": "study_claim_store_setup", "run_id": 42}
    monkeypatch.setattr(gate, "read_claim", lambda *_: json.dumps(first).encode())
    env = {key: value for key, value in environment.items() if key.startswith("GITHUB_")}
    if error:
        with pytest.raises(gate.AdmissionError, match=error):
            gate.check_store(env)
    else:
        gate.check_store(env)
    key = hashlib.sha256(b"issue290/setup/42").hexdigest()
    assert calls == [(key, first), (key, first | {"scope": "duplicate_probe"})][: len(outcomes)]


def test_live_preflight_creates_branch_once_and_preserves_original_claim(environment, ledger):
    claims, calls = ledger
    gate.check_store(environment)
    gate.check_store(environment | {"GITHUB_RUN_ID": "43"})
    assert len(claims) == 2
    assert calls.count(("POST", "git/refs")) == 1
    assert [json.loads(base64.b64decode(value)) for value in claims.values()] == [
        {"scope": "study_claim_store_setup", "run_id": 42},
        {"scope": "study_claim_store_setup", "run_id": 43},
    ]


def test_preflight_refuses_changed_claim_on_readback(monkeypatch, environment, ledger):
    monkeypatch.setattr(gate, "read_claim", lambda *_: b'{"scope":"duplicate_probe"}')
    with pytest.raises(gate.AdmissionError, match="study_claim_store_readback_mismatch"):
        gate.check_store(environment)
