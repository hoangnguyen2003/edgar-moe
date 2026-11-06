"""Synthetic source revisions only; CI never requests Wikipedia or market data."""

from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from scripts import capture_historical_candidate_roster as capture

OBSERVED = datetime(2026, 11, 6, tzinfo=UTC)
HEADER = (
    '{| class="wikitable sortable" id="constituents"\n|-\n'
    "![[Ticker symbol|Symbol]]\n"
    "! Security !![[SEC filing]]s !! [[Global Industry Classification Standard|GICS]] Sector !! GICS Sub-Industry !! Headquarters Location !! Date first added!! [[Central Index Key|CIK]] !! Founded\n"
)


def row(symbol: str, cik: str, label: str, *, template: str = "NyseSymbol") -> str:
    return (
        f"|-\n|{{{{{template}|{symbol}}}}}\n"
        f"|{label}||links||Synthetic sector||Synthetic industry||Synthetic place||2000-01-01||{cik}||1900\n"
    )


def fixture(text: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    if text is None:
        text = HEADER + row("REN", "1", "Synthetic later-renamed")
        text += row("DEL", "2", "Synthetic later-delisted")
        text += row("CLS.A", "3", "Synthetic Class A")
        text += row("CLS.B", "3", "Synthetic Class B") + "|}\n"
    policy = capture.load_policy()
    policy.update(
        cohort_id="synthetic-cohort",
        page_id=7,
        revision_id=42,
        expected_candidates=4,
        expected_issuers=3,
        wikitext_sha256=hashlib.sha256(text.encode()).hexdigest(),
    )
    value = {
        "query": {
            "pages": {
                "7": {
                    "pageid": 7,
                    "title": policy["page_title"],
                    "revisions": [
                        {
                            "revid": 42,
                            "timestamp": policy["revision_timestamp"],
                            "sha1": hashlib.sha1(text.encode()).hexdigest(),
                            "slots": {"main": {"contentmodel": "wikitext", "*": text}},
                        }
                    ],
                }
            }
        }
    }
    return policy, value


def encoded(value: Any) -> bytes:
    return json.dumps(value).encode()


def test_retains_every_historical_member_and_separate_share_classes() -> None:
    policy, value = fixture()
    roster = json.loads(capture.build_roster(encoded(value), policy))
    members = roster["members"]
    assert {r["symbol_at_snapshot"] for r in members} == {"REN", "DEL", "CLS.A", "CLS.B"}
    assert len({r["snapshot_member_key"] for r in members}) == 4
    assert len({r["cik"] for r in members}) == 3
    assert {r["cik"] for r in members} == {"0000000001", "0000000002", "0000000003"}
    assert roster["later_status_rule"] == "retain_every_snapshot_member"
    assert roster["status"] == capture.STATUS
    # Retrieving the same fixed source years later never consults survivors or
    # substitutes today's renamed symbol, nor collapses two classes into a CIK.
    first = capture.commitment(encoded(value), policy, OBSERVED)
    later = capture.commitment(encoded(value), policy, datetime(2027, 1, 1, tzinfo=UTC))
    assert first["roster_sha256"] == later["roster_sha256"]
    assert first["retrieved_at"] != policy["revision_timestamp"]


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("revid", 43),
        ("revid", True),
        ("timestamp", "2023-01-02T00:00:00Z"),
        ("timestamp", "2022-12-28T23:23:50"),
        ("sha1", "f" * 40),
    ],
)
def test_rejects_late_revisions_identity_substitution_and_server_digest(field, replacement):
    policy, value = fixture()
    value["query"]["pages"]["7"]["revisions"][0][field] = replacement
    with pytest.raises(ValueError):
        capture.build_roster(encoded(value), policy)


def test_rejects_changed_source_even_when_server_sha1_is_updated() -> None:
    policy, value = fixture()
    revision = value["query"]["pages"]["7"]["revisions"][0]
    revision["slots"]["main"]["*"] += "\nchanged source\n"
    revision["sha1"] = hashlib.sha1(revision["slots"]["main"]["*"].encode()).hexdigest()
    with pytest.raises(ValueError, match="digest"):
        capture.build_roster(encoded(value), policy)


