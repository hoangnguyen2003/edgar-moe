"""Bounded, offline #275 development study; never releases general execution gates.

Run prepare first and commit its redacted input commitment before run. All data,
selection, uncertainty and cost outputs stay in the one ignored private case.
"""

from __future__ import annotations

import argparse
import fcntl
import gc
import hashlib
import importlib.metadata
import os
import platform
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import orjson

from edgar_moe.data.refresh import verify_authenticated_bundle
from edgar_moe.features.dataset import ResearchDataset, build_research_dataset
from edgar_moe.features.text import FinBertEmbedder
from edgar_moe.modeling.policy_delta import compare_feature_policies, verify_feature_policy_report
from edgar_moe.modeling.policy_evidence import audit_saved_policy_evidence
from edgar_moe.modeling.policy_outcome import (
    _expected_roster,
    review_policy_outcomes,
    verify_policy_outcome_report,
)
from edgar_moe.modeling.pretest_review import review_v2_pretest
from edgar_moe.modeling.walk_forward import run_walk_forward_study, save_walk_forward_artifacts
from edgar_moe.settings import ResearchConfig
from edgar_moe.utils.hashing import sha256_file

SCOPE_SHA256 = "ae72948c3ea5925e49632e3437370a358ee6ed9a4961390034b496e57c09ed70"

PROTECTED = {
    "config/forward.yaml": "6fee9ade5a98b291cc1a895b10e0213a1d98911ff446252dbe9844b3e15ac318",
    "data/processed/finbert/research-2026-07-31-3553e7ad78dd/manifest.json": "399fb7ddd9410728ac6cfe941ca15c4b067ba26a0a1143fdfce5516fe62c1e6a",
    "data/artifacts/frozen-studies/research-2026-07-31-3553e7ad78dd/frozen-model.pt": "5cf77b2a8de255eb959236a48caac1f6c5d429767e250976c0c31324ef0bcd2c",
    "data/artifacts/frozen-studies/research-2026-07-31-3553e7ad78dd/locked-test.json": "88b88c70e5a65253adb186c6f24d14a0d6ee8827cc145effda6fdd7ea5c777c5",
}


def verify_preserved(shared: Path) -> None:
    for relative, digest in PROTECTED.items():
        if sha256_file(shared / relative) != digest:
            raise ValueError(f"Frozen evidence changed: {relative}")
    dataset_root = shared / "data/processed/finbert/research-2026-07-31-3553e7ad78dd"
    manifest = read(dataset_root / "manifest.json")
    for name, digest in manifest["hashes"].items():
        if sha256_file(dataset_root / manifest["assets"][name]) != digest:
            raise ValueError("Original frozen dataset asset changed")


def read(path: Path) -> Any:
    return orjson.loads(path.read_bytes())


def write(path: Path, value: Any) -> None:
    """Never replace a previous report, even on an interrupted/repeated run."""
    raw = (
        orjson.dumps(value, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS, default=str) + b"\n"
    )
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def retain_report(path: Path, report: dict[str, Any]) -> None:
    if path.exists():
        if read(path) != report:
            raise ValueError("Recomputed report differs; preserve retained evidence")
    else:
        write(path, report)


def progress(message: str) -> None:
    print(f"{datetime.now(UTC).isoformat()} {message}", flush=True)


def context(
    root: Path, shared: Path
) -> tuple[dict[str, Any], Path, Path, dict[str, ResearchConfig]]:
    scope_path = root / "ops/issue275/private-study-scope.json"
    if sha256_file(scope_path) != SCOPE_SHA256:
        raise ValueError("Bound private scope changed")
    scope = read(scope_path)
    if (
        platform.python_implementation() + " " + platform.python_version()
        != scope["python_runtime"]
    ):
        raise ValueError("Python runtime differs from scope")
    for package, version in scope["packages"].items():
        if importlib.metadata.version(package) != version:
            raise ValueError(f"Package differs from scope: {package}")
    for relative, digest in scope["input_sha256"].items():
        if sha256_file(root / relative) != digest:
            raise ValueError(f"Configuration changed: {relative}")
    for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
        if os.environ.get(key) != "1":
            raise ValueError("Offline model loading must be explicit")
    verify_preserved(shared)
    checkpoint = shared / scope["checkpoint"]
    if sha256_file(checkpoint / "manifest.json") != scope["checkpoint_manifest_sha256"]:
        raise ValueError("Checkpoint identity changed")
    verify_authenticated_bundle(checkpoint)
    configs = {
        name: ResearchConfig.from_yaml(root / filename)
        for name, filename in (
            ("baseline", "config/authenticated-free.yaml"),
            ("candidate", "config/authenticated-v2.yaml"),
        )
    }
    for config in configs.values():
        if (
            _expected_roster(config) != scope["roster"]
            or config.model.max_epochs != 80
            or config.evaluation.test_start != "2025-01-01"
            or config.evaluation.walk_forward_years != [2023, 2024]
        ):
            raise ValueError("Full original pretest protocol required")
    output = shared / "data/artifacts" / scope["output"]
    if output.is_symlink():
        raise ValueError("Private output cannot be a symlink")
    output.mkdir(mode=0o700, exist_ok=True)
    if output.stat().st_mode & 0o077:
        raise ValueError("Private output must be owner-only")
    return scope, checkpoint, output, configs


