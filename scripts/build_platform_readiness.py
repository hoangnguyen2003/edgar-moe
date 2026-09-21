"""Build a hash-pinned cross-domain platform readiness decision."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import orjson

from edgar_moe.copilot.readiness import verify_copilot_readiness
from edgar_moe.features.drift_readiness import verify_research_drift_readiness
from edgar_moe.forward.operator_evidence import verify_operator_readiness_report
from edgar_moe.platform_readiness import (
    CONTROL_IDS,
    build_platform_readiness,
)

Verifier = Callable[[dict[str, Any]], None]


def _public_release_verifier() -> Verifier:
    path = Path(__file__).with_name("public_release_readiness.py")
    spec = importlib.util.spec_from_file_location("public_release_readiness_for_platform", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("public release readiness verifier is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    verifier = getattr(module, "verify_public_release_readiness", None)
    if not callable(verifier):
        raise RuntimeError("public release readiness verifier is unavailable")
    return cast(Verifier, verifier)


def _verifier(control_id: str) -> Verifier:
    if control_id == "public_release":
        return _public_release_verifier()
    if control_id == "operator_evidence":
        return verify_operator_readiness_report
    if control_id == "research_copilot":
        return verify_copilot_readiness
    if control_id == "prospective_drift":
        return verify_research_drift_readiness
    raise ValueError(f"unknown platform control: {control_id}")


def _load_report(path: Path | None, control_id: str) -> dict[str, Any] | None:
    if path is None:
        return None
    payload = orjson.loads(path.read_bytes())
    if not isinstance(payload, dict):
        raise ValueError(f"{control_id} readiness report must be a JSON object")
    _verifier(control_id)(payload)
    status = payload.get("status")
    digest = payload.get("readiness_sha256")
    if not isinstance(status, str) or not isinstance(digest, str):
        raise ValueError(f"{control_id} readiness report identity is incomplete")

    blocked = _safe_reasons(payload.get("blocked_reasons"), f"{control_id}.blocked_reasons")
    stale = _safe_reasons(payload.get("stale_checks"), f"{control_id}.stale_checks")
    review = _safe_reasons(payload.get("review_reasons"), f"{control_id}.review_reasons")
    if status == "blocked":
        blocked = blocked or _safe_reasons(
            payload.get("blocked_checks"), f"{control_id}.blocked_checks"
        )
    elif status == "stale":
        blocked = blocked or stale or ["source_report_stale"]
    elif status == "review_required":
        review = review or blocked or ["source_report_review_required"]
    return {
        "scope": str(payload["scope"]),
        "status": status,
        "report_sha256": digest,
        "blocked_reasons": blocked if status in {"blocked", "stale"} else [],
        "review_reasons": review if status == "review_required" else [],
    }


def _safe_reasons(value: object, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError(f"{label} must be a list of reason codes")
    return [str(item) for item in value]


def build_from_paths(paths: dict[str, Path | None]) -> dict[str, Any]:
    """Load, source-verify, and compose the four readiness reports."""
    reports = {
        control_id: _load_report(paths.get(control_id), control_id) for control_id in CONTROL_IDS
    }
    return build_platform_readiness(reports)


def _write_immutable(payload: dict[str, Any], output: Path) -> None:
    if output.exists() or output.is_symlink():
        raise ValueError(f"refusing to overwrite existing platform readiness report: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.with_name(f".{output.name}.staging-{uuid4().hex}")
    try:
        staging.write_bytes(
            orjson.dumps(payload, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS)
        )
        os.link(staging, output)
    finally:
        staging.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-release-report", type=Path)
    parser.add_argument("--operator-report", type=Path)
    parser.add_argument("--copilot-report", type=Path)
    parser.add_argument("--drift-report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    paths = {
        "public_release": args.public_release_report,
        "operator_evidence": args.operator_report,
        "research_copilot": args.copilot_report,
        "prospective_drift": args.drift_report,
    }
    try:
        report = build_from_paths(paths)
        _write_immutable(report, args.output)
    except (OSError, TypeError, ValueError, RuntimeError) as error:
        print(f"Platform readiness build failed: {type(error).__name__}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "status": "written",
                "report": str(args.output),
                "readiness_status": report["status"],
                "blocked_reasons": report["blocked_reasons"],
                "review_reasons": report["review_reasons"],
                "readiness_sha256": report["readiness_sha256"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