@pytest.mark.parametrize(
    "change", ["missing", "duplicate", "invalid_cik", "unknown_template", "columns"]
)
def test_rejects_ambiguous_or_partial_rosters_even_with_matching_source_digest(change):
    text = HEADER + row("AAA", "1", "Synthetic") + row("BBB", "2", "Synthetic") + "|}\n"
    if change == "missing":
        text = text.replace(row("BBB", "2", "Synthetic"), "")
    elif change == "duplicate":
        text = text.replace("BBB", "AAA")
    elif change == "invalid_cik":
        text = text.replace("||2||", "||True||")
    elif change == "unknown_template":
        text = text.replace("NyseSymbol", "UnknownSymbol")
    else:
        text = text.replace("Synthetic sector||", "")
    policy, value = fixture(text)
    policy.update(expected_candidates=2, expected_issuers=2)
    with pytest.raises(ValueError):
        capture.build_roster(encoded(value), policy)


def test_inline_ticker_comments_preserve_the_dotted_symbol() -> None:
    text = HEADER + row("CLS.A", "1", "Synthetic") + "|}\n"
    text = text.replace(
        "{{NyseSymbol|CLS.A}}", "{{NyseSymbol|CLS.A}} <!-- do not change punctuation -->"
    )
    policy, value = fixture(text)
    policy.update(expected_candidates=1, expected_issuers=1)
    assert (
        json.loads(capture.build_roster(encoded(value), policy))["members"][0]["symbol_at_snapshot"]
        == "CLS.A"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("page_id", True),
        ("revision_id", 42.0),
        ("expected_candidates", True),
        ("expected_issuers", 504),
        ("revision_timestamp", "2023-01-02T00:00:00Z"),
        ("scope", "research_cleared"),
    ],
)
def test_policy_cannot_relax_identity_chronology_or_scope(field, value):
    policy = capture.load_policy()
    policy[field] = value
    with pytest.raises(ValueError):
        capture.validate_policy(policy)


def test_rejects_duplicate_json_and_hidden_revision_content() -> None:
    policy, value = fixture()
    raw = encoded(value).replace(b'"revid": 42', b'"revid": 41, "revid": 42')
    with pytest.raises(ValueError, match="duplicate"):
        capture.build_roster(raw, policy)
    del value["query"]["pages"]["7"]["revisions"][0]["slots"]["main"]["*"]
    with pytest.raises(ValueError, match="unavailable"):
        capture.build_roster(encoded(value), policy)


def test_private_capture_replays_and_rejects_tampered_members_and_commitment(tmp_path):
    policy, value = fixture()
    path = tmp_path / "capture"
    report = capture.save_capture(encoded(value), policy, path, observed_at=OBSERVED, root=tmp_path)
    assert capture.verify_capture(path, policy, root=tmp_path) == report
    assert path.stat().st_mode & 0o077 == 0
    assert all(f.stat().st_mode & 0o077 == 0 for f in path.iterdir())
    original = (path / "candidate-roster.json").read_bytes()
    (path / "candidate-roster.json").write_bytes(original.replace(b'"DEL"', b'"NEW"'))
    with pytest.raises(ValueError):
        capture.verify_capture(path, policy, root=tmp_path)
    (path / "candidate-roster.json").write_bytes(original)
    changed = deepcopy(report)
    changed["candidate_count"] = 4.0
    (path / "commitment.json").write_bytes(encoded(changed))
    with pytest.raises(ValueError):
        capture.verify_capture(path, policy, root=tmp_path)


@pytest.mark.parametrize("alias", ["symlink", "hardlink", "public"])
def test_private_source_cannot_be_aliased_or_public(tmp_path, alias):
    policy, value = fixture()
    path = tmp_path / "capture"
    capture.save_capture(encoded(value), policy, path, observed_at=OBSERVED, root=tmp_path)
    target = path / "revision.json"
    if alias == "public":
        target.chmod(0o644)
    else:
        original = path / "original.json"
        target.rename(original)
        if alias == "symlink":
            target.symlink_to(original)
        else:
            os.link(original, target)
    with pytest.raises(RuntimeError):
        capture.verify_capture(path, policy, root=tmp_path)


def test_operational_commitment_has_no_source_rows_or_completed_claim() -> None:
    policy, value = fixture()
    report = capture.commitment(encoded(value), policy, OBSERVED)
    assert "Synthetic" not in json.dumps(report)
    assert not {"members", "cik", "symbol_at_snapshot"}.intersection(report)
    assert report["market_provider_requests"] == 0
    assert report["issue_complete"] is report["security_identifier_continuity_verified"] is False
    assert report["checkpoint_dataset_binding_verified"] is False


