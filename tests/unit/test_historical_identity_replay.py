from __future__ import annotations

import os
from copy import deepcopy

import httpx
import pytest

from edgar_moe.data.historical_identity import IdentityEvidenceError
from edgar_moe.data.screen_inputs import canonical_bytes
from scripts import verify_historical_security_identity as verifier
from scripts.capture_historical_security_identity import digest
from scripts.verify_historical_security_identity import ReplayCache, replay

URL = "https://data.sec.gov/submissions/CIK0000000001.json"


@pytest.fixture
def private_fd(tmp_path):
    tmp_path.chmod(0o700)
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        yield fd
    finally:
        os.close(fd)


def failure():
    return {
        "url": URL,
        "source_url_sha256": digest(URL.encode()),
        "http_status": 503,
        "timeout": False,
        "observed_at": "2026-11-07T00:00:00Z",
        "response_body_retained": False,
        "automatically_retried": False,
    }


def write_receipt(path, value):
    body = canonical_bytes(value) + b"\n"
    file = path / ("unavailable-" + digest(body) + ".json")
    file.write_bytes(body)
    file.chmod(0o600)
    return file


def test_failure_replay_retains_unresolved_status_without_creating_http_client(
    private_fd, tmp_path, monkeypatch
):
    def forbidden(*args, **kwargs):
        pytest.fail("offline replay created an HTTP client")

    monkeypatch.setattr(httpx, "Client", forbidden)
    item = failure()
    write_receipt(tmp_path, item)
    cache = ReplayCache(private_fd, {digest(URL.encode()): item})
    with pytest.raises(IdentityEvidenceError, match="temporarily_unavailable"):
        cache.get(URL)
    with pytest.raises(IdentityEvidenceError, match="deferred_this_run"):
        cache.get(URL)
    assert cache.requests == 0
    assert cache.used == {}
    assert len(cache.deferred) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("http_status", 200),
        ("http_status", True),
        ("http_status", "503"),
        ("timeout", True),
        ("response_body_retained", True),
        ("automatically_retried", True),
        ("url", "https://example.com"),
        ("source_url_sha256", "0" * 64),
    ],
)
def test_failure_records_cannot_invent_success_or_another_source(
    private_fd, tmp_path, field, value
):
    item = failure()
    item[field] = value
    write_receipt(tmp_path, item)
    with pytest.raises(IdentityEvidenceError, match="invalid_recorded"):
        ReplayCache(private_fd, {digest(URL.encode()): item}).get(URL)


def test_failure_receipt_bytes_must_match_the_captured_result(private_fd, tmp_path):
    item = failure()
    file = write_receipt(tmp_path, item)
    file.write_bytes(b"{}")
    with pytest.raises(IdentityEvidenceError, match="receipt_changed"):
        ReplayCache(private_fd, {digest(URL.encode()): item}).get(URL)


def test_missing_source_is_not_silently_reclassified_as_a_provider_failure(private_fd):
    with pytest.raises(IdentityEvidenceError, match="uncaptured"):
        ReplayCache(private_fd, {}).get(URL)


@pytest.mark.parametrize("name", ["../result.json", "result-not-a-hash.json", "binding.json"])
def test_result_name_refuses_paths_and_unbound_files_before_inputs(tmp_path, name):
    with pytest.raises(IdentityEvidenceError, match="filename"):
        replay(tmp_path, name, tmp_path / "missing-source", tmp_path / "missing-checkpoint")


def test_timeout_replay_requires_an_explicit_timeout_flag(private_fd, tmp_path):
    item = deepcopy(failure())
    item.update(http_status=None, timeout=True)
    write_receipt(tmp_path, item)
    with pytest.raises(IdentityEvidenceError, match="temporarily_unavailable"):
        ReplayCache(private_fd, {digest(URL.encode()): item}).get(URL)


