"""Deliver an optional redacted forward-run alert to an HTTPS webhook."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import orjson

from edgar_moe.forward.alerts import (
    build_failure_alert,
    build_status_alert,
    send_webhook,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--failure-context",
        type=Path,
        default=Path(
            os.environ.get(
                "EDGAR_MOE_FAILURE_CONTEXT_PATH",
                "data/forward/diagnostics/forward-failure-context.json",
            )
        ),
    )
    parser.add_argument(
        "--status-file",
        type=Path,
        help="Optional JSON status payload from `edgar-moe forward-status`.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=10.0,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the redacted payload without sending it.",
    )
    parser.add_argument(
        "--receipt",
        type=Path,
        default=Path(
            os.environ.get(
                "EDGAR_MOE_ALERT_RECEIPT_PATH",
                "data/forward/diagnostics/forward-alert-receipt.json",
            )
        ),
        help="Redacted delivery receipt destination.",
    )
    args = parser.parse_args()
    webhook = os.environ.get("EDGAR_MOE_ALERT_WEBHOOK_URL", "").strip()
    if not webhook and not args.dry_run:
        print("EDGAR_MOE_ALERT_WEBHOOK_URL is not configured; alert delivery skipped.")
        return 0

    try:
        payload = _load_payload(args.failure_context, args.status_file)
    except (OSError, ValueError, TypeError) as error:
        print(f"Unable to build forward alert: {type(error).__name__}", file=sys.stderr)
        return 2
    if payload is None:
        print("Forward status is healthy; no alert is required.")
        return 0
    if args.dry_run:
        print(orjson.dumps(payload, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS).decode())
        return 0
    try:
        status = send_webhook(webhook, payload, timeout_seconds=args.timeout_seconds)
    except (ValueError, RuntimeError) as error:
        _write_receipt(
            args.receipt,
            {
                "delivered": False,
                "error_type": type(error).__name__,
                "kind": payload["kind"],
                "dedupe_key": payload["dedupe_key"],
            },
        )
        print(f"Forward alert delivery failed: {type(error).__name__}", file=sys.stderr)
        return 1
    _write_receipt(
        args.receipt,
        {
            "delivered": True,
            "http_status": status,
            "kind": payload["kind"],
            "dedupe_key": payload["dedupe_key"],
        },
    )
    print(f"Forward alert delivered (HTTP {status}).")
    return 0


def _load_payload(
    failure_context: Path,
    status_file: Path | None,
) -> dict[str, Any] | None:
    if failure_context.exists():
        context = _read_json(failure_context)
        if not isinstance(context, dict):
            raise ValueError("Failure context must be a JSON object")
        return build_failure_alert(context)
    if status_file is None:
        raise FileNotFoundError(f"Failure context does not exist: {failure_context}")
    status = _read_json(status_file)
    if not isinstance(status, dict):
        raise ValueError("Forward status must be a JSON object")
    # `forward-status` wraps the status object with performance; accept either
    # shape so this script can also consume an API monitor export.
    status_payload = status.get("status", status)
    if not isinstance(status_payload, dict):
        raise ValueError("Forward status field must be a JSON object")
    return build_status_alert(status_payload)


def _read_json(path: Path) -> Any:
    return orjson.loads(path.read_bytes())


def _write_receipt(path: Path, result: dict[str, Any]) -> None:
    receipt = {
        "schema_version": 1,
        "observed_at": datetime.now(UTC).isoformat(),
        **result,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(orjson.dumps(receipt, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS))
    temporary.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())
