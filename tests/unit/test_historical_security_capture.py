from __future__ import annotations

import os
from datetime import UTC, datetime

import httpx
import pytest

from edgar_moe.data.historical_identity import IdentityEvidenceError
from edgar_moe.data.screen_inputs import canonical_bytes
from scripts import capture_historical_security_identity as collector
from scripts.capture_historical_security_identity import (
    RetainedCheckpoint,
    SourceCache,
    choose_filings,
    choose_ownership_filings,
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


def test_large_insider_history_is_filtered_before_row_expansion():
    wanted = row()
    count = 100001
    columns = {key: [value] + ["ignored"] * count for key, value in wanted.items() if key != "cik"}
    columns["form"] = ["10-K"] + ["4"] * count
    assert expand_columns(columns, CIK) == [wanted]


def test_discovery_keeps_first_five_then_diversifies_only_pre_cutoff_series():
    newest = [
        {
            **row(i, f"2022-12-{20 - i:02}T00:00:00Z", form="SC 13G"),
            "fileNumber": "005-owner-series",
        }
        for i in range(1, 6)
    ]
    historical = [
        {**row(i, "2022-11-01T00:00:00Z", form="SC 13G"), "fileNumber": "005-other-series"}
        for i in range(6, 16)
    ]
    late = {**row(99, "2023-01-01T00:00:00Z", form="SC 13G"), "fileNumber": "005-future"}
    chosen = choose_ownership_filings(newest + historical + [late], CUTOFF)
    assert chosen[:5] == newest
    assert chosen[5]["fileNumber"] == "005-other-series"
    assert len(chosen) == 10
    assert late not in chosen
    assert len({r["accessionNumber"] for r in chosen}) == 10


def test_filing_series_metadata_is_retained_without_importing_current_tickers():
    expected = {**row(), "fileNumber": "005-synthetic"}
    columns = {key: [value] for key, value in expected.items() if key != "cik"}
    columns["tickers"] = ["CURRENT"]
    assert expand_columns(columns, CIK) == [expected]


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


def test_new_oversized_source_refusal_has_the_same_receipt_on_offline_replay(fd, monkeypatch):
    monkeypatch.setattr("scripts.capture_historical_security_identity.time.sleep", lambda _: None)
    monkeypatch.setattr("scripts.capture_historical_security_identity.MAX_DOCUMENT_BYTES", 4)
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"12345"))
    ) as client:
        live = SourceCache(fd, client, 1)
        with pytest.raises(IdentityEvidenceError, match="document_byte_bound"):
            live.get(URL)
    offline = SourceCache(fd, None, 1)
    with pytest.raises(IdentityEvidenceError, match="document_byte_bound"):
        offline.get(URL)
    assert live.used == offline.used
    assert len(live.used) == 1
    assert offline.requests == 0


def test_transient_failure_defers_only_that_source_without_retry(fd, monkeypatch):
    monkeypatch.setattr("scripts.capture_historical_security_identity.time.sleep", lambda _: None)
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(503 if str(request.url) == URL else 200, content=b"{}")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        cache = SourceCache(fd, client, 10)
        with pytest.raises(IdentityEvidenceError, match="temporarily_unavailable"):
            cache.get(URL)
        with pytest.raises(IdentityEvidenceError, match="deferred_this_run"):
            cache.get(URL)
        assert cache.get(URL.replace("0000000001", "0000000002")) == b"{}"
    assert len(calls) == 2
    assert cache.consecutive_failures == 0
    assert len(cache.deferred) == 1
    assert cache.deferred[digest(URL.encode())]["automatically_retried"] is False


@pytest.mark.parametrize("failure", ["503", "timeout"])
def test_three_consecutive_transient_failures_open_the_circuit(fd, monkeypatch, failure):
    monkeypatch.setattr("scripts.capture_historical_security_identity.time.sleep", lambda _: None)
    calls = []

    def handler(request):
        calls.append(request.url)
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        return httpx.Response(503)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        cache = SourceCache(fd, client, 10)
        for number in (1, 2, 3):
            with pytest.raises(IdentityEvidenceError, match="temporarily_unavailable"):
                cache.get(URL.replace("0000000001", f"{number:010}"))
        with pytest.raises(IdentityEvidenceError, match="circuit_open"):
            cache.get(URL.replace("0000000001", "0000000004"))
    assert len(calls) == 3
    assert len(cache.deferred) == 3


