"""Write a redacted report for a failed Production deployment status."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def _public_target_url(value: str) -> str:
    """Keep only an HTTPS path; never retain query or fragment credentials."""
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        return ""
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path, "", ""))


def build_failure_report(
    *,
    state: str,
    environment: str,
    sha: str,
    deployment_id: str,
    status_id: str,
    target_url: str,
    checked_at: str | None = None,
) -> dict[str, object]:
    """Build a value-safe deployment failure report from provider metadata."""
    return {
        "schema_version": 1,
        "status": "deployment_failed",
        "checked_at": checked_at or datetime.now(UTC).isoformat(),
        "deployment": {
            "state": state,
            "environment": environment,
            "sha": sha,
            "github_deployment_id": deployment_id,
            "github_deployment_status_id": status_id,
            "target_url": _public_target_url(target_url),
        },
        "next_action": (
            "Inspect provider build logs, redeploy the reviewed commit, "
            "and rerun the deployment smoke check."
        ),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    report = build_failure_report(
        state=os.environ.get("DEPLOYMENT_STATE", ""),
        environment=os.environ.get("DEPLOYMENT_ENVIRONMENT", ""),
        sha=os.environ.get("DEPLOYMENT_SHA", ""),
        deployment_id=os.environ.get("DEPLOYMENT_ID", ""),
        status_id=os.environ.get("DEPLOYMENT_STATUS_ID", ""),
        target_url=os.environ.get("DEPLOYMENT_URL", ""),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
