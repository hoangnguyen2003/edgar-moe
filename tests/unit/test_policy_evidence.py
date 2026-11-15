"""Saved evidence must bind as a chain; valid summaries alone cannot establish it."""

from __future__ import annotations

import hashlib
import json

import numpy as np
import orjson
import pytest

from edgar_moe.features.dataset import ResearchDataset
from edgar_moe.modeling.policy_evidence import ASSETS, audit_saved_policy_evidence
from edgar_moe.modeling.policy_outcome import (
    FIXED_MODELS,
    _expected_roster,
    verify_policy_outcome_report,
)
from edgar_moe.modeling.walk_forward import PROTOCOL_REVISIONS
from edgar_moe.settings import ResearchConfig


def digest(value):
    return hashlib.sha256(orjson.dumps(value, option=orjson.OPT_SORT_KEYS)).hexdigest()


def write(path, value):
    path.write_bytes(orjson.dumps(value, option=orjson.OPT_SORT_KEYS))


def sign(value, field):
    value[field] = digest({key: item for key, item in value.items() if key != field})


@pytest.fixture
def evidence(tmp_path):
    configs = [ResearchConfig(), ResearchConfig()]
    configs[0].features.xbrl_fact_policy = "legacy_v1"
    configs[1].features.xbrl_fact_policy = "duration_aware_v2"
    source = tmp_path / "source-manifest.json"
    write(
        source,
        {
            "dataset_id": "authenticated-2026-07-31",
            "configuration": {"as_of": "2026-07-31"},
            "source_versions": {"sec": "fixture", "macro": "fixture", "market": "fixture"},
        },
    )
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    roots, selections, manifests, choices = [], [], [], []
    for side, config, identity in zip(("baseline", "candidate"), configs, ("a", "b"), strict=True):
        root, selection = tmp_path / side, tmp_path / (side + "-selection")
        root.mkdir()
        selection.mkdir()
        for filename in ASSETS.values():
            # Intentionally not valid Parquet/NPZ: content binding must not decode rows.
            (root / filename).write_bytes(("opaque synthetic " + filename).encode())
        (selection / "oof-predictions.npz").write_bytes(b"opaque synthetic score archive")
        manifest = {
            "dataset_id": "research-2026-07-31-" + identity * 12,
            "source_manifest_hash": source_hash,
            "as_of": "2026-07-31",
            "provenance": {
                "xbrl_fact_policy": config.features.xbrl_fact_policy,
                "python_runtime": "3.12.13",
                "text_encoder": "finbert-v2:torch=2.14.0:transformers=4.57.6:model=fixture:"
                f"revision={'a' * 40}:tokenizer_revision={'b' * 40}:"
                "chunk_tokens=510:pooling=weighted-cls:max_chunks=12:batch_size=16:"
                "device=cpu:sampling=uniform-v1:sentiment=negative-neutral-positive",
            },
            "assets": dict(ASSETS),
            "hashes": {
                key: hashlib.sha256((root / filename).read_bytes()).hexdigest()
                for key, filename in ASSETS.items()
            },
        }
        models = [
            {"name": name, "selected": i == 0} for i, name in enumerate(_expected_roster(config))
        ]
        choice = {
            "dataset_id": manifest["dataset_id"],
            "configuration": config.model_dump(mode="json"),
            "models": models,
            "champion": models[0],
            "protocol_revisions": PROTOCOL_REVISIONS,
            "locked_test_evaluated": False,
            "locked_test_prediction_count": 0,
            "locked_test_start": "2025-01-01",
            "oof_predictions_sha256": hashlib.sha256(
                (selection / "oof-predictions.npz").read_bytes()
            ).hexdigest(),
            "folds": [
                {"name": "validation-2023", "validation_events": 2},
                {"name": "validation-2024", "validation_events": 2},
            ],
        }
        roots.append(root)
        selections.append(selection)
        manifests.append(manifest)
        choices.append(choice)
    audit = {
        "schema_version": 1,
        "comparison_type": "pretest_xbrl_policy_attribution",
        "test_start": "2025-01-01",
        "scope": "pretest_inputs_only",
        "locked_targets_compared": False,
        "status": "isolated_policy_change",
        "baseline_pretest_events": 4,
        "candidate_pretest_events": 4,
        "same_source_checkpoint": True,
        "same_pretest_event_set": True,
        "same_python_runtime": True,
        "same_text_encoder": True,
        "same_pretest_event_timing": True,
        "same_pretest_targets": True,
        "modalities": {
            name: {
                "same_feature_names": True,
                "same_shape": True,
                "exactly_equal": name != "fundamental",
                "changed_rows": int(name == "fundamental"),
                "changed_cells": int(name == "fundamental"),
                "maximum_absolute_delta": float(name == "fundamental"),
            }
            for name in ("text", "fundamental", "market", "regime")
        },
    }
    outcome = {
        "schema_version": 1,
        "status": "pretest_development_only",
        "test_start": "2025-01-01",
        "model_count": len(choices[0]["models"]),
        "oof_events": 4,
        "folds": choices[0]["folds"],
        "negative_controls_equal": True,
        "locked_test_predictions": 0,
        "fixed_model_comparisons": [
            {
                "name": name,
                "baseline_weighted_rank_ic": 0.0,
                "candidate_weighted_rank_ic": 0.0,
                "candidate_minus_baseline_rank_ic": 0.0,
                "delta_interval_95": {
                    "status": "ready",
                    "low": -0.1,
                    "high": 0.1,
                    "valid_resamples": 100,
                    "calendar_months_per_fold": [11, 11],
                },
            }
            for name in FIXED_MODELS
        ],
        "uncertainty": {
            "method": "paired_calendar_month_moving_block_within_fold",
            "conditional_on_selection": True,
            "resamples": 100,
            "block_months": 2,
            "seed": 42,
        },
        "interpretation": "Development-fold policy comparisons only; frozen v1 was known before either "
        "reconstruction, this comparison roster was codified after exploratory "
        "inspection, and both champions were selected on these same folds. "
        "Intervals are descriptive, not independent tests or alpha evidence.",
    }
    args = dict(
        baseline_dataset=roots[0],
        candidate_dataset=roots[1],
        baseline_selection=selections[0],
        candidate_selection=selections[1],
        input_audit=tmp_path / "input.json",
        outcome_report=tmp_path / "outcome.json",
        source_manifest=source,
        baseline_config=configs[0],
        candidate_config=configs[1],
    )

    def save():
        for side, root, selection, manifest, choice in zip(
            ("baseline", "candidate"), roots, selections, manifests, choices, strict=True
        ):
            write(root / "manifest.json", manifest)
            sign(choice, "selection_hash")
            write(selection / "walk-forward-selection.json", choice)
            audit[side + "_dataset_id"] = outcome[side + "_dataset_id"] = manifest["dataset_id"]
            audit[side + "_manifest_sha256"] = outcome[side + "_manifest_sha256"] = hashlib.sha256(
                (root / "manifest.json").read_bytes()
            ).hexdigest()
            audit[side + "_source_manifest_hash"] = manifest["source_manifest_hash"]
            outcome[side + "_selection_sha256"] = choice["selection_hash"]
            outcome[side + "_oof_sha256"] = choice["oof_predictions_sha256"]
            outcome[side + "_champion"] = choice["champion"]["name"]
        sign(audit, "report_hash")
        outcome["input_audit_hash"] = audit["report_hash"]
        sign(outcome, "report_hash")
        write(args["input_audit"], audit)
        write(args["outcome_report"], outcome)

    save()
    return args, audit, outcome, manifests, choices, save


