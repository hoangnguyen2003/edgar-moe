"""What the public bundle publishes from each upstream source.

The redistribution review recorded in `public/data-provenance.json` is an
operator decision about specific content, so the content must not drift after
it is made. These assertions describe the published surface exactly: derived
scores, SEC identifiers and links, and aggregate series. A new field, or a
string long enough to hold document text, fails here and sends the question
back for review rather than shipping quietly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
SNAPSHOT = REPOSITORY / "data" / "demo" / "snapshot.json"
PROVENANCE = REPOSITORY / "apps" / "web" / "public" / "data-provenance.json"

#: Every field published for one scored filing. SEC identifiers and links, the
#: model's own outputs, and one realized outcome computed from market data.
EVENT_FIELDS = frozenset(
    {
        "accepted_at",
        "accession_number",
        "company_name",
        "direction",
        "entry_date",
        "event_id",
        "expert_weights",
        "filing_url",
        "form",
        "horizon_date",
        "industry_code",
        "rank",
        "realized_abnormal_return",
        "score",
        "security_id",
        "ticker",
        "top_attributions",
    }
)

EQUITY_POINT_FIELDS = frozenset({"date", "equity", "drawdown", "turnover"})

#: No string in the snapshot should be long enough to carry filing text; the
#: longest today is a 233-character status message written by this project.
MAX_STRING_LENGTH = 400

#: Field names a republished price bar, quote, or filing body would carry.
#: Matched exactly, because "high" is also how a confidence bound is named.
FORBIDDEN_FIELD_NAMES = frozenset(
    {
        "open",
        "high",
        "low",
        "close",
        "adj_close",
        "adjusted_close",
        "volume",
        "vwap",
        "bid",
        "ask",
        "trade_count",
        "observations",
        "facts",
        "body",
        "content",
        "html",
        # "text" is not listed: it names the filing-text expert's weight, and
        # republished text is caught by the string-length assertion instead.
    }
)

#: Fragments that are unambiguous wherever they appear in a field name.
FORBIDDEN_FRAGMENTS = ("xbrl", "raw_", "_raw", "filing_text")


@pytest.fixture(scope="module")
def snapshot() -> dict[str, Any]:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def _strings(node: Any, path: str = "$") -> list[tuple[str, str]]:
    if isinstance(node, dict):
        return [item for key, value in node.items() for item in _strings(value, f"{path}.{key}")]
    if isinstance(node, list):
        return [item for i, value in enumerate(node) for item in _strings(value, f"{path}[{i}]")]
    return [(path, node)] if isinstance(node, str) else []


def _field_names(node: Any) -> set[str]:
    if isinstance(node, dict):
        return set(node) | {name for value in node.values() for name in _field_names(value)}
    if isinstance(node, list):
        return {name for value in node for name in _field_names(value)}
    return set()


@pytest.mark.parametrize("section", ["events", "latest_signals"])
def test_a_published_filing_carries_exactly_the_reviewed_fields(
    snapshot: dict[str, Any], section: str
) -> None:
    rows = snapshot[section]

    assert rows, f"{section} is empty"
    for row in rows:
        assert set(row) == EVENT_FIELDS, f"{section} field set changed: {sorted(set(row))}"


def test_no_filing_text_is_republished(snapshot: dict[str, Any]) -> None:
    # SEC filings are public domain, but the bundle links to them rather than
    # copying them, which is also what keeps it small enough to review by eye.
    too_long = [
        (path, len(value)) for path, value in _strings(snapshot) if len(value) > MAX_STRING_LENGTH
    ]

    assert not too_long, f"strings long enough to hold document text: {too_long}"


def test_filing_links_point_back_to_the_regulator(snapshot: dict[str, Any]) -> None:
    for row in snapshot["events"]:
        assert row["filing_url"].startswith("https://www.sec.gov/"), row["filing_url"]


def test_market_data_appears_only_as_derived_figures(snapshot: dict[str, Any]) -> None:
    # One realized abnormal return per filing and an indexed portfolio series;
    # no quotes, bars, or price levels.
    for series in snapshot["equity_curves"].values():
        assert series
        for point in series:
            assert set(point) == EQUITY_POINT_FIELDS
    assert snapshot["equity_curves"]["cost_10bps"][0]["equity"] == 1.0

    names = {name.lower() for name in _field_names(snapshot)}
    offenders = sorted(
        name
        for name in names
        if name in FORBIDDEN_FIELD_NAMES or any(bad in name for bad in FORBIDDEN_FRAGMENTS)
    )
    assert not offenders, f"field names suggesting republished vendor data: {offenders}"


def test_the_provenance_manifest_names_every_source_the_bundle_depends_on() -> None:
    manifest = json.loads(PROVENANCE.read_text(encoding="utf-8"))

    assert {source["id"] for source in manifest["sources"]} == {
        "sec-edgar",
        "alpaca-iex",
        "fred-alfred",
    }
    for source in manifest["sources"]:
        assert source["terms_url"].startswith("https://")
        assert source["redistribution_status"] in {"review_required", "approved"}


def test_the_review_state_is_recorded_rather_than_assumed() -> None:
    # The readiness report reads these fields; an approval has to be written
    # here deliberately, and dated, before the report can say "ready".
    review = json.loads(PROVENANCE.read_text(encoding="utf-8"))["review"]

    assert set(review) == {"redistribution_status", "legal_approval", "last_reviewed_at"}
    assert review["redistribution_status"] in {"operator_review_required", "approved"}
    if review["redistribution_status"] == "approved":
        assert review["legal_approval"] is True
        assert review["last_reviewed_at"], "an approval must record when it was made"
