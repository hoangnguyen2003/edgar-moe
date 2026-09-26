from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from edgar_moe.forward.failure_context import safe_exception_message
from edgar_moe.forward.operations import (
    find_processed_dataset,
    rolling_source_start,
    source_cutoff,
    update_filing_cache,
    validate_cutoff,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Refresh point-in-time inputs and run immutable forecast/settlement jobs."
    )
    parser.add_argument("--print-cutoff", action="store_true")
    parser.add_argument("--cutoff")
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw/authenticated"))
    parser.add_argument("--filing-cache", type=Path, default=Path("data/cache/forward-filings"))
    parser.add_argument("--processed-root", type=Path, default=Path("data/processed/finbert"))
    parser.add_argument(
        "--embedding-cache",
        type=Path,
        default=Path("data/artifacts/embedding-cache-finbert"),
    )
    parser.add_argument(
        "--research-config", type=Path, default=Path("config/authenticated-free.yaml")
    )
    parser.add_argument("--model-config", type=Path, default=Path("config/forward.yaml"))
    parser.add_argument("--universe", type=Path, default=Path("config/universe.research.csv"))
    parser.add_argument(
        "--diagnostic-output",
        type=Path,
        help="Optional output path for a read-only short-horizon diagnostic report.",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=730,
        help="Bounded prospective source window; must retain at least 400 calendar days.",
    )
    parser.add_argument("--device", choices=("cpu", "mps", "auto"), default="cpu")
    parser.add_argument(
        "--prewarm-only",
        action="store_true",
        help="Refresh sources and build the versioned embedding cache without registry writes.",
    )
    args = parser.parse_args()
    if args.prewarm_only and args.diagnostic_output is not None:
        parser.error("--diagnostic-output is unavailable with --prewarm-only")

    cutoff = validate_cutoff(args.cutoff or source_cutoff())
    if args.print_cutoff:
        print(cutoff)
        return
    source_start = rolling_source_start(cutoff, lookback_days=args.lookback_days)
    print(f"Prospective source window: {source_start} through {cutoff}.")

    executable = shutil.which("edgar-moe")
    if executable is None:
        raise RuntimeError("edgar-moe executable is unavailable; run through `uv run`")

    checkpoint = args.raw_root / cutoff
    _run(
        executable,
        "refresh-data",
        "--universe",
        str(args.universe),
        "--output-dir",
        str(args.raw_root),
        "--as-of",
        cutoff,
        "--start",
        source_start,
        "--end",
        cutoff,
        "--config",
        str(args.research_config),
        "--filing-cache",
        str(args.filing_cache),
        "--resume",
    )
    cached = update_filing_cache(checkpoint=checkpoint, filing_cache=args.filing_cache)
    print(f"Checkpointed {cached:,} new immutable filing document(s) before embedding.")
    _run(
        executable,
        "build-dataset",
        "--checkpoint",
        str(checkpoint),
        "--output-dir",
        str(args.processed_root),
        "--embedding-cache",
        str(args.embedding_cache),
        "--embedder",
        "finbert",
        "--device",
        args.device,
        "--config",
        str(args.research_config),
    )
    dataset = find_processed_dataset(
        processed_root=args.processed_root,
        checkpoint_manifest=checkpoint / "manifest.json",
        cutoff=cutoff,
    )
    if args.prewarm_only:
        print(
            json.dumps(
                {
                    "mode": "prewarm_only",
                    "cutoff": cutoff,
                    "checkpoint": str(checkpoint),
                    "dataset": str(dataset),
                    "new_cached_filings": cached,
                },
                sort_keys=True,
            )
        )
        return
    _run(
        executable,
        "forward-forecast",
        "--dataset-dir",
        str(dataset),
        "--model-config",
        str(args.model_config),
        "--device",
        "cpu" if args.device == "auto" else args.device,
    )
    _run(
        executable,
        "forward-settle",
        "--dataset-dir",
        str(dataset),
        "--model-config",
        str(args.model_config),
    )
    if args.diagnostic_output is not None:
        _run(
            executable,
            "forward-diagnostic",
            "--dataset-dir",
            str(dataset),
            "--horizon-sessions",
            "5",
            "--output",
            str(args.diagnostic_output),
        )
    _run(executable, "forward-status")
    print(
        json.dumps(
            {
                "cutoff": cutoff,
                "checkpoint": str(checkpoint),
                "dataset": str(dataset),
                "new_cached_filings": cached,
                "diagnostic_output": (
                    str(args.diagnostic_output) if args.diagnostic_output is not None else None
                ),
            },
            sort_keys=True,
        )
    )


def _run(executable: str, *arguments: str) -> None:
    command = [executable, *arguments]
    print("+ " + " ".join(command), flush=True)
    subprocess.run(command, check=True)


def _failure_message(error: BaseException) -> str:
    """Return a provider-neutral terminal message for the private runner log."""
    return f"forward cycle failed: {safe_exception_message(error)}"


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode) from error
    except Exception as error:
        print(_failure_message(error), file=sys.stderr)
        raise SystemExit(1) from error
