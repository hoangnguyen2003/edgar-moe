"""Credential-free synthetic checks for the SEC historical-index feasibility pilot."""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from scripts.pilot_sec_archive_coverage import (
    ArchivePilotError,
    assess_archive_sample,
    choose_sample,
    classify_cover_facts,
    parse_daily_master_index,
    run_live,
    write_report,
)

INDEX_DATE = date(2022, 6, 15)
CUTOFF = date(2022, 12, 31)
ALPHA_PATH = "edgar/data/100/0000000100-22-000001.txt"
BETA_PATH = "edgar/data/200/0000000200-22-000002.txt"
ALPHA_LATER_PATH = "edgar/data/100/0000000100-22-000003.txt"


def _index(*extra: str) -> bytes:
    lines = [
        "Description: Master Index of EDGAR Dissemination Feed",
        "CIK|Company Name|Form Type|Date Filed|File Name",
        "-------------------------------------------------------",
        f"100|Alpha Incorporated|10-K|2022-06-15|{ALPHA_PATH}",
        f"200|Beta Corporation|10-Q|2022-06-15|{BETA_PATH}",
        "300|Gamma|8-K|2022-06-15|edgar/data/300/0000000300-22-000001.txt",
        *extra,
    ]
    return ("\n".join(lines) + "\n").encode()


def _cover(symbol: str, *, context: str = "class-a", exchange: str = "NASDAQ") -> bytes:
    return (
        f'<ix:nonNumeric name="dei:TradingSymbol" contextRef="{context}">'
        f"{symbol}</ix:nonNumeric>"
        f'<ix:nonNumeric name="dei:SecurityExchangeName" contextRef="{context}">'
        f"{exchange}</ix:nonNumeric>"
    ).encode()


def test_index_parser_selects_periodic_rows_without_current_master() -> None:
    records = parse_daily_master_index(_index(), index_date=INDEX_DATE)
    assert len(records) == 2
    assert {record.form for record in records} == {"10-K", "10-Q"}
    assert {record.cik for record in records} == {"0000000100", "0000000200"}
    assert {record.path for record in records} == {ALPHA_PATH, BETA_PATH}
    assert (
        parse_daily_master_index(
            _index().replace(b"Date Filed|File Name", b"Date Filed|Filename"),
            index_date=INDEX_DATE,
        )
        == records
    )


@pytest.mark.parametrize(
    "payload",
    [
        b"not an EDGAR master index",
        _index("400|Bad|10-K|2022-06-15|edgar/data/401/0000000400-22-000001.txt"),
        _index("400|Bad|10-K|2022-06-16|edgar/data/400/0000000400-22-000001.txt"),
        _index(f"100|Alpha Incorporated|10-K|2022-06-15|{ALPHA_PATH}"),
        _index("400|Bad|10-K|2022-06-15|../../outside.txt"),
    ],
)
def test_index_parser_fails_closed_on_missing_or_tampered_source(payload: bytes) -> None:
    with pytest.raises(ArchivePilotError):
        parse_daily_master_index(payload, index_date=INDEX_DATE)


def test_sample_is_distinct_deterministic_and_rejects_late_index() -> None:
    records = parse_daily_master_index(
        _index(f"100|Alpha Incorporated|10-Q|2022-06-15|{ALPHA_LATER_PATH}"),
        index_date=INDEX_DATE,
    )
    first = choose_sample(records, index_date=INDEX_DATE, cutoff=CUTOFF, limit=2)
    second = choose_sample(records, index_date=INDEX_DATE, cutoff=CUTOFF, limit=2)
    assert first == second
    assert len({record.cik for record in first}) == 2
    assert next(record.path for record in first if record.cik == "0000000100") == (ALPHA_LATER_PATH)
    with pytest.raises(ArchivePilotError, match="too close"):
        choose_sample(records, index_date=INDEX_DATE, cutoff=date(2022, 6, 20), limit=2)
    with pytest.raises(ArchivePilotError, match="bounded"):
        choose_sample(records, index_date=INDEX_DATE, cutoff=CUTOFF, limit=13)


