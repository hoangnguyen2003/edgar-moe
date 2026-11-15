"""Read-only binding checks for retained paired XBRL evidence.

This hashes saved assets only after metadata passes. It never deserializes an
array, loads a dataset, recalculates a metric, trains, contacts a provider or
grants publication/source permission. Hash consistency is not provenance truth,
historical availability, pre-test chronology or issue-completion evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn

import orjson

from edgar_moe.data.source_rights import SOURCE_USE_DECISION
from edgar_moe.modeling.policy_delta import (
    LOCKED_TEST_START,
    _is_pinned_finbert_identity,
    _is_pinned_python_runtime,
    verify_feature_policy_report,
)
from edgar_moe.modeling.policy_outcome import _expected_roster, verify_policy_outcome_report
from edgar_moe.modeling.walk_forward import PROTOCOL_REVISIONS
from edgar_moe.settings import ResearchConfig

ASSETS = {
    "events": "events.parquet",
    "daily_returns": "daily-returns.parquet",
    "availability": "availability.parquet",
    "features": "features.npz",
}


def _read_descriptor(path: Path) -> int:
    # Reject stable symlinks in the whole operator-supplied path, including its
    # parent directories. O_NOFOLLOW also protects the final open itself.
    absolute = path.absolute()
    if absolute.resolve(strict=True) != absolute:
        raise ValueError("direct_evidence_path_required")
    return os.open(absolute, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)


def _json(path: Path) -> tuple[dict[str, Any], str]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("ambiguous_evidence_json")
            result[key] = value
        return result

    def invalid(_value: str) -> NoReturn:
        raise ValueError("nonfinite_evidence_json")

    def finite_float(value: str) -> float:
        result = float(value)
        if not math.isfinite(result):
            invalid(value)
        return result

    with os.fdopen(_read_descriptor(path), "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 2 * 1024 * 1024:
            raise ValueError("bounded_evidence_json_required")
        raw = source.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("bounded_evidence_json_required")
    value = json.loads(
        raw, object_pairs_hook=unique, parse_constant=invalid, parse_float=finite_float
    )
    if type(value) is not dict:
        raise ValueError("evidence_object_required")
    return value, hashlib.sha256(raw).hexdigest()


def _digest(value: Any) -> str:
    return hashlib.sha256(orjson.dumps(value, option=orjson.OPT_SORT_KEYS)).hexdigest()


def _file_hash(path: Path) -> str:
    with os.fdopen(_read_descriptor(path), "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 2 * 1024**3:
            raise ValueError("bounded_regular_asset_required")
        result = hashlib.sha256()
        total = 0
        for block in iter(lambda: source.read(1024 * 1024), b""):
            total += len(block)
            if total > 2 * 1024**3:
                raise ValueError("bounded_regular_asset_required")
            result.update(block)
    return result.hexdigest()


def audit_saved_policy_evidence(
    *,
    baseline_dataset: Path,
    candidate_dataset: Path,
    baseline_selection: Path,
    candidate_selection: Path,
    input_audit: Path,
    outcome_report: Path,
    source_manifest: Path,
    baseline_config: ResearchConfig,
    candidate_config: ResearchConfig,
) -> dict[str, Any]:
    """Check content links and runtime declarations without accessing provider rows.

    Configurations are explicit trusted operator inputs, not a reduced roster
    accepted from the selection being checked. Failure leaves all assets intact.
    """
    checks: list[dict[str, str]] = []
    hashes: dict[str, str] = {}
    records: dict[str, dict[str, Any]] = {}

    def check(name: str, operation: Callable[..., object]) -> bool:
        try:
            passed = operation() is True
        except (ValueError, TypeError, KeyError, AttributeError, OSError, RecursionError):
            passed = False
        checks.append({"name": name, "status": "pass" if passed else "fail"})
        return passed

    paths = {
        "input_audit": input_audit,
        "outcome_report": outcome_report,
        "baseline_manifest": baseline_dataset / "manifest.json",
        "candidate_manifest": candidate_dataset / "manifest.json",
        "baseline_selection": baseline_selection / "walk-forward-selection.json",
        "candidate_selection": candidate_selection / "walk-forward-selection.json",
        "source_manifest": source_manifest,
    }
    for name, path in paths.items():

        def load(name: str = name, path: Path = path) -> bool:
            records[name], hashes[name] = _json(path)
            return True

        check(name + "_readable", load)
    if len(records) != len(paths):
        return _result(checks, hashes)
    audit, outcome = records["input_audit"], records["outcome_report"]
    manifests = [records[side + "_manifest"] for side in ("baseline", "candidate")]
    selections = [records[side + "_selection"] for side in ("baseline", "candidate")]

    def valid(validator: Callable[[dict[str, Any]], None], report: dict[str, Any]) -> bool:
        validator(report)
        return True

    check("input_report_contract", lambda: valid(verify_feature_policy_report, audit))
    check("outcome_report_contract", lambda: valid(verify_policy_outcome_report, outcome))
    check("input_report_isolated", lambda: audit.get("status") == "isolated_policy_change")
    check(
        "outcome_input_hash_link", lambda: outcome.get("input_audit_hash") == audit["report_hash"]
    )
    check(
        "shared_source_reference",
        lambda: (
            manifests[0]["source_manifest_hash"] == manifests[1]["source_manifest_hash"]
            and isinstance(manifests[0]["source_manifest_hash"], str)
            and len(manifests[0]["source_manifest_hash"]) == 64
            and all(c in "0123456789abcdef" for c in manifests[0]["source_manifest_hash"])
            and manifests[0]["as_of"] == manifests[1]["as_of"]
        ),
    )
    check(
        "distinct_dataset_identities",
        lambda: manifests[0]["dataset_id"] != manifests[1]["dataset_id"],
    )
    check(
        "source_manifest_reference_binding",
        lambda: (
            hashes["source_manifest"]
            == manifests[0]["source_manifest_hash"]
            == manifests[1]["source_manifest_hash"]
            and records["source_manifest"]["configuration"]["as_of"] == manifests[0]["as_of"]
            and records["source_manifest"]["dataset_id"] == "authenticated-" + manifests[0]["as_of"]
            and set(records["source_manifest"]["source_versions"]) == {"macro", "market", "sec"}
        ),
    )
    check(
        "python_runtime_pinned_and_equal",
        lambda: (
            all(_is_pinned_python_runtime(m["provenance"].get("python_runtime")) for m in manifests)
            and manifests[0]["provenance"]["python_runtime"]
            == manifests[1]["provenance"]["python_runtime"]
        ),
    )
    check(
        "finbert_runtime_pinned_and_equal",
        lambda: (
            all(_is_pinned_finbert_identity(m["provenance"].get("text_encoder")) for m in manifests)
            and manifests[0]["provenance"]["text_encoder"]
            == manifests[1]["provenance"]["text_encoder"]
        ),
    )
    check(
        "input_report_runtime_flags",
        lambda: audit.get("same_python_runtime") is True and audit.get("same_text_encoder") is True,
    )

    def comparable_configuration(config: ResearchConfig) -> dict[str, Any]:
        value = config.model_dump(mode="json")
        value["features"].pop("xbrl_fact_policy")
        return value

    check(
        "paired_configuration_matches_except_policy",
        lambda: (
            comparable_configuration(baseline_config) == comparable_configuration(candidate_config)
        ),
    )
    for side, manifest, selection, config, policy in zip(
        ("baseline", "candidate"),
        manifests,
        selections,
        (baseline_config, candidate_config),
        ("legacy_v1", "duration_aware_v2"),
        strict=True,
    ):
        check(
            side + "_manifest_links",
            lambda side=side, manifest=manifest: (
                audit[side + "_dataset_id"]
                == outcome[side + "_dataset_id"]
                == manifest["dataset_id"]
                and audit[side + "_manifest_sha256"]
                == outcome[side + "_manifest_sha256"]
                == hashes[side + "_manifest"]
                and audit[side + "_source_manifest_hash"] == manifest["source_manifest_hash"]
            ),
        )
        check(
            side + "_policy",
            lambda manifest=manifest, config=config, policy=policy: (
                manifest["provenance"]["xbrl_fact_policy"]
                == config.features.xbrl_fact_policy
                == policy
            ),
        )
        check(
            side + "_selection_self_hash",
            lambda selection=selection: (
                selection["selection_hash"]
                == _digest({k: v for k, v in selection.items() if k != "selection_hash"})
            ),
        )
        check(
            side + "_selection_links",
            lambda side=side, selection=selection, manifest=manifest: (
                selection["dataset_id"] == manifest["dataset_id"]
                and selection["selection_hash"] == outcome[side + "_selection_sha256"]
                and selection["oof_predictions_sha256"] == outcome[side + "_oof_sha256"]
            ),
        )
        check(
            side + "_declared_configuration",
            lambda selection=selection, config=config: (
                ResearchConfig.model_validate(selection["configuration"]).model_dump(mode="json")
                == config.model_dump(mode="json")
            ),
        )
        check(
            side + "_full_roster",
            lambda selection=selection, config=config: (
                [m["name"] for m in selection["models"]] == _expected_roster(config)
                and outcome["model_count"] == len(selection["models"])
            ),
        )
        check(
            side + "_pretest_protocol",
            lambda selection=selection, config=config: (
                selection["protocol_revisions"] == PROTOCOL_REVISIONS
                and selection["locked_test_evaluated"] is False
                and type(selection["locked_test_prediction_count"]) is int
                and selection["locked_test_prediction_count"] == 0
                and selection["locked_test_start"]
                == config.evaluation.test_start
                == audit["test_start"]
                == outcome["test_start"]
                == LOCKED_TEST_START.isoformat()
            ),
        )
        check(
            side + "_champion_link",
            lambda side=side, selection=selection: (
                [m for m in selection["models"] if m.get("selected") is True]
                == [selection["champion"]]
                and selection["champion"]["name"] == outcome[side + "_champion"]
            ),
        )
        check(
            side + "_fold_links",
            lambda selection=selection: (
                [
                    {"name": f["name"], "validation_events": f["validation_events"]}
                    for f in selection["folds"]
                ]
                == outcome["folds"]
            ),
        )
        check(
            side + "_asset_manifest",
            lambda manifest=manifest: (
                manifest["assets"] == ASSETS
                and set(manifest["hashes"]) == set(ASSETS)
                and all(
                    type(value) is str
                    and len(value) == 64
                    and all(c in "0123456789abcdef" for c in value)
                    for value in manifest["hashes"].values()
                )
            ),
        )

    metadata_passed = all(c["status"] == "pass" for c in checks)
    if metadata_passed:
        for side, manifest, directory, selection_directory, selection in zip(
            ("baseline", "candidate"),
            manifests,
            (baseline_dataset, candidate_dataset),
            (baseline_selection, candidate_selection),
            selections,
            strict=True,
        ):
            for name, filename in ASSETS.items():

                def asset(
                    side: str = side,
                    name: str = name,
                    filename: str = filename,
                    directory: Path = directory,
                    manifest: dict[str, Any] = manifest,
                ) -> bool:
                    digest = _file_hash(directory / filename)
                    hashes[side + "_" + name] = digest
                    return bool(digest == manifest["hashes"][name])

                check(side + "_" + name + "_hash", asset)

            def archive(
                side: str = side,
                selection_directory: Path = selection_directory,
                selection: dict[str, Any] = selection,
            ) -> bool:
                digest = _file_hash(selection_directory / "oof-predictions.npz")
                hashes[side + "_oof_archive"] = digest
                return bool(digest == selection["oof_predictions_sha256"])

            check(side + "_oof_archive_hash", archive)
    else:
        checks.append(
            {"name": "saved_binary_asset_hashes", "status": "not_checked_metadata_failed"}
        )
    return _result(checks, hashes)


def _result(checks: list[dict[str, str]], hashes: dict[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": 1,
        "scope": "paired_xbrl_saved_evidence_content_audit_only",
        "status": "content_bound_only" if all(c["status"] == "pass" for c in checks) else "blocked",
        "checks": checks,
        "file_sha256": hashes,
        "source_use_review_status": SOURCE_USE_DECISION.status,
        "source_permission_verified": False,
        "historical_membership_verified": False,
        "source_payload_hashes_verified": False,
        "pretest_audit_chronology_verified": False,
        "metrics_recomputed": False,
        "arrays_deserialized": False,
        "provider_requests": 0,
        "issue_275_completion_proven": False,
    }
    return result | {"audit_sha256": _digest(result)}
