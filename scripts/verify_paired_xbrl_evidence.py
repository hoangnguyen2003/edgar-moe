"""Audit saved paired evidence links without loading arrays or calculating outcomes."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import orjson

from edgar_moe.modeling.policy_evidence import audit_saved_policy_evidence
from edgar_moe.settings import ResearchConfig


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "baseline-dataset",
        "candidate-dataset",
        "baseline-selection",
        "candidate-selection",
        "input-audit",
        "outcome-report",
        "source-manifest",
        "output",
    ):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument(
        "--baseline-config", type=Path, default=Path("config/authenticated-free.yaml")
    )
    parser.add_argument(
        "--candidate-config", type=Path, default=Path("config/authenticated-v2.yaml")
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.absolute()
    if (
        output.resolve() != output
        or not output.is_relative_to(root / "data/artifacts")
        or output.exists()
    ):
        parser.error("output must be a new ignored artifact path without symlinks")
    output.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if output.parent.stat().st_mode & 0o077:
        parser.error("output directory must be owner-only")
    report = audit_saved_policy_evidence(
        baseline_dataset=args.baseline_dataset,
        candidate_dataset=args.candidate_dataset,
        baseline_selection=args.baseline_selection,
        candidate_selection=args.candidate_selection,
        input_audit=args.input_audit,
        outcome_report=args.outcome_report,
        source_manifest=args.source_manifest,
        baseline_config=ResearchConfig.from_yaml(args.baseline_config),
        candidate_config=ResearchConfig.from_yaml(args.candidate_config),
    )
    with os.fdopen(
        os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb"
    ) as stream:
        stream.write(
            orjson.dumps(report, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2) + b"\n"
        )
    failed = [item["name"] for item in report["checks"] if item["status"] == "fail"]
    print(
        f"Saved-evidence content audit: {report['status']}; failed checks: {', '.join(failed) or 'none'}"
    )
    return 0 if report["status"] == "content_bound_only" else 1


if __name__ == "__main__":
    raise SystemExit(main())