def failed(result):
    return {item["name"] for item in result["checks"] if item["status"] == "fail"}


def test_content_binding_hashes_but_never_loads_arrays_or_implies_acceptance(evidence, monkeypatch):
    args, *_ = evidence
    monkeypatch.setattr(np, "load", lambda *_a, **_k: pytest.fail("array access"))
    monkeypatch.setattr(ResearchDataset, "load", lambda *_a, **_k: pytest.fail("dataset access"))
    result = audit_saved_policy_evidence(**args)
    assert result["status"] == "content_bound_only" and not failed(result)
    assert result["arrays_deserialized"] is result["metrics_recomputed"] is False
    assert (
        result["source_permission_verified"] is result["pretest_audit_chronology_verified"] is False
    )
    assert (
        result["historical_membership_verified"] is result["issue_275_completion_proven"] is False
    )
    assert "opaque synthetic" not in json.dumps(result) and str(
        args["input_audit"].parent
    ) not in json.dumps(result)


def test_standalone_valid_outcome_cannot_hide_an_old_input_contract(evidence):
    args, audit, outcome, _manifests, _choices, save = evidence
    del audit["same_python_runtime"]
    del audit["same_text_encoder"]
    save()
    verify_policy_outcome_report(outcome)
    result = audit_saved_policy_evidence(**args)
    assert result["status"] == "blocked"
    assert {"input_report_contract", "input_report_runtime_flags"} <= failed(result)
    assert "baseline_features" not in result["file_sha256"]


