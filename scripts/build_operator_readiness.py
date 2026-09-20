"""Build a content-addressed operator-readiness report from a verified packet."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import orjson

from edgar_moe.forward.operator_evidence import (
    PROVIDER_EVIDENCE_PROFILES,
    OperatorEvidenceError,
    OperatorReadinessError,
    build_operator_readiness_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=30,
        help="maximum age for each required provider check (default: 30)",
    )
    parser.add_argument(
        "--profile",
        choices=tuple(PROVIDER_EVIDENCE_PROFILES),
        default="p0",
        help="readiness profile (default: p0)",
    )
    args = parser.parse_args()
    try:
        payload = orjson.loads(args.packet.read_bytes())
        if not isinstance(payload, dict):
            raise OperatorEvidenceError("operator evidence packet must be a JSON object")
        report = build_operator_readiness_report(
            payload,
            max_age=timedelta(days=args.max_age_days),
            profile=args.profile,
        )
        _write_immutable(args.output, report)
    except (OSError, TypeError, ValueError) as error:
        print(f"Operator readiness report write failed: {type(error).__name__}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "status": "written",
                "report": str(args.output),
                "readiness_status": report["status"],
                "profile": report["profile"],
                "packet_sha256": report["packet_sha256"],
                "readiness_sha256": report["readiness_sha256"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _write_immutable(output: Path, report: dict[str, object]) -> None:
    if output.exists() or output.is_symlink():
        raise OperatorReadinessError(f"refusing to overwrite existing report: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.with_name(f".{output.name}.staging-{uuid4().hex}")
    try:
        staging.write_bytes(orjson.dumps(report, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS))
        os.link(staging, output)
    finally:
        staging.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
