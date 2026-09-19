"""Run a safe, local-only forward-registry SQLite restore rehearsal."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from edgar_moe.forward.restore_rehearsal import RestoreRehearsalError, rehearse_sqlite_restore


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Copy a SQLite forward registry into a new file, compare table counts, "
            "and exercise the read path. This is not provider restore evidence."
        )
    )
    parser.add_argument("--source", type=Path, required=True, help="Existing SQLite registry file")
    parser.add_argument(
        "--destination",
        type=Path,
        required=True,
        help="New isolated SQLite file; an existing path is never overwritten",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="New JSON report path; an existing report is never overwritten",
    )
    return parser.parse_args()


def _write_report(path: Path, report: dict[str, Any]) -> None:
    output = path.expanduser().resolve()
    if output.exists():
        raise RestoreRehearsalError(f"output report already exists; refusing to overwrite: {path}")
    if not output.parent.is_dir():
        raise RestoreRehearsalError(f"output report parent directory does not exist: {output.parent}")
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")


def main() -> int:
    args = _parse_args()
    try:
        report = rehearse_sqlite_restore(args.source, args.destination)
        _write_report(args.output, report)
    except RestoreRehearsalError as error:
        print(f"SQLite restore rehearsal failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
