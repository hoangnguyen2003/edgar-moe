"""Verify a retained private, aggregate-only XBRL attribution report."""

from __future__ import annotations

import argparse
from pathlib import Path

import orjson

from edgar_moe.modeling.policy_delta import verify_feature_policy_report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = orjson.loads(args.report.read_bytes())
    if not isinstance(report, dict):
        raise ValueError("policy-delta report must be a JSON object")
    verify_feature_policy_report(report)
    print(f"Verified private pre-test attribution audit: status={report['status']}")


if __name__ == "__main__":
    main()