def prepare(
    scope: dict[str, Any], checkpoint: Path, output: Path, configs: dict[str, ResearchConfig]
) -> None:
    encoder = FinBertEmbedder(
        max_chunks=4, inference_batch_size=16, device="mps", revision=scope["finbert_revision"]
    )
    records = {}
    for side, config in configs.items():
        receipt = output / f"{side}-dataset.json"
        if receipt.exists():
            record = read(receipt)
            dataset_path = output / record["path"]
            if sha256_file(dataset_path / "manifest.json") != record["manifest_sha256"]:
                raise ValueError("Retained dataset manifest changed")
            dataset = ResearchDataset.load(dataset_path)
            if dataset.provenance["text_encoder"] != encoder.cache_identity:
                raise ValueError("Retained dataset encoder differs")
        else:
            progress(f"Building {side} dataset")
            dataset = build_research_dataset(
                checkpoint,
                config=config,
                embedder=encoder,
                embedding_cache=output / "embedding-cache",
                progress=progress,
            )
            dataset_path = dataset.save(output / "datasets")
            record = {
                "path": str(dataset_path.relative_to(output)),
                "dataset_id": dataset.dataset_id,
                "manifest_sha256": sha256_file(dataset_path / "manifest.json"),
            }
            write(receipt, record)
        records[side] = record
        del dataset
        gc.collect()
        progress(f"{side} dataset retained")
    baseline, candidate = [ResearchDataset.load(output / records[s]["path"]) for s in configs]
    report = compare_feature_policies(
        baseline,
        candidate,
        test_start=date(2025, 1, 1),
        baseline_manifest_sha256=records["baseline"]["manifest_sha256"],
        candidate_manifest_sha256=records["candidate"]["manifest_sha256"],
    )
    verify_feature_policy_report(report)
    if report["status"] != "isolated_policy_change":
        raise ValueError(f"Paired input audit did not isolate XBRL: {report['status']}")
    audit = output / "input-audit.json"
    if audit.exists():
        if read(audit) != report:
            raise ValueError("Retained input audit differs")
    else:
        write(audit, report)
    commitment = {
        "issue": 275,
        "scope_sha256": SCOPE_SHA256,
        "source_manifest_sha256": scope["checkpoint_manifest_sha256"],
        "datasets": records,
        "input_audit_sha256": sha256_file(audit),
        "input_audit_status": report["status"],
        "model_roster_count": len(scope["roster"]),
        "locked_test_start": "2025-01-01",
        "locked_test_scored": False,
        "prepared_at_utc": datetime.now(UTC).isoformat(),
    }
    destination = output / "prepared.json"
    if not destination.exists():
        write(destination, commitment)
    progress(
        "Preparation complete; commit prepared.json as the redacted input commitment before run"
    )


