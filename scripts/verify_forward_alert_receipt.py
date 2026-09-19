"""Verify the content hash on a redacted forward-alert receipt."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import orjson

from edgar_moe.forward.alerts import verify_alert_receipt


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify the SHA-256 content hash on a forward-alert receipt."
    )
    parser.add_argument("--receipt", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        payload = orjson.loads(args.receipt.read_bytes())
        if not isinstance(payload, dict):
            raise ValueError("Alert receipt must be a JSON object")
        verify_alert_receipt(payload)
    except (OSError, ValueError, TypeError) as error:
        print(f"Alert receipt verification failed: {type(error).__name__}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": "verified",
                "receipt": str(args.receipt),
                "receipt_hash": payload["receipt_hash"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
