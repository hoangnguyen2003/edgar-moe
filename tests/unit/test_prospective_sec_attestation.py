"""Offline tests for the external timestamp boundary; GitHub transport is mocked."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from scripts import verify_prospective_sec_attestation as verifier
from scripts.capture_prospective_sec_index import (
    build_commitment,
    load_policy,
    save_capture,
    verify_capture,
)
from scripts.verify_prospective_sec_attestation import (
    AttestationError,
    fetch_comment,
    validate_comment,
)

COMMENT_ID = 42
OBSERVED = datetime(2026, 10, 11, tzinfo=UTC)
INDEX_BYTES = (
    b"CIK|Company Name|Form Type|Date Filed|File Name\n"
    b"100|Synthetic|10-K|2026-09-01|edgar/data/100/0000000100-26-000001.txt\n"
)


def _fixture() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    policy = load_policy()
    policy["minimum_eligible_rows"] = 1
    commitment = build_commitment(
        INDEX_BYTES, policy, captured_at=datetime(2026, 10, 10, microsecond=500, tzinfo=UTC)
    )
    target = policy["github_attestation"]
    issue_path = f"{target['repository']}/issues/{target['issue_number']}"
    comment = {
        "id": COMMENT_ID,
        "issue_url": f"https://api.github.com/repos/{issue_path}",
        "html_url": f"https://github.com/{issue_path}#issuecomment-{COMMENT_ID}",
        "user": {"id": target["author_user_id"]},
        "created_at": "2026-10-10T00:00:00Z",
        "updated_at": "2026-10-10T00:00:00Z",
        "body": json.dumps(commitment, indent=2),
    }
    return policy, commitment, comment


def test_comment_binds_verified_hashes_with_github_second_precision() -> None:
    policy, commitment, comment = _fixture()
    report = validate_comment(
        comment, commitment, policy, comment_id=COMMENT_ID, observed_at=OBSERVED
    )
    assert report["status"] == "github_commitment_observed_before_cutoff"
    assert report["index_sha256"] == commitment["index_sha256"]
    assert report["filer_roster_sha256"] == commitment["filer_roster_sha256"]
    assert report["comment_created_at"] == "2026-10-10T00:00:00+00:00"
    assert "body" not in report and "user" not in report
    assert "Synthetic" not in json.dumps(report)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", 43),
        ("id", True),
        ("issue_url", "https://api.github.com/repos/other/repo/issues/292"),
        ("html_url", "https://github.com/other/repo/issues/292#issuecomment-42"),
        ("user", {"id": 123}),
        ("updated_at", "2026-10-10T00:00:01Z"),
        ("created_at", "2026-10-10T00:00:00"),
        ("body", "not JSON"),
        ("body", "{}"),
    ],
)
def test_rejects_wrong_identity_edit_or_content(field: str, value: Any) -> None:
    policy, commitment, comment = _fixture()
    comment[field] = value
    with pytest.raises(AttestationError):
        validate_comment(comment, commitment, policy, comment_id=COMMENT_ID, observed_at=OBSERVED)


@pytest.mark.parametrize("posted", ["2026-10-08T23:59:59Z", "2026-10-16T00:00:00Z"])
def test_rejects_posts_outside_the_window(posted: str) -> None:
    policy, commitment, comment = _fixture()
    comment["created_at"] = comment["updated_at"] = posted
    with pytest.raises(AttestationError, match="predeclared window"):
        validate_comment(
            comment,
            commitment,
            policy,
            comment_id=COMMENT_ID,
            observed_at=datetime(2026, 10, 20, tzinfo=UTC),
        )


def test_rejects_future_observations_and_comments_predating_capture() -> None:
    policy, commitment, comment = _fixture()
    with pytest.raises(AttestationError, match="observation time"):
        validate_comment(
            comment,
            commitment,
            policy,
            comment_id=COMMENT_ID,
            observed_at=datetime(2026, 10, 9, tzinfo=UTC),
        )
    commitment["captured_at"] = "2026-10-10T00:00:01+00:00"
    comment["body"] = json.dumps(commitment)
    with pytest.raises(AttestationError, match="predates"):
        validate_comment(comment, commitment, policy, comment_id=COMMENT_ID, observed_at=OBSERVED)


def test_rejects_tampered_policy_ambiguous_json_and_numeric_type_substitution() -> None:
    policy, commitment, comment = _fixture()
    changed = deepcopy(policy)
    changed["minimum_eligible_rows"] += 1
    with pytest.raises(AttestationError, match="bound to"):
        validate_comment(comment, commitment, changed, comment_id=COMMENT_ID, observed_at=OBSERVED)
    comment["body"] = '{"index_sha256":"other",' + json.dumps(commitment)[1:]
    with pytest.raises(AttestationError, match="duplicate"):
        validate_comment(comment, commitment, policy, comment_id=COMMENT_ID, observed_at=OBSERVED)
    altered = {**commitment, "eligible_filer_ciks": True}
    comment["body"] = json.dumps(altered)
    with pytest.raises(AttestationError, match="does not match"):
        validate_comment(comment, commitment, policy, comment_id=COMMENT_ID, observed_at=OBSERVED)


@respx.mock
def test_fetch_is_one_anonymous_request_and_does_not_follow_redirects() -> None:
    url = "https://api.github.com/repos/example/research/issues/comments/42"
    route = respx.get(url).mock(return_value=httpx.Response(200, json={"id": 42}))
    assert fetch_comment("example/research", 42) == {"id": 42}
    assert route.call_count == 1
    assert "authorization" not in route.calls[0].request.headers
    route.mock(return_value=httpx.Response(302, headers={"Location": "https://other.invalid/"}))
    with pytest.raises(httpx.HTTPStatusError):
        fetch_comment("example/research", 42)
    assert len(respx.calls) == 2


@respx.mock
def test_response_limit_rejects_oversized_github_content(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verifier, "_MAX_RESPONSE_BYTES", 32)
    respx.get("https://api.github.com/repos/example/research/issues/comments/42").mock(
        return_value=httpx.Response(200, content=b"x" * 33)
    )
    with pytest.raises(AttestationError, match="byte bound"):
        fetch_comment("example/research", 42)


@pytest.mark.parametrize("live", [False, True])
def test_cli_rejects_missing_live_permission_or_capture_before_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    live: bool,
) -> None:
    def forbidden_fetch(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("network must not be called")

    monkeypatch.setattr(verifier, "fetch_comment", forbidden_fetch)
    monkeypatch.setattr(verifier, "_ARTIFACT_ROOT", tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify",
            *(["--live"] if live else []),
            "--capture-dir",
            str(tmp_path / "missing"),
            "--comment-id",
            "42",
            "--output",
            str(tmp_path / "report.json"),
        ],
    )
    assert verifier.main() == 1
    assert not (tmp_path / "report.json").exists()
    assert str(tmp_path) not in capsys.readouterr().err


@respx.mock
def test_cli_verifies_private_capture_retains_report_and_rejects_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            return OBSERVED

    policy, commitment, comment = _fixture()
    capture = tmp_path / "capture"
    save_capture(INDEX_BYTES, commitment, capture, policy, root=tmp_path)
    monkeypatch.setattr(verifier, "datetime", FixedDatetime)
    monkeypatch.setattr(verifier, "_ARTIFACT_ROOT", tmp_path)
    monkeypatch.setattr(verifier, "load_policy", lambda: policy)
    monkeypatch.setattr(
        verifier, "verify_capture", lambda directory, p: verify_capture(directory, p, root=tmp_path)
    )
    repository = policy["github_attestation"]["repository"]
    route = respx.get(f"https://api.github.com/repos/{repository}/issues/comments/42").mock(
        return_value=httpx.Response(200, json=comment)
    )
    output = capture / "attestation.json"
    argv = [
        "verify",
        "--live",
        "--capture-dir",
        str(capture),
        "--comment-id",
        "42",
        "--output",
        str(output),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    assert verifier.main() == 0
    report = json.loads(output.read_text())
    assert report["index_sha256"] == commitment["index_sha256"]
    assert output.stat().st_mode & 0o777 == 0o600
    assert route.call_count == 1
    assert verifier.main() == 1  # existing evidence is never overwritten
    assert route.call_count == 1

    # A malformed private timestamp is rejected before the GitHub request and
    # must not escape as a traceback containing private artifact paths.
    (capture / "commitment.json").write_text(json.dumps({**commitment, "captured_at": {}}))
    monkeypatch.setattr(sys, "argv", [*argv[:-1], str(capture / "second.json")])
    assert verifier.main() == 1
    assert route.call_count == 1
    assert str(tmp_path) not in capsys.readouterr().err
