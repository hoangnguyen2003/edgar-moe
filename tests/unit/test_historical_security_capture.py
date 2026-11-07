from __future__ import annotations

import os
from datetime import UTC, datetime

import httpx
import pytest

from edgar_moe.data.historical_identity import IdentityEvidenceError
from edgar_moe.data.screen_inputs import canonical_bytes
from scripts.capture_historical_security_identity import (
    RetainedCheckpoint,
    SourceCache,
    choose_filings,
    digest,
    expand_columns,
    filing_url,
    unpack,
)

CIK = "0000000001"
CUTOFF = datetime(2022, 12, 28, tzinfo=UTC)
URL = "https://data.sec.gov/submissions/CIK0000000001.json"


def row(number=1, accepted="2022-11-01T00:00:00Z", form="10-K"):
    return {
        "cik": CIK,
        "accessionNumber": f"0000000001-22-{number:06}",
        "acceptanceDateTime": accepted,
        "form": form,
        "primaryDocument": "synthetic.htm",
    }


@pytest.fixture
def fd(tmp_path):
    os.chmod(tmp_path, 0o700)
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


def test_selects_latest_available_filing_without_late_mapping_or_current_listing_state():
    old = row(1)
    renamed = {**row(2, "2022-12-01T00:00:00Z"), "current_symbol": "RENAMED", "active": False}
    late = row(3, "2022-12-26T00:00:00Z")
    rows = [old, renamed, late, row(4, "2020-01-01T00:00:00Z")]
    assert choose_filings(rows, {"10-K"}, CUTOFF, 4) == [renamed, old]
    assert choose_filings(rows, {"10-K"}, CUTOFF, 1) == [renamed]


def test_conflicting_accession_metadata_fails_closed():
    with pytest.raises(IdentityEvidenceError, match="conflicting"):
        choose_filings([row(), {**row(), "primaryDocument": "another.htm"}], {"10-K"}, CUTOFF, 4)


def test_submissions_extract_only_filing_columns_not_present_day_tickers():
    expected = row()
    columns = {key: [value] for key, value in expected.items() if key != "cik"}
    columns["tickers"] = ["SURVIVOR"]
    assert expand_columns(columns, CIK) == [expected]
    columns["form"] = []
    with pytest.raises(IdentityEvidenceError, match="inconsistent"):
        expand_columns(columns, CIK)


@pytest.mark.parametrize(
    "document",
    [
        "../secret.htm",
        "x.htm?query=1",
        "https://example.com/x.htm",
        "x.htm#fragment",
        "x.htm/sub.htm",
    ],
)
def test_archive_paths_cannot_redirect_or_traverse(document):
    with pytest.raises(IdentityEvidenceError, match="archive_url"):
        filing_url({**row(), "primaryDocument": document})


def test_cache_replays_exact_owner_only_source_without_another_request(fd, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("scripts.capture_historical_security_identity.time.sleep", lambda _: None)

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=b'{"synthetic":true}')

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        cache = SourceCache(fd, client, 1)
        body = cache.get(URL)
        assert cache.get(URL) == body
    offline = SourceCache(fd, None, 1)
    assert offline.get(URL) == body
    assert calls == [URL]
    assert len(offline.used) == 1
    assert all(path.stat().st_mode & 0o077 == 0 for path in tmp_path.iterdir())


def test_missing_source_in_offline_mode_never_calls_provider(fd):
    with pytest.raises(IdentityEvidenceError, match="uncaptured"):
        SourceCache(fd, None, 1).get(URL)


def test_tampered_source_and_orphan_pair_do_not_trigger_new_live_request(fd, tmp_path):
    key = digest(URL.encode())
    body = tmp_path / (key + ".body")
    body.write_bytes(b"original")
    body.chmod(0o600)

    def no_request(_):
        pytest.fail("tampered/orphan source must not be repaired with a provider request")

    with httpx.Client(transport=httpx.MockTransport(no_request)) as client:
        with pytest.raises(IdentityEvidenceError, match="incomplete"):
            SourceCache(fd, client, 1).get(URL)
        meta = tmp_path / (key + ".json")
        meta.write_bytes(
            canonical_bytes(
                {
                    "url": URL,
                    "sha256": digest(b"changed"),
                    "bytes": 8,
                    "retrieved_at": "2026-11-07T00:00:00Z",
                }
            )
        )
        meta.chmod(0o600)
        with pytest.raises(IdentityEvidenceError, match="tampered"):
            SourceCache(fd, client, 1).get(URL)


def test_request_budget_and_http_refusals_stop_without_retry(fd, monkeypatch):
    calls = []
    monkeypatch.setattr("scripts.capture_historical_security_identity.time.sleep", lambda _: None)

    def handler(request):
        calls.append(request.url)
        return httpx.Response(429)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        cache = SourceCache(fd, client, 1)
        with pytest.raises(httpx.HTTPStatusError):
            cache.get(URL)
        with pytest.raises(IdentityEvidenceError, match="budget"):
            cache.get(URL)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://data.sec.gov/submissions/CIK0000000001.json",
        "https://evil.example/x",
        "https://data.sec.gov@evil.example/x",
        URL + "?redirect=1",
    ],
)
def test_only_fixed_sec_hosts_and_source_paths_are_allowed(fd, url):
    with pytest.raises(IdentityEvidenceError, match="URL"):
        SourceCache(fd, None, 1).get(url)


def test_source_byte_budget_and_redirects_fail_without_retry(fd, monkeypatch):
    monkeypatch.setattr("scripts.capture_historical_security_identity.time.sleep", lambda _: None)
    monkeypatch.setattr("scripts.capture_historical_security_identity.MAX_DOCUMENT_BYTES", 4)
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"12345"))
        ) as client,
        pytest.raises(IdentityEvidenceError, match="document_byte_bound"),
    ):
        SourceCache(fd, client, 1).get(URL)
    with (
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(302, headers={"location": "https://example.com"})
            ),
            follow_redirects=False,
        ) as client,
        pytest.raises(httpx.HTTPStatusError),
    ):
        SourceCache(fd, client, 1).get(URL.replace("0000000001", "0000000002"))


def test_retained_checkpoint_cannot_substitute_a_new_manifest(tmp_path):
    (tmp_path / "manifest.json").write_bytes(b"{}")
    with pytest.raises(IdentityEvidenceError, match="manifest_mismatch"):
        RetainedCheckpoint(tmp_path)


def test_decompression_is_bounded(monkeypatch):
    import gzip

    monkeypatch.setattr("scripts.capture_historical_security_identity.MAX_DOCUMENT_BYTES", 4)
    with pytest.raises(IdentityEvidenceError, match="too_large"):
        unpack(gzip.compress(b"12345"))