def test_retry_after_stops_before_any_independent_request(fd, monkeypatch):
    monkeypatch.setattr("scripts.capture_historical_security_identity.time.sleep", lambda _: None)
    calls = []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(503, headers={"Retry-After": "60"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        cache = SourceCache(fd, client, 10)
        with pytest.raises(IdentityEvidenceError, match="temporarily_unavailable"):
            cache.get(URL)
        with pytest.raises(IdentityEvidenceError, match="circuit_open"):
            cache.get(URL.replace("0000000001", "0000000002"))
    assert len(calls) == 1


@pytest.mark.parametrize(
    "subject,expected",
    [(CIK, "pre_cutoff_SEC_identity_anchored"), ("0000000002", "ownership_identity_unresolved")],
)
def test_collector_requires_explicit_subject_even_when_submissions_request_matches(
    monkeypatch, subject, expected
):
    periodic = (
        f'<ix:nonNumeric name="dei:EntityCentralIndexKey" contextRef="c">{CIK}</ix:nonNumeric><ix:nonNumeric name="dei:TradingSymbol" contextRef="c">OLD</ix:nonNumeric><ix:nonNumeric name="dei:Security12bTitle" contextRef="c">Common Stock</ix:nonNumeric><ix:nonNumeric name="dei:SecurityExchangeName" contextRef="c">Synthetic exchange</ix:nonNumeric>'
    ).encode()
    own = row(2, form="SC 13G")
    body = (
        f"<SEC-HEADER>\nACCESSION NUMBER: {own['accessionNumber']}\nSUBJECT COMPANY:\n\tCENTRAL INDEX KEY: {subject}\nFILED BY:\n\tCENTRAL INDEX KEY: {CIK}\n</SEC-HEADER><p>(Name of Issuer)</p><p>Common Stock</p><p>(Title of Class of Securities)</p><p>123456782</p><p>(CUSIP Number)</p>"
    ).encode()
    monkeypatch.setattr(collector, "submissions", lambda *args: [row(), own])

    class Retained:
        def filing(self, _):
            return periodic

    class Cache:
        def get(self, url):
            assert url.endswith(own["accessionNumber"] + ".txt")
            return body

    result = collector.collect_member(
        {"cik": CIK, "symbol_at_snapshot": "OLD", "snapshot_member_key": "synthetic"},
        Cache(),
        Retained(),
        CUTOFF,
    )
    assert result["status"] == expected
    if subject == CIK:
        assert result["anchor"]["ownership_subject_evidence"]["subject_cik"] == CIK
    else:
        assert result["refusals"] == {"ownership_subject_issuer_mismatch": 1}


@pytest.mark.parametrize("leg", ["periodic", "ownership"])
@pytest.mark.parametrize("next_filing_succeeds", [True, False])
def test_oversized_source_can_use_next_bounded_filing_without_dropping_member(
    monkeypatch, leg, next_filing_succeeds
):
    periodic_rows = [row(2, "2022-12-01T00:00:00Z"), row(1)]
    ownership_rows = [row(4, "2022-12-01T00:00:00Z", "SC 13G"), row(3, form="SC 13G")]
    monkeypatch.setattr(collector, "submissions", lambda *args: periodic_rows + ownership_rows)
    monkeypatch.setattr(collector, "periodic_anchor", lambda *args, **kwargs: {"periodic": True})
    monkeypatch.setattr(collector, "ownership_subject", lambda *args, **kwargs: {"checked": True})
    monkeypatch.setattr(
        collector, "ownership_identity", lambda *args, **kwargs: {"ownership": True}
    )
    monkeypatch.setattr(collector, "join_identity", lambda *args: {"joined": True})
    attempts = []

    class Retained:
        def filing(self, filing):
            attempts.append(filing["accessionNumber"])
            if leg == "periodic" and (not next_filing_succeeds or filing == periodic_rows[0]):
                raise IdentityEvidenceError("retained_source_too_large")
            return b"synthetic bounded periodic source"

    class Cache:
        def get(self, url):
            attempts.append(url)
            if leg == "ownership" and (
                not next_filing_succeeds or ownership_rows[0]["accessionNumber"] in url
            ):
                raise IdentityEvidenceError("source_document_byte_bound")
            return b"synthetic bounded ownership source"

    member = {"cik": CIK, "symbol_at_snapshot": "OLD", "snapshot_member_key": "synthetic"}
    result = collector.collect_member(member, Cache(), Retained(), CUTOFF)
    assert result["snapshot_member_key"] == member["snapshot_member_key"]
    reason = "retained_source_too_large" if leg == "periodic" else "source_document_byte_bound"
    if next_filing_succeeds:
        assert result["status"] == "pre_cutoff_SEC_identity_anchored"
        assert result["source_size_refusals"] == {reason: 1}
    else:
        assert result["status"] == "source_size_unresolved"
        assert result["refusals"] == {reason: 2}
    assert len(attempts) == (2 if leg == "periodic" and not next_filing_succeeds else 3)


@pytest.mark.parametrize("reason", ["SEC_source_cache_tampered", "source_request_budget_exhausted"])
def test_alternative_filing_does_not_bypass_integrity_or_request_budget(monkeypatch, reason):
    monkeypatch.setattr(collector, "submissions", lambda *args: [row(2), row(1)])
    attempts = []

    class Retained:
        def filing(self, filing):
            attempts.append(filing)
            raise IdentityEvidenceError(reason)

    with pytest.raises(IdentityEvidenceError, match=reason):
        collector.collect_member(
            {"cik": CIK, "symbol_at_snapshot": "OLD", "snapshot_member_key": "synthetic"},
            None,
            Retained(),
            CUTOFF,
        )
    assert len(attempts) == 1


@pytest.mark.parametrize(
    "failure",
    [
        None,
        "shared_primary_context",
        "SEC_source_cache_tampered",
        "source_request_budget_exhausted",
        "source_document_byte_bound",
        "different_issuer",
    ],
)
def test_collects_one_explicit_cover_exhibit_and_rechecks_identity(monkeypatch, failure):
    own = row(2, form="SC 13G")
    parent = (
        f'<ix:nonNumeric name="dei:EntityCentralIndexKey" contextRef="issuer">{CIK}</ix:nonNumeric>'
        '<p>Securities registered pursuant to Section 12(b) of the Act formatted in Inline XBRL: See Exhibit 99.01</p><a href="cover.htm">99.01+</a>'
    ).encode()
    issuer = "0000000002" if failure == "different_issuer" else CIK
    child = (
        f'<ix:nonNumeric name="dei:EntityCentralIndexKey" contextRef="c">{issuer}</ix:nonNumeric><ix:nonNumeric name="dei:TradingSymbol" contextRef="c">OLD</ix:nonNumeric><ix:nonNumeric name="dei:Security12bTitle" contextRef="c">Common Stock</ix:nonNumeric><ix:nonNumeric name="dei:SecurityExchangeName" contextRef="c">Synthetic exchange</ix:nonNumeric>'
    ).encode()
    if failure == "shared_primary_context":
        child = child.replace(
            f'<ix:nonNumeric name="dei:EntityCentralIndexKey" contextRef="c">{issuer}</ix:nonNumeric>'.encode(),
            b"",
        )
        parent += (
            f'<xbrli:context id="c"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{CIK}</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>2022-09-30</xbrli:instant></xbrli:period></xbrli:context>'
        ).encode()
    owner = (
        f"<SEC-HEADER>\nACCESSION NUMBER: {own['accessionNumber']}\nSUBJECT COMPANY:\n\tCENTRAL INDEX KEY: {CIK}\nFILED BY:\n\tCENTRAL INDEX KEY: {CIK}\n</SEC-HEADER><p>(Name of Issuer)</p><p>Common Stock</p><p>(Title of Class of Securities)</p><p>123456782</p><p>(CUSIP Number)</p>"
    ).encode()
    monkeypatch.setattr(collector, "submissions", lambda *args: [row(), own])
    calls = []

    class Retained:
        def filing(self, _):
            return parent

    class Cache:
        def get(self, url):
            calls.append(url)
            if url.endswith("/cover.htm"):
                if failure in {
                    "SEC_source_cache_tampered",
                    "source_request_budget_exhausted",
                    "source_document_byte_bound",
                }:
                    raise IdentityEvidenceError(failure)
                return child
            assert url.endswith(own["accessionNumber"] + ".txt")
            return owner

    def run():
        return collector.collect_member(
            {"cik": CIK, "symbol_at_snapshot": "OLD", "snapshot_member_key": "synthetic"},
            Cache(),
            Retained(),
            CUTOFF,
        )

    if failure in {"SEC_source_cache_tampered", "source_request_budget_exhausted"}:
        with pytest.raises(IdentityEvidenceError, match=failure):
            run()
        assert len(calls) == 1
    elif failure in {"source_document_byte_bound", "different_issuer"}:
        result = run()
        assert result["status"] == (
            "source_size_unresolved"
            if failure == "source_document_byte_bound"
            else "periodic_anchor_unresolved"
        )
        assert len(calls) == 1
    else:
        result = run()
        assert result["status"] == "pre_cutoff_SEC_identity_anchored"
        binding = result["anchor"]["periodic_evidence"]["cover_exhibit_binding"]
        assert binding["primary_document_sha256"] == digest(parent)
        assert binding["exhibit_document_sha256"] == digest(child)
        assert binding["relative_href"] == "cover.htm"
        if failure == "shared_primary_context":
            shared = result["anchor"]["periodic_evidence"]["shared_registration_contexts"]
            assert shared["primary_document_sha256"] == digest(parent)
            assert shared["context_ids"] == ["c"]
        assert len(calls) == 2