@pytest.mark.parametrize("runtime", ["python", "encoder"])
def test_matching_but_incomplete_recorded_runtime_cannot_be_repaired_by_true_report_flags(
    evidence, runtime
):
    args, _audit, _outcome, manifests, _choices, save = evidence
    for manifest in manifests:
        if runtime == "python":
            manifest["provenance"].pop("python_runtime")
        else:
            manifest["provenance"]["text_encoder"] = manifest["provenance"]["text_encoder"].replace(
                ":batch_size=16", ""
            )
    save()
    result = audit_saved_policy_evidence(**args)
    assert (
        "python_runtime_pinned_and_equal"
        if runtime == "python"
        else "finbert_runtime_pinned_and_equal"
    ) in failed(result)
    assert "baseline_features" not in result["file_sha256"]


def test_reduced_selection_roster_is_not_accepted_as_the_complete_expected_study(evidence):
    args, _audit, outcome, _manifests, choices, save = evidence
    for choice in choices:
        choice["models"] = choice["models"][:8]
    outcome["model_count"] = 8
    save()
    verify_policy_outcome_report(outcome)
    assert {"baseline_full_roster", "candidate_full_roster"} <= failed(
        audit_saved_policy_evidence(**args)
    )


def test_individually_valid_configs_must_still_match_between_paired_studies(evidence):
    args, _audit, _outcome, _manifests, choices, save = evidence
    args["candidate_config"].project.random_seed += 1
    choices[1]["configuration"] = args["candidate_config"].model_dump(mode="json")
    save()
    result = audit_saved_policy_evidence(**args)
    assert "paired_configuration_matches_except_policy" in failed(result)
    assert "candidate_declared_configuration" not in failed(result)


def test_source_reference_must_match_the_retained_manifest_bytes(evidence):
    args, _audit, _outcome, manifests, _choices, save = evidence
    for manifest in manifests:
        manifest["source_manifest_hash"] = "d" * 64
    save()
    result = audit_saved_policy_evidence(**args)
    assert "source_manifest_reference_binding" in failed(result)
    assert "shared_source_reference" not in failed(result)


@pytest.mark.parametrize("kind", ["features", "oof_archive"])
def test_tampered_saved_binary_is_detected_without_deserialization(evidence, kind):
    args, *_ = evidence
    path = (
        args["baseline_dataset"] / "features.npz"
        if kind == "features"
        else args["baseline_selection"] / "oof-predictions.npz"
    )
    path.write_bytes(path.read_bytes() + b"changed")
    result = audit_saved_policy_evidence(**args)
    assert result["status"] == "blocked" and "baseline_" + kind + "_hash" in failed(result)


