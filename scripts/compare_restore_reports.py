"""Compare isolated-restore counts and evidence-audit reports."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from edgar_moe.forward.restore_reports import (
    RestoreComparisonError,
    compare_reports,
    parse_counts,
    read_json_report,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare source and isolated-restore counts and evidence audits."
    )
    parser.add_argument("--source-counts", type=Path, required=True)
    parser.add_argument("--restored-counts", type=Path, required=True)
    parser.add_argument("--source-audit", type=Path, required=True)
    parser.add_argument("--restored-audit", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        source_counts_payload = read_json_report(args.source_counts)
        restored_counts_payload = read_json_report(args.restored_counts)
        source_audit = read_json_report(args.source_audit)
        restored_audit = read_json_report(args.restored_audit)
        report = compare_reports(
            parse_counts(source_counts_payload, args.source_counts),
            parse_counts(restored_counts_payload, args.restored_counts),
            source_audit,
            restored_audit,
        )
    except RestoreComparisonError as error:
        print(f"Restore comparison failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