@pytest.fixture
def synthetic_result(tmp_path, monkeypatch):
    source = tmp_path / "source"
    output = tmp_path / "capture"
    source.mkdir(mode=0o700)
    output.mkdir(mode=0o700)
    monkeypatch.setattr(verifier, "ARTIFACT_ROOT", tmp_path)
    member = {
        "snapshot_member_key": "synthetic-member",
        "cik": "0000000001",
        "symbol_at_snapshot": "OLD",
    }

    def write(path, payload):
        path.write_bytes(payload)
        path.chmod(0o600)

    write(source / "candidate-roster.json", canonical_bytes({"members": [member]}))
    monkeypatch.setattr(
        verifier, "load_policy", lambda: {"revision_timestamp": "2022-12-28T00:00:00Z"}
    )
    monkeypatch.setattr(
        verifier, "verify_capture", lambda *args, **kwargs: {"roster_sha256": "a" * 64}
    )
    monkeypatch.setattr(verifier, "RetainedCheckpoint", lambda _: object())

    def extract(member, cache, retained, cutoff):
        return {
            "snapshot_member_key": member["snapshot_member_key"],
            "status": "anchored",
            "document_sha256": digest(cache.get(URL)),
        }

    monkeypatch.setattr(verifier, "collect_member", extract)
    body = b"synthetic source"
    key = digest(URL.encode())
    meta = {
        "url": URL,
        "bytes": len(body),
        "sha256": digest(body),
        "retrieved_at": "2026-11-07T00:00:00Z",
    }
    write(output / (key + ".body"), body)
    write(output / (key + ".json"), canonical_bytes(meta))
    binding = {
        "source_roster_sha256": "a" * 64,
        "retained_manifest_sha256": verifier.RETAINED_MANIFEST_SHA256,
        "cutoff": "2022-12-28T00:00:00Z",
        "members": [member],
        "source_count": 1,
    }
    write(output / "binding.json", canonical_bytes(binding) + b"\n")
    report = {
        "issue_complete": False,
        "historical_market_continuity_verified": False,
        "checkpoint_dataset_binding_verified": False,
        "current_asset_directory_used": False,
        "source_roster_sha256": "a" * 64,
        "statuses": {"anchored": 1},
        "processed": 1,
        "source_count": 1,
        "identity_algorithm_sha256": "b" * 64,
        "collector_algorithm_sha256": "c" * 64,
    }
    value = {
        "binding": binding,
        "results": [
            {
                "snapshot_member_key": member["snapshot_member_key"],
                "status": "anchored",
                "document_sha256": digest(body),
            }
        ],
        "live_sources": {key: meta},
        "report": report,
    }

    def save(value):
        raw = canonical_bytes(value) + b"\n"
        name = "result-" + digest(raw) + ".json"
        write(output / name, raw)
        return name

    return output, source, value, save


def test_full_replay_recomputes_sources_without_network(synthetic_result, monkeypatch):
    output, source, value, save = synthetic_result
    monkeypatch.setattr(httpx, "Client", lambda *args, **kwargs: pytest.fail("network client"))
    report = replay(output, save(value), source, source)
    assert report["provider_requests"] == 0
    assert report["issue_complete"] is False
    assert report["processed"] == 1


@pytest.mark.parametrize(
    "field",
    [
        "issue_complete",
        "historical_market_continuity_verified",
        "checkpoint_dataset_binding_verified",
        "current_asset_directory_used",
    ],
)
def test_rehashed_result_cannot_promote_a_completion_flag(synthetic_result, field):
    output, source, value, save = synthetic_result
    value["report"][field] = True
    with pytest.raises(IdentityEvidenceError, match="report"):
        replay(output, save(value), source, source)


def test_rehashed_result_cannot_change_selected_member_or_identity(synthetic_result):
    output, source, value, save = synthetic_result
    value["results"][0]["document_sha256"] = "0" * 64
    with pytest.raises(IdentityEvidenceError, match="replay_mismatch"):
        replay(output, save(value), source, source)
    value["binding"]["members"] = []
    with pytest.raises(IdentityEvidenceError, match="binding_mismatch"):
        replay(output, save(value), source, source)