def run(
    root: Path,
    scope: dict[str, Any],
    checkpoint: Path,
    output: Path,
    configs: dict[str, ResearchConfig],
) -> None:
    commitment_path = root / "ops/issue275/input-commitment.json"
    commitment = read(commitment_path)
    if commitment != read(output / "prepared.json"):
        raise ValueError("Committed input audit differs from private preparation")
    subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", "src", "scripts", "config", "ops/issue275"],
        cwd=root,
        check=True,
    )
    relative = str(commitment_path.relative_to(root))
    committed = subprocess.check_output(["git", "show", "HEAD:" + relative], cwd=root)
    if orjson.loads(committed) != commitment:
        raise ValueError("Input audit must be committed before model training")
    if commitment["scope_sha256"] != SCOPE_SHA256:
        raise ValueError("Input audit has a different scope")
    audit = read(output / "input-audit.json")
    if sha256_file(output / "input-audit.json") != commitment["input_audit_sha256"]:
        raise ValueError("Input audit hash changed")
    verify_feature_policy_report(audit)
    selections = {}
    for side, config in configs.items():
        record = commitment["datasets"][side]
        dataset_path = output / record["path"]
        if sha256_file(dataset_path / "manifest.json") != record["manifest_sha256"]:
            raise ValueError("Dataset identity changed")
        dataset = ResearchDataset.load(dataset_path)
        selection = output / "selections" / dataset.dataset_id
        if selection.exists():
            retained = read(selection / "walk-forward-selection.json")
            if (
                retained["configuration"] != config.model_dump(mode="json")
                or retained["dataset_id"] != dataset.dataset_id
                or sorted(item["name"] for item in retained["models"]) != sorted(scope["roster"])
                or retained["locked_test_evaluated"] is not False
                or retained["locked_test_prediction_count"] != 0
                or retained["oof_predictions_sha256"]
                != sha256_file(selection / "oof-predictions.npz")
            ):
                raise ValueError("Retained selection differs; preserve it for inspection")
            selections[side] = selection
            del dataset
            gc.collect()
            progress(f"Reusing verified {side} selection without overwrite")
            continue
        execution_path = output / f"{side}-training-start.json"
        if not execution_path.exists():
            write(
                execution_path,
                {
                    "input_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root)
                    .decode()
                    .strip(),
                    "input_commitment_sha256": sha256_file(commitment_path),
                    "started_at_utc": datetime.now(UTC).isoformat(),
                },
            )
        progress(f"Starting full {side} walk-forward roster ({len(scope['roster'])} models)")
        result = run_walk_forward_study(dataset, config=config, device="cpu", progress=progress)
        if len(result.models) != len(scope["roster"]):
            raise ValueError("Full roster required")
        selections[side] = save_walk_forward_artifacts(result, output / "selections", config=config)
        del result, dataset
        gc.collect()
        progress(f"{side} selection and OOF retained")
    baseline, candidate = [
        ResearchDataset.load(output / commitment["datasets"][s]["path"]) for s in configs
    ]
    paired = review_policy_outcomes(
        baseline,
        candidate,
        selections["baseline"],
        selections["candidate"],
        audit,
        baseline_manifest_sha256=commitment["datasets"]["baseline"]["manifest_sha256"],
        candidate_manifest_sha256=commitment["datasets"]["candidate"]["manifest_sha256"],
    )
    verify_policy_outcome_report(paired)
    retain_report(output / "outcome-review.json", paired)
    review = review_v2_pretest(candidate, selections["candidate"])
    review["review_sha256"] = hashlib.sha256(
        orjson.dumps(review, option=orjson.OPT_SORT_KEYS)
    ).hexdigest()
    retain_report(output / "candidate-pretest-review.json", review)
    require_numerical_acceptance(paired, review)
    evidence = audit_saved_policy_evidence(
        baseline_dataset=output / commitment["datasets"]["baseline"]["path"],
        candidate_dataset=output / commitment["datasets"]["candidate"]["path"],
        baseline_selection=selections["baseline"],
        candidate_selection=selections["candidate"],
        input_audit=output / "input-audit.json",
        outcome_report=output / "outcome-review.json",
        source_manifest=checkpoint / "manifest.json",
        baseline_config=configs["baseline"],
        candidate_config=configs["candidate"],
    )
    retain_report(output / "content-audit.json", evidence)
    if evidence["status"] != "content_bound_only":
        raise ValueError("Saved evidence audit failed")
    progress(
        "Full paired study and numerical reviews retained privately; inspect interpretation before closeout"
    )


def require_numerical_acceptance(paired: dict[str, Any], review: dict[str, Any]) -> None:
    """Report presence and metadata alone cannot establish completion."""
    if (
        paired["status"] != "pretest_development_only"
        or paired["negative_controls_equal"] is not True
    ):
        raise ValueError("Negative controls do not support an isolated policy comparison")
    if paired["model_count"] != 33 or paired["locked_test_predictions"] != 0:
        raise ValueError("Full roster and zero locked predictions required")
    if any(
        item["delta_interval_95"]["status"] != "ready" for item in paired["fixed_model_comparisons"]
    ):
        raise ValueError("Paired uncertainty is incomplete")
    if review["portfolio"]["status"] != "development_only":
        raise ValueError("Pretest cost scenarios are unavailable")
    for model in review["portfolio"]["models"]:
        if sorted(item["cost_bps"] for item in model["scenarios"]) != [10, 25, 50]:
            raise ValueError("All original cost scenarios required")
    if len(review["hypotheses"]) != 2:
        raise ValueError("Explicit development hypotheses required")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["prepare", "run"])
    parser.add_argument("--shared-root", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    root = Path(__file__).resolve().parents[1]
    scope, checkpoint, output, configs = context(root, args.shared_root.resolve())
    with (output / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.stage == "prepare":
            prepare(scope, checkpoint, output, configs)
        else:
            run(root, scope, checkpoint, output, configs)
        verify_preserved(args.shared_root.resolve())
        verify_authenticated_bundle(checkpoint)


if __name__ == "__main__":
    main()
