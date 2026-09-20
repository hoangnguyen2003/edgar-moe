"""Verify a retained operator-readiness report without reopening its packet."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import orjson

from edgar_moe.forward.operator_evidence import (
    OperatorReadinessError,
    verify_operator_readiness_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = orjson.loads(args.report.read_bytes())
        if not isinstance(report, dict):
            raise OperatorReadinessError("operator readiness report must be a JSON object")
        verify_operator_readiness_report(report)
    except (OSError, TypeError, ValueError) as error:
        print(f"Operator readiness report verification failed: {type(error).__name__}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "status": "verified",
                "report": str(args.report),
                "readiness_status": report["status"],
                "profile": report["profile"],
                "packet_sha256": report["packet_sha256"],
                "readiness_sha256": report["readiness_sha256"],
                "blocked_checks": report["blocked_checks"],
                "stale_checks": report["stale_checks"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
