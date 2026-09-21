"""Build a redacted readiness report for the anonymous public release surface."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ISO_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
_DISCLAIMER = (
    "Public release readiness is a repository contract, not legal advice or provider approval."
)
_CHECK_IDS = ("public_bundle", "snapshot_lock", "redistribution_review")
_REPORT_KEYS = frozenset(
    {
        "blocked_reasons",
        "checks",
        "disclaimer",
        "generated_at",
        "identity",
        "readiness_sha256",
        "research_only",
        "review_reasons",
        "review",
        "schema_version",
        "scope",
        "status",
    }
)


class PublicReleaseReadinessError(ValueError):
    """Raised when a readiness report is malformed or internally inconsistent."""


def build_public_release_readiness(
    bundle_root: Path = Path("public"),
    lock_path: Path = Path("config/public_snapshot.lock.json"),
    *,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Build a safe report without publishing source data or provider values.

    The existing validators remain the source of truth for structural checks. This
    report only composes their redacted error categories with the provenance review
    state, so a pending legal/provider review cannot be mistaken for a clean release.
    """

    root = (repo_root or Path.cwd()).resolve()
    bundle = _resolve_under_root(root, bundle_root)
    lock = _resolve_under_root(root, lock_path)
    bundle_validator = _load_sibling("validate_public_bundle.py", "public_bundle_validator")
    lock_validator = _load_sibling("verify_public_snapshot_lock.py", "public_snapshot_validator")
    bundle_errors = bundle_validator.validate_public_bundle(bundle)
    lock_errors = lock_validator.validate_public_snapshot_lock(lock, repo_root=root)

    manifest = _read_json_object(bundle / "data-provenance.json")
    review = _review_state(manifest)
    checks = [
        {
            "id": "public_bundle",
            "status": "passed" if not bundle_errors else "failed",
            "error_codes": [_error_code(error) for error in bundle_errors],
        },
        {
            "id": "snapshot_lock",
            "status": "passed" if not lock_errors else "failed",
            "error_codes": [_error_code(error) for error in lock_errors],
        },
        {
            "id": "redistribution_review",
            "status": review["check_status"],
            "error_codes": list(review["error_codes"]),
        },
    ]

    blocked_reasons: list[str] = []
    if bundle_errors:
        blocked_reasons.append("public_bundle_contract_failed")
    if lock_errors:
        blocked_reasons.append("snapshot_lock_contract_failed")
    if review["check_status"] == "failed":
        blocked_reasons.append("redistribution_contract_invalid")
    review_reasons: list[str] = []
    if review["check_status"] == "review_required":
        if review["redistribution_status"] != "approved":
            review_reasons.append("redistribution_review_pending")
        if review["source_review_required"]:
            review_reasons.append("source_terms_review_pending")

    status = "blocked" if blocked_reasons else "review_required" if review_reasons else "ready"
    snapshot = manifest.get("snapshot") if isinstance(manifest, dict) else None
    identity = {
        "path": snapshot.get("path") if isinstance(snapshot, dict) else None,
        "sha256": snapshot.get("sha256") if isinstance(snapshot, dict) else None,
    }
    report: dict[str, Any] = {
        "schema_version": 1,
        "scope": "public_release_readiness",
        "research_only": True,
        "status": status,
        "checks": checks,
        "blocked_reasons": blocked_reasons,
        "review_reasons": review_reasons,
        "review": {
            "status": review["review_status"],
            "redistribution_status": review["redistribution_status"],
            "legal_approval": review["legal_approval"],
            "source_review_required": review["source_review_required"],
            "source_approved": review["source_approved"],
        },
        "identity": identity,
        "disclaimer": _DISCLAIMER,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    report["readiness_sha256"] = _content_hash(report)
    verify_public_release_readiness(report)
    return report


def verify_public_release_readiness(report: Mapping[str, Any]) -> None:
    """Verify a saved report without reopening the public bundle or source data."""

    if not isinstance(report, Mapping):
        raise PublicReleaseReadinessError("public release readiness must be a JSON object")
    unknown = sorted(str(key) for key in report if key not in _REPORT_KEYS)
    if unknown:
        raise PublicReleaseReadinessError(
            "public release readiness contains unknown fields: " + ", ".join(unknown)
        )
    missing = sorted(key for key in _REPORT_KEYS if key not in report)
    if missing:
        raise PublicReleaseReadinessError(
            "public release readiness is missing fields: " + ", ".join(missing)
        )
    if report.get("schema_version") != 1 or report.get("scope") != "public_release_readiness":
        raise PublicReleaseReadinessError("public release readiness schema or scope is invalid")
    if report.get("research_only") is not True:
        raise PublicReleaseReadinessError("public release readiness research_only must be true")
    if report.get("disclaimer") != _DISCLAIMER:
        raise PublicReleaseReadinessError("public release readiness disclaimer is invalid")
    _verify_timestamp(report.get("generated_at"), "generated_at")
    status = report.get("status")
    if status not in {"blocked", "review_required", "ready"}:
        raise PublicReleaseReadinessError("public release readiness status is invalid")

    checks = report.get("checks")
    if not isinstance(checks, list) or len(checks) != len(_CHECK_IDS):
        raise PublicReleaseReadinessError("public release readiness checks are incomplete")
    observed: dict[str, Mapping[str, Any]] = {}
    for item in checks:
        if not isinstance(item, Mapping):
            raise PublicReleaseReadinessError("public release readiness check must be an object")
        if set(item) != {"id", "status", "error_codes"}:
            raise PublicReleaseReadinessError("public release readiness check fields are invalid")
        check_id = item.get("id")
        check_status = item.get("status")
        if check_id not in _CHECK_IDS or check_id in observed:
            raise PublicReleaseReadinessError(
                "public release readiness check identifiers are invalid"
            )
        if check_status not in {"passed", "failed", "review_required"}:
            raise PublicReleaseReadinessError("public release readiness check status is invalid")
        error_codes = item.get("error_codes")
        if not isinstance(error_codes, list) or not all(
            isinstance(code, str) and re.fullmatch(r"[a-z0-9_]{1,120}", code)
            for code in error_codes
        ):
            raise PublicReleaseReadinessError("public release readiness error codes are invalid")
        if check_status == "passed" and error_codes:
            raise PublicReleaseReadinessError(
                "passed public release readiness checks cannot contain errors"
            )
        observed[str(check_id)] = item
    if set(observed) != set(_CHECK_IDS):
        raise PublicReleaseReadinessError(
            "public release readiness check identifiers are incomplete"
        )

    _verify_reasons(report.get("blocked_reasons"), "blocked_reasons")
    _verify_reasons(report.get("review_reasons"), "review_reasons")
    review = report.get("review")
    if not isinstance(review, Mapping) or set(review) != {
        "status",
        "redistribution_status",
        "legal_approval",
        "source_review_required",
        "source_approved",
    }:
        raise PublicReleaseReadinessError("public release readiness review details are invalid")
    if review.get("status") not in {"failed", "review_required", "passed"}:
        raise PublicReleaseReadinessError("public release readiness review status is invalid")
    if review.get("redistribution_status") not in {None, "operator_review_required", "approved"}:
        raise PublicReleaseReadinessError(
            "public release readiness redistribution status is invalid"
        )
    if review.get("legal_approval") not in {None, False, True}:
        raise PublicReleaseReadinessError("public release readiness legal approval is invalid")
    for key in ("source_review_required", "source_approved"):
        value = review.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise PublicReleaseReadinessError(f"public release readiness {key} is invalid")

    identity = report.get("identity")
    if not isinstance(identity, Mapping) or set(identity) != {"path", "sha256"}:
        raise PublicReleaseReadinessError("public release readiness identity is invalid")
    if identity.get("path") is not None and (
        not isinstance(identity.get("path"), str) or identity["path"] != "data/demo/snapshot.json"
    ):
        raise PublicReleaseReadinessError("public release readiness identity path is invalid")
    if identity.get("sha256") is not None and (
        not isinstance(identity.get("sha256"), str) or not _SHA256.fullmatch(identity["sha256"])
    ):
        raise PublicReleaseReadinessError("public release readiness identity hash is invalid")

    blocked = report.get("blocked_reasons")
    review_reasons = report.get("review_reasons")
    if status == "blocked" and not blocked:
        raise PublicReleaseReadinessError("blocked public release readiness must include reasons")
    if status != "blocked" and blocked:
        raise PublicReleaseReadinessError(
            "non-blocked public release readiness cannot include blocked reasons"
        )
    if status == "review_required" and not review_reasons:
        raise PublicReleaseReadinessError("review-required readiness must include review reasons")
    if status == "ready" and review_reasons:
        raise PublicReleaseReadinessError(
            "ready public release readiness cannot include review reasons"
        )
    expected_hash = report.get("readiness_sha256")
    if not isinstance(expected_hash, str) or not _SHA256.fullmatch(expected_hash):
        raise PublicReleaseReadinessError("public release readiness hash is invalid")
    unsigned = dict(report)
    unsigned.pop("readiness_sha256", None)
    if _content_hash(unsigned) != expected_hash:
        raise PublicReleaseReadinessError("public release readiness content hash mismatch")


def _review_state(manifest: dict[str, Any] | None) -> dict[str, Any]:
    if manifest is None:
        return {
            "check_status": "failed",
            "review_status": "failed",
            "error_codes": ["provenance_manifest_unreadable"],
            "redistribution_status": None,
            "legal_approval": None,
            "source_review_required": 0,
            "source_approved": 0,
        }
    review = manifest.get("review")
    sources = manifest.get("sources")
    if not isinstance(review, dict) or not isinstance(sources, list):
        return {
            "check_status": "failed",
            "review_status": "failed",
            "error_codes": ["provenance_review_contract_invalid"],
            "redistribution_status": None,
            "legal_approval": None,
            "source_review_required": 0,
            "source_approved": 0,
        }
    redistribution_status = review.get("redistribution_status")
    legal_approval = review.get("legal_approval")
    source_statuses = [
        source.get("redistribution_status") for source in sources if isinstance(source, dict)
    ]
    source_review_required = sum(status != "approved" for status in source_statuses)
    source_approved = sum(status == "approved" for status in source_statuses)
    approved = (
        redistribution_status == "approved"
        and legal_approval is True
        and source_statuses
        and source_review_required == 0
        and isinstance(review.get("last_reviewed_at"), str)
        and bool(_ISO_TIMESTAMP.fullmatch(review["last_reviewed_at"]))
    )
    if approved:
        return {
            "check_status": "passed",
            "review_status": "passed",
            "error_codes": [],
            "redistribution_status": redistribution_status,
            "legal_approval": legal_approval,
            "source_review_required": source_review_required,
            "source_approved": source_approved,
        }
    return {
        "check_status": "review_required",
        "review_status": "review_required",
        "error_codes": ["redistribution_review_pending"],
        "redistribution_status": redistribution_status,
        "legal_approval": legal_approval,
        "source_review_required": source_review_required,
        "source_approved": source_approved,
    }


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _resolve_under_root(root: Path, path: Path) -> Path:
    candidate = path if path.is_absolute() else root / path
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise PublicReleaseReadinessError(
            "public release input path escapes repository root"
        ) from error
    return resolved


def _load_sibling(filename: str, module_name: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise PublicReleaseReadinessError(f"public release validator is unavailable: {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _error_code(message: str) -> str:
    prefix = message.split(":", 1)[0].lower()
    code = re.sub(r"[^a-z0-9]+", "_", prefix).strip("_")
    return code[:120] or "validation_error"


def _verify_reasons(value: object, label: str) -> None:
    if not isinstance(value, list) or not all(
        isinstance(reason, str) and re.fullmatch(r"[a-z0-9_]{1,120}", reason) for reason in value
    ):
        raise PublicReleaseReadinessError(f"public release readiness {label} are invalid")


def _verify_timestamp(value: object, label: str) -> None:
    if not isinstance(value, str) or not _ISO_TIMESTAMP.fullmatch(value):
        raise PublicReleaseReadinessError(
            f"public release readiness {label} must be a UTC timestamp"
        )


def _content_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, default=Path("public"))
    parser.add_argument("--lock", type=Path, default=Path("config/public_snapshot.lock.json"))
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--allow-review-required",
        action="store_true",
        help="return success for a structurally valid release that still needs review",
    )
    args = parser.parse_args()
    try:
        report = build_public_release_readiness(
            args.bundle_root,
            args.lock,
            repo_root=args.repo_root,
        )
    except (OSError, PublicReleaseReadinessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(serialized, encoding="utf-8")
        temporary.replace(args.output)
        print(f"Wrote public release readiness to {args.output}")
    print(serialized, end="")
    return (
        0
        if report["status"] == "ready"
        or (report["status"] == "review_required" and args.allow_review_required)
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
