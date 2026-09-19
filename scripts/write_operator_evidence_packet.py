"""Validate a draft operator packet and write its content-addressed form."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import orjson

from edgar_moe.forward.operator_evidence import (
    OperatorEvidenceError,
    prepare_operator_evidence_packet,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="redacted packet draft")
    parser.add_argument("--output", type=Path, required=True, help="content-addressed packet")
    args = parser.parse_args()
    try:
        payload = orjson.loads(args.input.read_bytes())
        if not isinstance(payload, dict):
            raise OperatorEvidenceError("packet draft must be a JSON object")
        packet = prepare_operator_evidence_packet(payload)
        if args.output.exists():
            raise OperatorEvidenceError(f"refusing to overwrite existing packet: {args.output}")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(
            orjson.dumps(packet, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS)
        )
    except (OSError, TypeError, ValueError) as error:
        print(f"Operator evidence packet write failed: {type(error).__name__}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": "written",
                "packet": str(args.output),
                "packet_sha256": packet["packet_sha256"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
