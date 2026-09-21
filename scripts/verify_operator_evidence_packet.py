"""Verify a redacted operator evidence packet without printing its contents."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import orjson

from edgar_moe.forward.operator_evidence import (
    OperatorEvidenceError,
    packet_summary,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    args = parser.parse_args()
    try:
        payload = orjson.loads(args.packet.read_bytes())
        if not isinstance(payload, dict):
            raise OperatorEvidenceError("packet must be a JSON object")
        summary = packet_summary(payload)
    except (OSError, TypeError, ValueError) as error:
        print(
            f"Operator evidence packet verification failed: {type(error).__name__}", file=sys.stderr
        )
        return 2
    summary["packet"] = str(args.packet)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
