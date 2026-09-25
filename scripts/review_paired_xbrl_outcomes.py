"""Retain a private, aggregate-only paired pre-test XBRL model-outcome review."""

from __future__ import annotations

import argparse
from pathlib import Path

import orjson

from edgar_moe.data.storage import sha256_file
from edgar_moe.features.dataset import ResearchDataset
from edgar_moe.modeling.policy_outcome import (
    review_policy_outcomes,
    verify_policy_outcome_report,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-dataset", type=Path, required=True)
    parser.add_argument("--candidate-dataset", type=Path, required=True)
    parser.add_argument("--baseline-selection", type=Path, required=True)
    parser.add_argument("--candidate-selection", type=Path, required=True)
    parser.add_argument("--input-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bootstrap-samples", type=int, default=1000)
    parser.add_argument("--block-months", type=int, default=2)
    args = parser.parse_args()
    private_root = Path("data/artifacts/v2-reviews").resolve()
    destination = args.output.resolve()
    if not destination.is_relative_to(private_root):
        parser.error("output must be under the ignored data/artifacts/v2-reviews directory")
    if destination.exists():
        parser.error("refusing to overwrite an existing paired policy review")
    input_audit = orjson.loads(args.input_audit.read_bytes())
    if not isinstance(input_audit, dict):
        parser.error("input audit must be a JSON object")
    report = review_policy_outcomes(
        ResearchDataset.load(args.baseline_dataset),
        ResearchDataset.load(args.candidate_dataset),
        args.baseline_selection,
        args.candidate_selection,
        input_audit,
        baseline_manifest_sha256=sha256_file(args.baseline_dataset / "manifest.json"),
        candidate_manifest_sha256=sha256_file(args.candidate_dataset / "manifest.json"),
        bootstrap_samples=args.bootstrap_samples,
        block_months=args.block_months,
    )
    verify_policy_outcome_report(report)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(orjson.dumps(report, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS))
    print(f"Wrote private paired policy outcome review: {destination}; status={report['status']}")


if __name__ == "__main__":
    main()
