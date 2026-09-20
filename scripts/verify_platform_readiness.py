"""Verify a retained platform readiness decision without source reports."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import orjson

from edgar_moe.platform_readiness import PlatformReadinessError, verify_platform_readiness


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = orjson.loads(args.report.read_bytes())
        if not isinstance(report, dict):
            raise PlatformReadinessError("platform readiness must be a JSON object")
        verify_platform_readiness(report)
    except (OSError, TypeError, ValueError) as error:
        print(f"Platform readiness verification failed: {type(error).__name__}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "status": "verified",
                "report": str(args.report),
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