def test_cover_parser_classifies_missing_ambiguous_and_context_mismatch() -> None:
    assert classify_cover_facts(_cover("AAA")) == "single_symbol_exchange_context"
    assert classify_cover_facts(b"<html>No tagged cover facts</html>") == "missing_symbol_fact"
    assert (
        classify_cover_facts(_cover("AAA") + _cover("BBB", context="class-b"))
        == "ambiguous_symbol_or_class"
    )
    assert (
        classify_cover_facts(
            b'<ix:nonNumeric name="dei:TradingSymbol" contextRef="class-a">AAA</ix:nonNumeric>'
            b'<ix:nonNumeric name="dei:SecurityExchangeName" contextRef="class-b">NASDAQ</ix:nonNumeric>'
        )
        == "missing_matching_exchange_fact"
    )
    assert classify_cover_facts(_cover("BAD TICKER")) == "unsupported_symbol_format"


def test_report_is_identity_redacted_and_binds_source_bytes() -> None:
    files = {ALPHA_PATH: _cover("AAA"), BETA_PATH: b"<html>no cover facts</html>"}
    captured_at = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)

    def fetch(path: str) -> bytes:
        return files[path]

    report = assess_archive_sample(
        _index(),
        index_date=INDEX_DATE,
        cutoff=CUTOFF,
        limit=2,
        fetch_filing=fetch,
        live=False,
        captured_at=captured_at,
    )
    assert report["status"] == "feasibility_only_not_historical_membership_evidence"
    assert report["sample_count"] == 2
    assert report["cover_fact_classification_counts"] == {
        "missing_symbol_fact": 1,
        "single_symbol_exchange_context": 1,
    }
    serialized = json.dumps(report)
    for private_identity in ("Alpha", "Beta", "AAA", "0000000100", "edgar/data/"):
        assert private_identity not in serialized

    changed_index = _index().replace(b"Alpha Incorporated", b"Alpha Renamed")
    index_changed = assess_archive_sample(
        changed_index,
        index_date=INDEX_DATE,
        cutoff=CUTOFF,
        limit=2,
        fetch_filing=fetch,
        live=False,
        captured_at=captured_at,
    )
    assert index_changed["index_sha256"] != report["index_sha256"]
    files[ALPHA_PATH] = _cover("NEW")
    filing_changed = assess_archive_sample(
        _index(),
        index_date=INDEX_DATE,
        cutoff=CUTOFF,
        limit=2,
        fetch_filing=fetch,
        live=False,
        captured_at=captured_at,
    )
    assert filing_changed["sample_input_manifest_sha256"] != report["sample_input_manifest_sha256"]


def test_private_report_boundary_and_no_overwrite(tmp_path: Path) -> None:
    root = tmp_path / "private-artifacts"
    destination = root / "pilot.json"
    with pytest.raises(ArchivePilotError):
        write_report({"status": "test"}, tmp_path / "outside.json", root=root)
    write_report({"status": "test"}, destination, root=root)
    assert json.loads(destination.read_text()) == {"status": "test"}
    assert destination.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        write_report({"status": "replacement"}, destination, root=root)


def test_live_requires_declared_sec_user_agent_before_network() -> None:
    with pytest.raises(ArchivePilotError, match="SEC_USER_AGENT"):
        run_live(index_date=INDEX_DATE, cutoff=CUTOFF, limit=1, user_agent="")


def test_no_credential_environment_is_needed_by_offline_pilot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    report = assess_archive_sample(
        _index(),
        index_date=INDEX_DATE,
        cutoff=CUTOFF,
        limit=1,
        fetch_filing=lambda _path: _cover("AAA"),
        live=False,
    )
    assert report["provider_contacted"] is False
    assert os.environ.get("SEC_USER_AGENT") is None
