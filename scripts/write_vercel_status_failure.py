"""Retain a redacted failed Vercel commit status for the default-branch tip.

GitHub may receive a failed Vercel commit status without a deployment object
(for example, when a build is rate limited). This report is evidence of that
status only; it is not proof that a Production deployment was attempted.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_TERMINAL_FAILURES = frozenset({"failure", "error"})


def build_report(
    event: dict[str, Any], *, default_sha: str, checked_at: str | None = None
) -> dict[str, object]:
    """Accept only a terminal Vercel status for the checked-out default tip.

    Descriptions, target URLs, sender details, and provider payloads are never
    copied to the report. They may contain sensitive values or untrusted text.
    """
    sha = event.get("sha")
    state = event.get("state")
    context = event.get("context")
    status_id = event.get("id")
    if not isinstance(sha, str) or _SHA1.fullmatch(sha) is None:
        raise ValueError("commit status SHA must be a full lowercase SHA-1")
    if not isinstance(default_sha, str) or _SHA1.fullmatch(default_sha) is None:
        raise ValueError("default-branch SHA must be a full lowercase SHA-1")
    if sha != default_sha:
        raise ValueError("commit status does not target the default-branch tip")
    if context != "Vercel" or state not in _TERMINAL_FAILURES:
        raise ValueError("expected a terminal Vercel failure status")
    if isinstance(status_id, bool) or not isinstance(status_id, int) or status_id <= 0:
        raise ValueError("commit status ID must be a positive integer")
    return {
        "schema_version": 1,
        "status": "vercel_commit_status_failed",
        "checked_at": checked_at or datetime.now(UTC).isoformat(),
        "source": "github_commit_status",
        "commit_sha": sha,
        "context": "Vercel",
        "state": state,
        "github_status_id": status_id,
        "production_deployment_verified": False,
        "next_action": (
            "Inspect the provider status privately, retry the reviewed main commit "
            "when allowed, then verify Production with the deployment smoke check."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", type=Path, required=True)
    parser.add_argument("--default-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    event = json.loads(args.event.read_text(encoding="utf-8"))
    if not isinstance(event, dict):
        raise ValueError("commit status event must be an object")
    report = build_report(event, default_sha=args.default_sha)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