def test_asset_path_redirection_fails_before_binary_access(evidence):
    args, _audit, _outcome, manifests, _choices, save = evidence
    manifests[0]["assets"]["features"] = "../private-foreign.npz"
    save()
    result = audit_saved_policy_evidence(**args)
    assert (
        "baseline_asset_manifest" in failed(result)
        and "baseline_features" not in result["file_sha256"]
    )


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("input_hash", "outcome_input_hash_link"),
        ("manifest_hash", "baseline_manifest_links"),
        ("champion", "baseline_champion_link"),
        ("selection_hash", "baseline_selection_links"),
    ],
)
def test_rehashed_outcome_cannot_rebind_to_another_evidence_chain(evidence, mutation, expected):
    args, _audit, outcome, *_ = evidence
    field = {
        "input_hash": "input_audit_hash",
        "manifest_hash": "baseline_manifest_sha256",
        "champion": "baseline_champion",
        "selection_hash": "baseline_selection_sha256",
    }[mutation]
    outcome[field] = "different champion" if mutation == "champion" else "e" * 64
    sign(outcome, "report_hash")
    write(args["outcome_report"], outcome)
    verify_policy_outcome_report(outcome)
    assert expected in failed(audit_saved_policy_evidence(**args))


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("locked", "baseline_pretest_protocol"),
        ("protocol", "baseline_pretest_protocol"),
        ("configuration", "baseline_declared_configuration"),
        ("fold", "baseline_fold_links"),
    ],
)
def test_selection_self_hash_does_not_authorize_protocol_or_scope_drift(
    evidence, mutation, expected
):
    args, _audit, _outcome, _manifests, choices, save = evidence
    if mutation == "locked":
        choices[0]["locked_test_evaluated"] = True
    elif mutation == "protocol":
        choices[0]["protocol_revisions"] = {}
    elif mutation == "configuration":
        choices[0]["configuration"]["evaluation"]["test_start"] = "2026-01-01"
    else:
        choices[0]["folds"] = [{"name": "validation-2023", "validation_events": 99}]
    save()
    assert expected in failed(audit_saved_policy_evidence(**args))


def test_ambiguous_json_and_symlinked_asset_are_refused_without_rewriting(evidence):
    args, *_ = evidence
    path = args["input_audit"]
    original = path.read_bytes()
    ambiguous = original[:-1] + b',"status":"isolated_policy_change"}'
    path.write_bytes(ambiguous)
    result = audit_saved_policy_evidence(**args)
    assert "input_audit_readable" in failed(result) and path.read_bytes() == ambiguous
    path.write_bytes(original)
    asset = args["baseline_dataset"] / "features.npz"
    foreign = asset.with_name("foreign.npz")
    asset.rename(foreign)
    asset.symlink_to(foreign)
    assert "baseline_features_hash" in failed(audit_saved_policy_evidence(**args))


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1e999", "-1e999"])
def test_nonfinite_json_including_overflow_is_refused_before_asset_access(evidence, value):
    args, *_ = evidence
    path = args["source_manifest"]
    malformed = path.read_bytes()[:-1] + b',"extra":' + value.encode() + b"}"
    path.write_bytes(malformed)
    result = audit_saved_policy_evidence(**args)
    assert "source_manifest_readable" in failed(result)
    assert "baseline_features" not in result["file_sha256"]
    assert path.read_bytes() == malformed


def test_symlinked_parent_directory_is_refused_without_following_assets(evidence):
    args, *_ = evidence
    root = args["baseline_dataset"]
    alias = root.with_name("baseline-alias")
    alias.symlink_to(root, target_is_directory=True)
    args["baseline_dataset"] = alias
    result = audit_saved_policy_evidence(**args)
    assert "baseline_manifest_readable" in failed(result)
    assert "baseline_features" not in result["file_sha256"]
