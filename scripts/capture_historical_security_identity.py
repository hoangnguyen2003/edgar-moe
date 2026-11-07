"""Collect/replay dated SEC identity anchors; never create a research universe.

All 503 source members stay in the denominator, including failed anchors.
Live mode is an identified, paced, bounded SEC-only read. Current tickers,
listing status, market prices and asset directories are never consulted.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from edgar_moe.data.historical_identity import (
    MAX_DOCUMENT_BYTES,
    PUBLICATION_LAG_DAYS,
    IdentityEvidenceError,
    instant,
    join_identity,
    ownership_identity,
    ownership_subject,
    periodic_anchor,
)
from edgar_moe.data.screen_inputs import canonical_bytes, decode_json
from edgar_moe.settings import RuntimeSettings
from scripts.capture_historical_candidate_roster import load_policy, verify_capture
from scripts.capture_prospective_sec_index import (
    _capture_directory,
    _preflight_new_capture,
    _read_private_file,
    _write_new,
)

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = ROOT / "data/artifacts"
RETAINED_MANIFEST_SHA256 = "5d5aa4c9cb67975cad21bdd0a9caf5763ac81b0c31a019ca6edd9f576c8b9603"
PERIODIC_FORMS = {"10-K", "10-Q"}
OWNERSHIP_FORMS = {"SC 13G", "SC 13G/A", "SC 13D", "SC 13D/A"}
_SOURCE_URL = re.compile(
    r"https://(?:data\.sec\.gov/submissions/CIK[0-9]{10}(?:-submissions-[0-9]{3})?\.json"
    r"|www\.sec\.gov/Archives/edgar/data/[1-9][0-9]*/[0-9]{18}/[A-Za-z0-9_.-]+\.(?:htm|html|txt))"
)


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def code_identity() -> dict[str, str]:
    return {
        "identity_algorithm_sha256": digest(
            (ROOT / "src/edgar_moe/data/historical_identity.py").read_bytes()
        ),
        "collector_algorithm_sha256": digest(Path(__file__).read_bytes()),
    }


def expand_columns(value: Any, cik: str) -> list[dict[str, Any]]:
    """Only filing records are read; present-day ticker/name/status fields are ignored."""
    if not isinstance(value, dict) or not isinstance(value.get("accessionNumber"), list):
        raise IdentityEvidenceError("invalid_submissions_columns")
    count = len(value["accessionNumber"])
    required = {"accessionNumber", "form", "acceptanceDateTime", "primaryDocument"}
    if not required <= value.keys() or any(
        not isinstance(value[key], list) or len(value[key]) != count for key in required
    ):
        raise IdentityEvidenceError("inconsistent_submissions_columns")
    if count > 1000000:
        raise IdentityEvidenceError("excessive_submissions_rows")
    eligible = [
        i for i, form in enumerate(value["form"]) if form in PERIODIC_FORMS | OWNERSHIP_FORMS
    ]
    if len(eligible) > 100000:
        raise IdentityEvidenceError("excessive_eligible_submissions_rows")
    return [{"cik": cik, **{key: value[key][i] for key in required}} for i in eligible]


def choose_filings(
    rows: list[dict[str, Any]], forms: set[str], cutoff: datetime, limit: int
) -> list[dict[str, Any]]:
    available: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("form") not in forms:
            continue
        accepted = instant(row.get("acceptanceDateTime"))
        if (
            not cutoff - timedelta(days=730)
            <= accepted
            <= cutoff - timedelta(days=PUBLICATION_LAG_DAYS)
        ):
            continue
        accession = row.get("accessionNumber")
        if (
            not isinstance(accession, str)
            or re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", accession) is None
        ):
            raise IdentityEvidenceError("invalid_accession")
        if accession in available and canonical_bytes(row) != canonical_bytes(available[accession]):
            raise IdentityEvidenceError("conflicting_accession_metadata")
        available[accession] = row
    return sorted(
        available.values(),
        key=lambda row: (instant(row["acceptanceDateTime"]), row["accessionNumber"]),
        reverse=True,
    )[:limit]


def filing_url(row: dict[str, Any]) -> str:
    cik, accession, document = row["cik"], row["accessionNumber"], row["primaryDocument"]
    if not isinstance(cik, str) or re.fullmatch(r"[0-9]{10}", cik) is None or int(cik) == 0:
        raise IdentityEvidenceError("invalid_filing_cik")
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{document}"
    if _SOURCE_URL.fullmatch(url) is None:
        raise IdentityEvidenceError("invalid_SEC_archive_url")
    return url


class SourceCache:
    """Exclusive private source pairs, byte ceilings, no redirects/retries."""

    def __init__(self, directory_fd: int, client: httpx.Client | None, max_requests: int):
        if not 1 <= max_requests <= 2000:
            raise ValueError("invalid_request_budget")
        self.fd, self.client, self.max_requests = directory_fd, client, max_requests
        self.requests = 0
        self.bytes = 0
        self.last_request = 0.0
        self.used: dict[str, dict[str, Any]] = {}
        self.deferred: dict[str, dict[str, Any]] = {}
        self.consecutive_failures = 0

    def get(self, url: str) -> bytes:
        key = digest(url.encode())
        if key in self.deferred:
            raise IdentityEvidenceError("SEC_source_deferred_this_run")
        if self.consecutive_failures >= 3:
            raise IdentityEvidenceError("SEC_transient_circuit_open")
        previous_requests = self.requests
        try:
            payload = self._get(url)
        except (httpx.HTTPStatusError, httpx.TimeoutException) as error:
            status = (
                error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
            )
            if status is not None and status not in {500, 502, 503, 504}:
                raise
            self.consecutive_failures += 1
            if isinstance(error, httpx.HTTPStatusError) and error.response.headers.get(
                "Retry-After"
            ):
                self.consecutive_failures = 3
            receipt = {
                "url": url,
                "source_url_sha256": key,
                "http_status": status,
                "timeout": status is None,
                "observed_at": datetime.now(UTC).isoformat(),
                "response_body_retained": False,
                "automatically_retried": False,
            }
            self.deferred[key] = receipt
            encoded = canonical_bytes(receipt) + b"\n"
            _write_new(self.fd, "unavailable-" + digest(encoded) + ".json", encoded)
            raise IdentityEvidenceError("SEC_source_temporarily_unavailable") from None
        if self.requests > previous_requests:
            self.consecutive_failures = 0
        return payload

    def _get(self, url: str) -> bytes:
        if _SOURCE_URL.fullmatch(url) is None:
            raise IdentityEvidenceError("unapproved_source_URL")
        key = digest(url.encode())
        try:
            meta = decode_json(_read_private_file(self.fd, key + ".json", 4096))
        except FileNotFoundError:
            try:
                os.stat(key + ".body", dir_fd=self.fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise IdentityEvidenceError("incomplete_SEC_source_cache_pair") from None
            if self.client is None:
                raise IdentityEvidenceError("uncaptured_SEC_source") from None
            if self.requests >= self.max_requests or self.bytes >= 1024**3:
                raise IdentityEvidenceError("source_request_budget_exhausted") from None
            time.sleep(max(0.0, 0.5 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            self.requests += 1
            with self.client.stream("GET", url) as response:
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    self.bytes += len(chunk)
                    if self.bytes > 1024**3:
                        raise IdentityEvidenceError("source_response_budget_exhausted") from None
                    if len(body) > MAX_DOCUMENT_BYTES:
                        meta = {
                            "url": url,
                            "refusal": "source_document_byte_bound",
                            "retrieved_at": datetime.now(UTC).isoformat(),
                        }
                        _write_new(self.fd, key + ".json", canonical_bytes(meta) + b"\n")
                        self.used[key] = meta
                        raise IdentityEvidenceError("source_document_byte_bound") from None
            payload = bytes(body)
            if not payload:
                raise IdentityEvidenceError("empty_SEC_source") from None
            meta = {
                "url": url,
                "sha256": digest(payload),
                "bytes": len(payload),
                "retrieved_at": datetime.now(UTC).isoformat(),
            }
            _write_new(self.fd, key + ".body", payload)
            _write_new(self.fd, key + ".json", canonical_bytes(meta) + b"\n")
        if meta.get("url") == url and meta.get("refusal") == "source_document_byte_bound":
            instant(meta.get("retrieved_at"))
            self.used[key] = meta
            raise IdentityEvidenceError("source_document_byte_bound")
        payload = _read_private_file(self.fd, key + ".body", MAX_DOCUMENT_BYTES)
        if (
            meta.get("url") != url
            or meta.get("sha256") != digest(payload)
            or meta.get("bytes") != len(payload)
        ):
            raise IdentityEvidenceError("SEC_source_cache_tampered")
        instant(meta.get("retrieved_at"))
        self.used[key] = meta
        return payload


def unpack(payload: bytes, maximum: int | None = None) -> bytes:
    maximum = MAX_DOCUMENT_BYTES if maximum is None else maximum
    with gzip.GzipFile(fileobj=io.BytesIO(payload)) as stream:
        result = stream.read(maximum + 1)
    if not result or len(result) > maximum:
        raise IdentityEvidenceError("retained_source_too_large")
    return result


class RetainedCheckpoint:
    """Reuse only sources committed by the original immutable manifest."""

    def __init__(self, directory: Path):
        self.directory = directory
        payload = (directory / "manifest.json").read_bytes()
        if digest(payload) != RETAINED_MANIFEST_SHA256:
            raise IdentityEvidenceError("retained_checkpoint_manifest_mismatch")
        self.hashes = decode_json(payload)["hashes"]
        index = self.read("sec/filing-index.json", "filing_index", compressed=False)
        self.filings = {row["accessionNumber"]: row for row in decode_json(index)}

    def read(
        self, relative: str, key: str, *, compressed: bool = True, maximum: int = MAX_DOCUMENT_BYTES
    ) -> bytes:
        path = self.directory / relative
        if (
            not path.is_file()
            or path.is_symlink()
            or not path.resolve().is_relative_to(self.directory.resolve())
        ):
            raise IdentityEvidenceError("retained_source_path_invalid")
        if path.stat().st_size > 32 * 1024**2:
            raise IdentityEvidenceError("retained_source_too_large")
        body = path.read_bytes()
        if digest(body) != self.hashes.get(key):
            raise IdentityEvidenceError("retained_source_digest_mismatch")
        return unpack(body, maximum) if compressed else body

    def submissions(self, cik: str) -> bytes | None:
        key = "submissions_" + cik
        if key not in self.hashes:
            return None
        return self.read(f"sec/CIK{cik}-submissions.json.gz", key, maximum=64 * 1024**2)

    def filing(self, row: dict[str, Any]) -> bytes | None:
        record = self.filings.get(row["accessionNumber"])
        if record is None or record.get("status") != "ok":
            return None
        if record.get("cik") != row["cik"]:
            raise IdentityEvidenceError("retained_document_issuer_mismatch")
        if any(
            record.get(field) != row.get(field) for field in ("form", "primaryDocument")
        ) or instant(record.get("acceptanceDateTime")) != instant(row.get("acceptanceDateTime")):
            raise IdentityEvidenceError("retained_document_metadata_mismatch")
        # Filings are committed individually by the hash-bound filing index.
        relative = record["localPath"]
        path = Path(relative)
        if path.is_absolute() or not relative.startswith("sec/filings/"):
            try:
                relative = str(path.relative_to(self.directory))
            except ValueError:
                # Original checkpoint paths may refer to another checkout.
                marker = "sec/filings/"
                if marker not in relative:
                    raise IdentityEvidenceError("retained_source_path_invalid") from None
                relative = marker + relative.split(marker)[1]
        key = "filing:" + row["accessionNumber"]
        self.hashes[key] = record["sha256"]
        return self.read(relative, key)


def submissions(
    cik: str, cache: SourceCache, retained: RetainedCheckpoint, cutoff: datetime
) -> list[dict[str, Any]]:
    body = retained.submissions(cik)
    if body is None:
        body = cache.get(f"https://data.sec.gov/submissions/CIK{cik}.json")
        payload = decode_json(body)
        if str(payload.get("cik", "")).zfill(10) != cik:
            raise IdentityEvidenceError("SEC_submissions_issuer_mismatch")
        rows = expand_columns(payload["filings"]["recent"], cik)
        for descriptor in payload["filings"].get("files", []):
            # Discover archive pages by their advertised coverage, not current listing status.
            if (
                descriptor["filingFrom"] > cutoff.date().isoformat()
                or descriptor["filingTo"] < (cutoff - timedelta(days=730)).date().isoformat()
            ):
                continue
            name = descriptor["name"]
            if re.fullmatch(rf"CIK{cik}-submissions-[0-9]{{3}}\.json", name) is None:
                raise IdentityEvidenceError("invalid_submission_archive_descriptor")
            rows.extend(
                expand_columns(
                    decode_json(cache.get(f"https://data.sec.gov/submissions/{name}")), cik
                )
            )
        return rows
    payload = decode_json(body)
    if str(payload.get("cik", "")).zfill(10) != cik:
        raise IdentityEvidenceError("retained_submissions_issuer_mismatch")
    return expand_columns(payload["filings"]["recent"], cik)


def collect_member(
    member: dict[str, Any], cache: SourceCache, retained: RetainedCheckpoint, cutoff: datetime
) -> dict[str, Any]:
    cik, symbol = member["cik"], member["symbol_at_snapshot"]
    rows = submissions(cik, cache, retained, cutoff)
    periodic = None
    refusals: Counter[str] = Counter()
    for row in choose_filings(rows, PERIODIC_FORMS, cutoff, 4):
        body = retained.filing(row)
        if body is None:
            body = cache.get(filing_url(row))
        try:
            periodic = periodic_anchor(body, row, cik=cik, symbol=symbol, cutoff=cutoff)
            break
        except IdentityEvidenceError as error:
            refusals[str(error)] += 1
    if periodic is None:
        return {
            "snapshot_member_key": member["snapshot_member_key"],
            "status": "periodic_anchor_unresolved",
            "refusals": dict(refusals),
        }
    for row in choose_filings(rows, OWNERSHIP_FORMS, cutoff, 5):
        body = cache.get(filing_url({**row, "primaryDocument": row["accessionNumber"] + ".txt"}))
        try:
            subject = ownership_subject(body, cik=cik, accession=row["accessionNumber"])
            result = join_identity(periodic, ownership_identity(body, row, cik=cik, cutoff=cutoff))
            result["ownership_subject_evidence"] = subject
            return {
                "snapshot_member_key": member["snapshot_member_key"],
                "status": "pre_cutoff_SEC_identity_anchored",
                "anchor": result,
            }
        except IdentityEvidenceError as error:
            refusals[str(error)] += 1
    return {
        "snapshot_member_key": member["snapshot_member_key"],
        "status": "ownership_identity_unresolved",
        "refusals": dict(refusals),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--source-capture", required=True, type=Path)
    parser.add_argument("--retained-checkpoint", required=True, type=Path)
    parser.add_argument("--settings-root", type=Path, default=ROOT)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--limit", type=int, default=503)
    parser.add_argument("--max-requests", type=int, default=96)
    args = parser.parse_args()
    client = None
    try:
        if not 1 <= args.limit <= 503 or not 1 <= args.max_requests <= 2000:
            raise ValueError("invalid_collection_bounds")
        policy = load_policy()
        algorithms = code_identity()
        source = verify_capture(args.source_capture, policy, root=args.source_capture.parent)
        with _capture_directory(args.source_capture, root=args.source_capture.parent) as fd:
            roster = decode_json(_read_private_file(fd, "candidate-roster.json", 1024**2))
        # Full source always determines the denominator. A bounded run is explicitly partial.
        ordered = sorted(
            roster["members"],
            key=lambda m: digest(
                ("issue292-security-anchor-v1|" + m["snapshot_member_key"]).encode()
            ),
        )
        retained = RetainedCheckpoint(args.retained_checkpoint)
        binding = {
            "source_roster_sha256": source["roster_sha256"],
            "retained_manifest_sha256": RETAINED_MANIFEST_SHA256,
            "cutoff": policy["revision_timestamp"],
            "members": ordered[: args.limit],
            "source_count": len(ordered),
        }
        if not args.resume:
            _preflight_new_capture(args.output_dir, root=ARTIFACT_ROOT)
        with _capture_directory(args.output_dir, root=ARTIFACT_ROOT, create=not args.resume) as fd:
            if args.resume:
                if (
                    _read_private_file(fd, "binding.json", 1024**2)
                    != canonical_bytes(binding) + b"\n"
                ):
                    raise IdentityEvidenceError("capture_binding_mismatch")
            else:
                _write_new(fd, "binding.json", canonical_bytes(binding) + b"\n")
            if args.live:
                settings = RuntimeSettings(  # type: ignore[call-arg]
                    _env_file=(args.settings_root / ".env", args.settings_root / ".env.local")
                )
                contact = re.search(
                    r"[A-Za-z0-9_.+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})", settings.sec_user_agent
                )
                if contact is None or contact[1].lower().split(".")[-2:] == ["example", "com"]:
                    raise ValueError("identified_SEC_contact_required")
                client = httpx.Client(
                    headers={"User-Agent": settings.sec_user_agent},
                    timeout=30,
                    follow_redirects=False,
                )
            cache = SourceCache(fd, client, args.max_requests)
            results = []
            for index, member in enumerate(binding["members"]):
                try:
                    result = collect_member(member, cache, retained, instant(binding["cutoff"]))
                except IdentityEvidenceError as error:
                    if str(error) not in {
                        "source_document_byte_bound",
                        "retained_source_too_large",
                        "SEC_source_temporarily_unavailable",
                        "SEC_source_deferred_this_run",
                    }:
                        raise
                    result = {
                        "snapshot_member_key": member["snapshot_member_key"],
                        "status": "source_temporarily_unavailable"
                        if str(error).startswith("SEC_source_")
                        else "source_size_unresolved",
                        "refusals": {str(error): 1},
                    }
                results.append(result)
                # Persist completed members before another provider request. Never
                # trust these as a cache: resume always recomputes from sources.
                receipt = (
                    canonical_bytes(
                        {
                            "binding_sha256": digest(canonical_bytes(binding)),
                            "algorithms": algorithms,
                            "result": result,
                        }
                    )
                    + b"\n"
                )
                name = "member-" + digest(receipt) + ".json"
                try:
                    _write_new(fd, name, receipt)
                except FileExistsError:
                    if _read_private_file(fd, name, 1024**2) != receipt:
                        raise IdentityEvidenceError("member_receipt_changed") from None
                print(
                    json.dumps(
                        {
                            "processed": index + 1,
                            "planned": args.limit,
                            "requests": cache.requests,
                            "status": results[-1]["status"],
                        }
                    ),
                    flush=True,
                )
                if cache.consecutive_failures >= 3:
                    raise IdentityEvidenceError("SEC_transient_circuit_open")
            if algorithms != code_identity():
                raise IdentityEvidenceError("algorithms_changed_during_capture")
            report = {
                "schema_version": 1,
                "source_roster_sha256": source["roster_sha256"],
                "source_count": len(ordered),
                "processed": len(results),
                "statuses": dict(Counter(row["status"] for row in results)),
                "SEC_requests_this_run": cache.requests,
                "deferred_sources_this_run": len(cache.deferred),
                "current_asset_directory_used": False,
                "market_provider_requests": 0,
                "issue_complete": False,
                "historical_market_continuity_verified": False,
                "checkpoint_dataset_binding_verified": False,
                **algorithms,
            }
            result = {
                "binding": binding,
                "results": results,
                "live_sources": cache.used,
                "unavailable_sources": cache.deferred,
                "report": report,
            }
            encoded = canonical_bytes(result) + b"\n"
            _write_new(fd, "result-" + digest(encoded) + ".json", encoded)
            print(json.dumps({**report, "result_sha256": digest(encoded)}, sort_keys=True))
        return 0
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, httpx.HTTPError) as error:
        status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
        print(
            json.dumps(
                {
                    "refused": type(error).__name__,
                    "code": str(error) if isinstance(error, IdentityEvidenceError) else None,
                    "http_status": status,
                }
            ),
            file=sys.stderr,
        )
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
