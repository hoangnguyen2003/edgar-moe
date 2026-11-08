"""Offline replay of a complete or explicitly bounded SEC identity result.

No HTTP client is created. Recorded source failures can reproduce unresolved
rows, but can never supply an identity or promote a completion flag.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from edgar_moe.data.historical_identity import IdentityEvidenceError, instant
from edgar_moe.data.screen_inputs import canonical_bytes, decode_json
from scripts.capture_historical_candidate_roster import load_policy, verify_capture
from scripts.capture_historical_security_identity import (
    ARTIFACT_ROOT,
    RETAINED_MANIFEST_SHA256,
    RetainedCheckpoint,
    SourceCache,
    code_identity,
    collect_member,
    digest,
)
from scripts.capture_prospective_sec_index import _capture_directory, _read_private_file


class ReplayCache(SourceCache):
    def __init__(self, directory_fd: int, unavailable: dict[str, Any]):
        super().__init__(directory_fd, None, 2000)
        self.recorded_unavailable = unavailable

    def get(self, url: str) -> bytes:
        key = digest(url.encode())
        if key not in self.recorded_unavailable:
            return super().get(url)
        if key in self.deferred:
            raise IdentityEvidenceError("SEC_source_deferred_this_run")
        receipt = self.recorded_unavailable[key]
        if (
            not isinstance(receipt, dict)
            or set(receipt)
            != {
                "url",
                "source_url_sha256",
                "http_status",
                "timeout",
                "observed_at",
                "response_body_retained",
                "automatically_retried",
            }
            or receipt["url"] != url
            or receipt["source_url_sha256"] != key
            or receipt["response_body_retained"] is not False
            or receipt["automatically_retried"] is not False
            or not (
                (
                    type(receipt["http_status"]) is int
                    and receipt["http_status"] in {500, 502, 503, 504}
                    and receipt["timeout"] is False
                )
                or (receipt["http_status"] is None and receipt["timeout"] is True)
            )
        ):
            raise IdentityEvidenceError("invalid_recorded_source_failure")
        instant(receipt["observed_at"])
        encoded = canonical_bytes(receipt) + b"\n"
        if _read_private_file(self.fd, "unavailable-" + digest(encoded) + ".json", 8192) != encoded:
            raise IdentityEvidenceError("source_failure_receipt_changed")
        self.deferred[key] = receipt
        raise IdentityEvidenceError("SEC_source_temporarily_unavailable")


def replay(
    directory: Path, result_name: str, source_capture: Path, retained_checkpoint: Path
) -> dict[str, Any]:
    match = re.fullmatch(r"result-([a-f0-9]{64})\.json", result_name)
    if match is None:
        raise IdentityEvidenceError("invalid_result_filename")
    algorithms = code_identity()
    verifier_digest = digest(Path(__file__).read_bytes())
    policy = load_policy()
    source = verify_capture(source_capture, policy, root=source_capture.parent)
    with _capture_directory(source_capture, root=source_capture.parent) as fd:
        roster = decode_json(_read_private_file(fd, "candidate-roster.json", 1024**2))
    ordered = sorted(
        roster["members"],
        key=lambda m: digest(("issue292-security-anchor-v1|" + m["snapshot_member_key"]).encode()),
    )
    retained = RetainedCheckpoint(retained_checkpoint)
    with _capture_directory(directory, root=ARTIFACT_ROOT) as fd:
        raw = _read_private_file(fd, result_name, 32 * 1024**2)
        if digest(raw) != match[1]:
            raise IdentityEvidenceError("result_digest_mismatch")
        value = decode_json(raw)
        results = value["results"]
        if not isinstance(results, list) or not 1 <= len(results) <= len(ordered):
            raise IdentityEvidenceError("invalid_result_count")
        expected_binding = {
            "source_roster_sha256": source["roster_sha256"],
            "retained_manifest_sha256": RETAINED_MANIFEST_SHA256,
            "cutoff": policy["revision_timestamp"],
            "members": ordered[: len(results)],
            "source_count": len(ordered),
        }
        if (
            canonical_bytes(value["binding"]) != canonical_bytes(expected_binding)
            or _read_private_file(fd, "binding.json", 1024**2)
            != canonical_bytes(expected_binding) + b"\n"
        ):
            raise IdentityEvidenceError("result_source_binding_mismatch")
        unavailable = value.get("unavailable_sources", {})
        if not isinstance(unavailable, dict):
            raise IdentityEvidenceError("invalid_unavailable_source_set")
        cache = ReplayCache(fd, unavailable)
        recomputed = []
        for member in expected_binding["members"]:
            try:
                result = collect_member(
                    member, cache, retained, instant(expected_binding["cutoff"])
                )
            except IdentityEvidenceError as error:
                if str(error) not in {
                    "SEC_source_temporarily_unavailable",
                    "SEC_source_deferred_this_run",
                    "source_document_byte_bound",
                    "retained_source_too_large",
                }:
                    raise
                result = {
                    "snapshot_member_key": member["snapshot_member_key"],
                    "status": "source_temporarily_unavailable"
                    if str(error).startswith("SEC_source_")
                    else "source_size_unresolved",
                    "refusals": {str(error): 1},
                }
            recomputed.append(result)
        if canonical_bytes(recomputed) != canonical_bytes(results):
            raise IdentityEvidenceError("identity_result_replay_mismatch")
        if canonical_bytes(cache.used) != canonical_bytes(value["live_sources"]) or canonical_bytes(
            cache.deferred
        ) != canonical_bytes(unavailable):
            raise IdentityEvidenceError("result_source_receipt_set_mismatch")
        report = value["report"]
        statuses = dict(Counter(row["status"] for row in recomputed))
        if (
            any(
                report.get(key) is not False
                for key in (
                    "issue_complete",
                    "historical_market_continuity_verified",
                    "checkpoint_dataset_binding_verified",
                    "current_asset_directory_used",
                )
            )
            or report.get("source_roster_sha256") != source["roster_sha256"]
            or canonical_bytes(report.get("statuses")) != canonical_bytes(statuses)
            or type(report.get("processed")) is not int
            or report["processed"] != len(results)
            or type(report.get("source_count")) is not int
            or report["source_count"] != len(ordered)
        ):
            raise IdentityEvidenceError("invalid_identity_result_report")
    if code_identity() != algorithms or digest(Path(__file__).read_bytes()) != verifier_digest:
        raise IdentityEvidenceError("replay_algorithm_changed_during_verification")
    return {
        "status": "offline_source_replay_verified",
        "result_sha256": digest(raw),
        "source_count": len(ordered),
        "processed": len(results),
        "statuses": statuses,
        "provider_requests": 0,
        "issue_complete": False,
        "capture_identity_algorithm_sha256": report["identity_algorithm_sha256"],
        "capture_collector_algorithm_sha256": report["collector_algorithm_sha256"],
        "capture_certificate_algorithm_sha256": report.get("certificate_algorithm_sha256"),
        "replay_algorithms": algorithms,
        "verifier_sha256": verifier_digest,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--result", required=True)
    parser.add_argument("--source-capture", required=True, type=Path)
    parser.add_argument("--retained-checkpoint", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = replay(args.directory, args.result, args.source_capture, args.retained_checkpoint)
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
        print(json.dumps({"refused": type(error).__name__}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