def test_retained_public_capture_is_code_bound_and_cannot_claim_completion():
    root = Path(__file__).resolve().parents[2]
    record = json.loads(
        (root / "ops/issue292/historical-candidate-source-20261106.json").read_bytes()
    )
    assert (
        record["capture_code_sha256"]
        == hashlib.sha256(Path(capture.__file__).read_bytes()).hexdigest()
    )
    assert record["public_roster_rows"] == 0
    report = record["capture"]
    policy = capture.load_policy()
    assert report["policy_sha256"] == hashlib.sha256(capture.canonical_bytes(policy)).hexdigest()
    assert report["source_revision_id"] == policy["revision_id"]
    assert report["source_revision_timestamp"] == policy["revision_timestamp"]
    assert report["source_wikitext_sha256"] == policy["wikitext_sha256"]
    assert report["candidate_count"] == policy["expected_candidates"]
    assert report["issuer_count"] == policy["expected_issuers"]
    assert report["status"] == capture.STATUS
    for flag in (
        "issue_complete",
        "security_identifier_continuity_verified",
        "checkpoint_dataset_binding_verified",
    ):
        assert report[flag] is False
    assert not {"members", "cik", "symbol_at_snapshot"}.intersection(report)


@respx.mock
def test_live_fetch_uses_one_fixed_anonymous_revision_request():
    policy, value = fixture()
    route = respx.get(capture.API).respond(200, content=encoded(value))
    with httpx.Client(follow_redirects=False) as client:
        assert capture.fetch_revision(client, policy) == encoded(value)
    assert route.call_count == 1
    request = route.calls[0].request
    assert request.url.params["revids"] == "42"
    assert "titles" not in request.url.params and "rvstart" not in request.url.params
    assert "authorization" not in request.headers and "cookie" not in request.headers


@pytest.mark.parametrize("status", [301, 403, 429, 500])
@respx.mock
def test_live_fetch_does_not_retry_or_follow_redirects(status):
    policy, _ = fixture()
    route = respx.get(capture.API).respond(status, headers={"Location": "https://other.invalid"})
    with httpx.Client(follow_redirects=False) as client, pytest.raises(httpx.HTTPStatusError):
        capture.fetch_revision(client, policy)
    assert route.call_count == 1


@respx.mock
def test_live_fetch_bounds_response_bytes():
    route = respx.get(capture.API).respond(200, content=b"x" * (capture.MAX_RESPONSE_BYTES + 1))
    with (
        httpx.Client(follow_redirects=False) as client,
        pytest.raises(ValueError, match="byte bound"),
    ):
        capture.fetch_revision(client, capture.load_policy())
    assert route.call_count == 1


def test_invalid_destination_is_refused_before_live_transport(tmp_path, monkeypatch, capsys):
    path = tmp_path / "already-exists"
    path.mkdir()
    monkeypatch.setattr(capture, "ARTIFACT_ROOT", tmp_path)
    monkeypatch.setattr("sys.argv", ["capture", "--live", "--output-dir", str(path)])

    def forbidden(*args, **kwargs):
        raise AssertionError("Transport must not be constructed")

    monkeypatch.setattr(capture.httpx, "Client", forbidden)
    assert capture.main() == 1
    assert "refused" in capsys.readouterr().err.lower()


def test_live_failure_reports_status_without_url_body_or_credentials(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(capture, "ARTIFACT_ROOT", tmp_path)
    monkeypatch.setattr("sys.argv", ["capture", "--live", "--output-dir", str(tmp_path / "new")])

    def refused(*args, **kwargs):
        request = httpx.Request("GET", "https://source.invalid/?secret=withheld")
        response = httpx.Response(429, request=request, text="private response must stay withheld")
        raise httpx.HTTPStatusError(
            "private exception must stay withheld", request=request, response=response
        )

    monkeypatch.setattr(capture, "fetch_revision", refused)
    assert capture.main() == 1
    message = capsys.readouterr().err
    assert "HTTP 429" in message
    assert "source.invalid" not in message and "secret=" not in message and "private" not in message
    assert not (tmp_path / "new").exists()
