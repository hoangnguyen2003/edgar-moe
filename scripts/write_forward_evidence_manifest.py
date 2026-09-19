"""Write a content-addressed inventory of forward-cycle evidence files."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import orjson

from edgar_moe.forward.evidence_manifest import build_evidence_manifest


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--path", action="append", required=True, dest="paths")
    for name in (
        "workflow",
        "run_id",
        "run_attempt",
        "commit",
        "ref",
        "cutoff",
        "cycle_outcome",
        "device",
    ):
        parser.add_argument(f"--{name.replace('_', '-')}", default=os.environ.get(name.upper()))
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    context = {
        name: getattr(args, name)
        for name in (
            "workflow",
            "run_id",
            "run_attempt",
            "commit",
            "ref",
            "cutoff",
            "cycle_outcome",
            "device",
        )
    }
    manifest = build_evidence_manifest(args.paths, root=args.root, context=context)
    output = (args.root / args.output).resolve() if not args.output.is_absolute() else args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_bytes(orjson.dumps(manifest, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS))
    temporary.replace(output)
    print(orjson.dumps(manifest, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
