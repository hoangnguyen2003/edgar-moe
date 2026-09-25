"""Verify a private, aggregate-only paired XBRL outcome review."""

from __future__ import annotations

import argparse
from pathlib import Path

import orjson

from edgar_moe.modeling.policy_outcome import verify_policy_outcome_report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = orjson.loads(args.report.read_bytes())
    if not isinstance(report, dict):
        parser.error("policy-outcome report must be a JSON object")
    verify_policy_outcome_report(report)
    print(f"Verified private paired policy outcome review: status={report['status']}")


if __name__ == "__main__":
    main()
