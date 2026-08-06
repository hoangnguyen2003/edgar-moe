from datetime import UTC, date
from pathlib import Path

from edgar_moe.data.sec import (
    FilingParser,
    merge_submission_columns,
    parse_sec_acceptance,
    periodic_filing_records,
    submission_rows,
)


def test_parser_extracts_longest_real_sections() -> None:
    html = Path("tests/fixtures/filing.html").read_text(encoding="utf-8")
    sections = FilingParser().extract_sections(html)
    assert set(sections) == {"risk_factors", "management_discussion"}
    assert len(sections["risk_factors"].text) > 500
    assert "Revenue improved" in sections["management_discussion"].text


def test_submission_history_merges_and_filters_periodic_filings() -> None:
    recent = {
        "accessionNumber": ["0000000001-25-000001", "0000000001-25-000002"],
        "acceptanceDateTime": ["20250501173000", "2025-05-02T17:30:00-04:00"],
        "reportDate": ["2025-03-31", "2025-03-31"],
        "form": ["10-Q", "10-Q/A"],
        "primaryDocument": ["quarter.htm", "amendment.htm"],
    }
    older = {
        "accessionNumber": ["0000000001-24-000001"],
        "acceptanceDateTime": ["2024-03-01T17:30:00-05:00"],
        "reportDate": ["2023-12-31"],
        "form": ["10-K"],
        "primaryDocument": ["annual.htm"],
    }
    merged = merge_submission_columns(recent, older)
    payload = {
        "cik": "1",
        "name": "Example Corp",
        "sic": "3571",
        "filings": {"recent": merged},
    }

    records = periodic_filing_records(payload, start=date(2024, 1, 1), end=date(2025, 12, 31))

    assert len(submission_rows(merged)) == 3
    assert [row["form"] for row in records] == ["10-K", "10-Q"]
    assert records[-1]["filingUrl"].endswith("/quarter.htm")
    assert parse_sec_acceptance("20250501173000").tzinfo is UTC
