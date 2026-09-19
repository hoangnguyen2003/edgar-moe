"""Check whether required provider evidence is complete and fresh."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

import orjson

from edgar_moe.forward.operator_evidence import operator_readiness


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=30,
        help="maximum age for each required provider check (default: 30)",
    )
    args = parser.parse_args()
    try:
        payload = orjson.loads(args.packet.read_bytes())
        if not isinstance(payload, dict):
            raise ValueError("operator evidence packet must be a JSON object")
        summary = operator_readiness(
            payload,
            max_age=timedelta(days=args.max_age_days),
        )
    except (OSError, TypeError, ValueError) as error:
        print(f"Operator readiness check failed: {type(error).__name__}", file=sys.stderr)
        return 2

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
