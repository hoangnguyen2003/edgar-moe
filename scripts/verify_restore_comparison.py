"""Verify the content hash on a restore comparison report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from edgar_moe.forward.restore_reports import (
    RestoreComparisonError,
    read_json_report,
    verify_restore_comparison_report,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify the SHA-256 content hash on a restore comparison report."
    )
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        report = read_json_report(args.report)
        verify_restore_comparison_report(report)
    except RestoreComparisonError as error:
        print(f"Restore comparison verification failed: {error}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": "verified",
                "report_hash": report["report_hash"],
                "report": str(args.report),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
