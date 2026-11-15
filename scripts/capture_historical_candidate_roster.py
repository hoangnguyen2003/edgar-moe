"""Pin a real dated candidate roster, without claiming a historical security master.

Run as ``python -m scripts.capture_historical_candidate_roster``. Live mode makes
one fixed anonymous revision request. Offline mode replays privately retained
bytes. Neither mode contacts market providers, joins present-day identifiers,
screens prices, trains models, or releases research/publication gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from edgar_moe.data.screen_inputs import canonical_bytes, decode_json
from scripts.capture_prospective_sec_index import (
    _capture_directory,
    _preflight_new_capture,
    _read_private_file,
    _write_new,
)

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "config/historical_candidate_roster_v1.json"
ARTIFACT_ROOT = ROOT / "data/artifacts"
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_ROSTER_BYTES = 1024 * 1024
API = "https://en.wikipedia.org/w/api.php"
STATUS = "historical_candidate_source_only_security_continuity_unverified"
_POLICY_FIELDS = {
    "schema_version",
    "cohort_id",
    "source",
    "page_id",
    "page_title",
    "revision_id",
    "revision_timestamp",
    "wikitext_sha256",
    "expected_candidates",
    "expected_issuers",
    "first_validation_start",
    "selection_rule",
    "later_status_rule",
    "scope",
}
_TICKER = re.compile(r"\{\{(NyseSymbol|NasdaqSymbol|BZX link)\|([A-Z][A-Z0-9.-]{0,14})\}\}")
_HEADERS = [
    "![[Ticker symbol|Symbol]]",
    "! Security !![[SEC filing]]s !! [[Global Industry Classification Standard|GICS]] Sector !! GICS Sub-Industry !! Headquarters Location !! Date first added!! [[Central Index Key|CIK]] !! Founded",
]


def _utc(value: Any) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("A UTC source timestamp ending in Z is required")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() != UTC.utcoffset(result):
        raise ValueError("Source timestamp must be UTC")
    return result


def validate_policy(policy: Any) -> dict[str, Any]:
    if not isinstance(policy, dict) or set(policy) != _POLICY_FIELDS:
        raise ValueError("Historical candidate policy schema differs")
    if (
        type(policy["schema_version"]) is not int
        or policy["schema_version"] != 1
        or policy["source"] != "enwiki-server-revision"
        or policy["page_title"] != "List of S&P 500 companies"
        or policy["selection_rule"] != "all_rows_in_the_pinned_constituents_table"
        or policy["later_status_rule"] != "retain_every_snapshot_member"
        or policy["scope"] != "private_source_feasibility_only"
        or not isinstance(policy["cohort_id"], str)
        or re.fullmatch(r"[a-z0-9][a-z0-9-]{1,79}", policy["cohort_id"]) is None
        or not isinstance(policy["wikitext_sha256"], str)
        or re.fullmatch(r"[a-f0-9]{64}", policy["wikitext_sha256"]) is None
    ):
        raise ValueError("Historical candidate policy is unsupported")
    for field in ("page_id", "revision_id", "expected_candidates", "expected_issuers"):
        if type(policy[field]) is not int or policy[field] <= 0:
            raise ValueError("Historical candidate policy needs positive exact integers")
    if (
        policy["expected_candidates"] > 1000
        or policy["expected_issuers"] > policy["expected_candidates"]
        or not _utc(policy["revision_timestamp"]) < _utc(policy["first_validation_start"])
    ):
        raise ValueError("Historical candidate policy count or chronology is invalid")
    return policy


def load_policy(path: Path = POLICY) -> dict[str, Any]:
    return validate_policy(decode_json(path.read_bytes()))


def parse_members(text: str, policy: dict[str, Any]) -> list[dict[str, str]]:
    """Retain each source row; CIKs identify issuers, never share classes."""
    start = text.find('{| class="wikitable sortable" id="constituents"')
    end = text.find("|}", start)
    if start < 0 or end < start:
        raise ValueError("Pinned constituents table is missing")
    blocks = text[start:end].split("|-")
    if len(blocks) < 3:
        raise ValueError("Pinned constituents table is incomplete")
    headers = [line for block in blocks for line in block.splitlines() if line.startswith("!")]
    if headers != _HEADERS:
        raise ValueError("Pinned constituents table columns differ")
    members: list[dict[str, str]] = []
    symbols: set[str] = set()
    for block in blocks[1:]:
        lines = [line.strip() for line in block.strip().splitlines() if line.strip()]
        if not lines or all(line.startswith("!") for line in lines):
            continue
        if any(not line.startswith("|") for line in lines):
            raise ValueError("Candidate row syntax is ambiguous")
        cells = [cell.strip() for line in lines for cell in line[1:].split("||")]
        if len(cells) != 9:
            raise ValueError("Candidate row columns differ")
        # The historical source annotates two dotted tickers with HTML comments.
        # Comments are not part of the symbol; unmatched markup still fails.
        ticker = _TICKER.fullmatch(re.sub(r"<!--.*?-->", "", cells[0], flags=re.DOTALL).strip())
        if (
            ticker is None
            or re.fullmatch(r"[0-9]{1,10}", cells[7]) is None
            or int(cells[7]) == 0
            or not 0 < len(cells[1]) <= 512
            or any("\x00" in cell for cell in cells)
        ):
            raise ValueError("Candidate ticker, issuer or security label is invalid")
        symbol, cik = ticker[2], cells[7].zfill(10)
        if symbol in symbols:
            raise ValueError("Candidate snapshot contains a duplicate symbol")
        symbols.add(symbol)
        key = hashlib.sha256(canonical_bytes([policy["revision_id"], cik, symbol])).hexdigest()
        members.append(
            {
                "snapshot_member_key": key,
                "cik": cik,
                "symbol_at_snapshot": symbol,
                "source_security_label": cells[1],
                "source_symbol_template": ticker[1],
            }
        )
    if (
        len(members) != policy["expected_candidates"]
        or len({member["cik"] for member in members}) != policy["expected_issuers"]
    ):
        raise ValueError("Historical candidate/issuer counts differ from the pinned source")
    return sorted(members, key=lambda member: member["symbol_at_snapshot"])


def build_roster(response: bytes, policy: dict[str, Any]) -> bytes:
    validate_policy(policy)
    if not 0 < len(response) <= MAX_RESPONSE_BYTES:
        raise ValueError("Historical revision response exceeds its byte bound")
    value = decode_json(response)
    if not isinstance(value, dict) or "error" in value:
        raise ValueError("Historical revision API did not provide a source")
    pages = value.get("query", {}).get("pages", {})
    if not isinstance(pages, dict) or set(pages) != {str(policy["page_id"])}:
        raise ValueError("Historical revision page identity differs")
    page = pages[str(policy["page_id"])]
    if (
        type(page.get("pageid")) is not int
        or page["pageid"] != policy["page_id"]
        or page.get("title") != policy["page_title"]
        or not isinstance(page.get("revisions"), list)
        or len(page["revisions"]) != 1
    ):
        raise ValueError("Historical revision identity is ambiguous")
    revision = page["revisions"][0]
    if (
        type(revision.get("revid")) is not int
        or revision["revid"] != policy["revision_id"]
        or revision.get("timestamp") != policy["revision_timestamp"]
        or not _utc(revision["timestamp"]) < _utc(policy["first_validation_start"])
    ):
        raise ValueError("Historical revision identity or server chronology differs")
    slot = revision.get("slots", {}).get("main", {})
    text = slot.get("*")
    if not isinstance(text, str) or slot.get("contentmodel") != "wikitext":
        raise ValueError("Historical revision content is unavailable")
    encoded = text.encode("utf-8")
    if (
        hashlib.sha256(encoded).hexdigest() != policy["wikitext_sha256"]
        or revision.get("sha1") != hashlib.sha1(encoded).hexdigest()
    ):
        raise ValueError("Historical revision content digest differs")
    result = {
        "schema_version": 1,
        "status": STATUS,
        "cohort_id": policy["cohort_id"],
        "source_revision_id": policy["revision_id"],
        "source_revision_timestamp": policy["revision_timestamp"],
        "source_wikitext_sha256": policy["wikitext_sha256"],
        "policy_sha256": hashlib.sha256(canonical_bytes(policy)).hexdigest(),
        "later_status_rule": policy["later_status_rule"],
        "members": parse_members(text, policy),
    }
    return canonical_bytes(result) + b"\n"


def commitment(response: bytes, policy: dict[str, Any], observed_at: datetime) -> dict[str, Any]:
    if observed_at.utcoffset() is None or observed_at <= _utc(policy["revision_timestamp"]):
        raise ValueError("Local retrieval time must follow the historical revision")
    roster = build_roster(response, policy)
    return {
        "schema_version": 1,
        "status": STATUS,
        "cohort_id": policy["cohort_id"],
        "source_revision_id": policy["revision_id"],
        "source_revision_timestamp": policy["revision_timestamp"],
        "retrieved_at": observed_at.astimezone(UTC).isoformat(),
        "source_response_sha256": hashlib.sha256(response).hexdigest(),
        "source_wikitext_sha256": policy["wikitext_sha256"],
        "policy_sha256": hashlib.sha256(canonical_bytes(policy)).hexdigest(),
        "roster_sha256": hashlib.sha256(roster).hexdigest(),
        "candidate_count": policy["expected_candidates"],
        "issuer_count": policy["expected_issuers"],
        "first_validation_start": policy["first_validation_start"],
        "market_provider_requests": 0,
        "issue_complete": False,
        "security_identifier_continuity_verified": False,
        "checkpoint_dataset_binding_verified": False,
    }


def fetch_revision(client: httpx.Client, policy: dict[str, Any]) -> bytes:
    validate_policy(policy)
    params = {
        "action": "query",
        "prop": "revisions",
        "revids": str(policy["revision_id"]),
        "rvprop": "ids|timestamp|content|sha1",
        "rvslots": "main",
        "format": "json",
    }
    with client.stream("GET", API, params=params) as response:
        response.raise_for_status()
        payload = bytearray()
        for chunk in response.iter_bytes():
            payload.extend(chunk)
            if len(payload) > MAX_RESPONSE_BYTES:
                raise ValueError("Historical revision response exceeds its byte bound")
    return bytes(payload)


def save_capture(
    response: bytes,
    policy: dict[str, Any],
    directory: Path,
    *,
    observed_at: datetime,
    root: Path = ARTIFACT_ROOT,
) -> dict[str, Any]:
    report = commitment(response, policy, observed_at)
    roster = build_roster(response, policy)
    _preflight_new_capture(directory, root=root)
    with _capture_directory(directory, root=root, create=True) as fd:
        _write_new(fd, "revision.json", response)
        _write_new(fd, "candidate-roster.json", roster)
        _write_new(fd, "commitment.json", canonical_bytes(report) + b"\n")
    return report


def verify_capture(
    directory: Path, policy: dict[str, Any], *, root: Path = ARTIFACT_ROOT
) -> dict[str, Any]:
    with _capture_directory(directory, root=root) as fd:
        response = _read_private_file(fd, "revision.json", MAX_RESPONSE_BYTES)
        roster = _read_private_file(fd, "candidate-roster.json", MAX_ROSTER_BYTES)
        raw = _read_private_file(fd, "commitment.json", 16384)
    report = decode_json(raw)
    expected = commitment(response, policy, datetime.fromisoformat(report["retrieved_at"]))
    if canonical_bytes(report) != canonical_bytes(expected) or roster != build_roster(
        response, policy
    ):
        raise ValueError("Historical candidate capture differs from its bound source")
    return expected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--verify", action="store_true")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        policy = load_policy()
        if args.verify:
            report = verify_capture(args.output_dir, policy)
        else:
            _preflight_new_capture(args.output_dir, root=ARTIFACT_ROOT)
            with httpx.Client(
                timeout=30,
                follow_redirects=False,
                headers={
                    "User-Agent": "edgar-moe-source-feasibility/1.0 (https://github.com/hoangnguyen2003/edgar-moe)"
                },
            ) as client:
                response = fetch_revision(client, policy)
            report = save_capture(response, policy, args.output_dir, observed_at=datetime.now(UTC))
    except (
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        OSError,
        RuntimeError,
        httpx.HTTPError,
    ) as error:
        status = (
            f" HTTP {error.response.status_code}"
            if isinstance(error, httpx.HTTPStatusError)
            else ""
        )
        print(
            f"Historical candidate source refused: {type(error).__name__}{status}", file=sys.stderr
        )
        return 1
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
