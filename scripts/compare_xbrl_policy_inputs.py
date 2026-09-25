"""Compare paired legacy/duration-aware rebuilds under the same runtime."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import orjson

from edgar_moe.data.storage import sha256_file
from edgar_moe.features.dataset import ResearchDataset
from edgar_moe.modeling.policy_delta import (
    compare_feature_policies,
    verify_feature_policy_report,
)
from edgar_moe.settings import ResearchConfig


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-dataset", type=Path, required=True)
    parser.add_argument("--candidate-dataset", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("config/authenticated-v2.yaml"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    private_root = Path("data/artifacts/v2-reviews").resolve()
    destination = args.output.resolve()
    if not destination.is_relative_to(private_root):
        parser.error("output must be under the ignored data/artifacts/v2-reviews directory")
    if destination.exists():
        parser.error("refusing to overwrite an existing policy-delta report")
    config = ResearchConfig.from_yaml(args.config)
    result = compare_feature_policies(
        ResearchDataset.load(args.baseline_dataset),
        ResearchDataset.load(args.candidate_dataset),
        test_start=date.fromisoformat(config.evaluation.test_start),
        baseline_manifest_sha256=sha256_file(args.baseline_dataset / "manifest.json"),
        candidate_manifest_sha256=sha256_file(args.candidate_dataset / "manifest.json"),
    )
    verify_feature_policy_report(result)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(orjson.dumps(result, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS))
    print(f"Wrote private pre-test attribution audit: {destination}; status={result['status']}")


if __name__ == "__main__":
    main()
