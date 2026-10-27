from __future__ import annotations

import hashlib
import importlib.util
import stat
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path
from subprocess import run
from types import SimpleNamespace

import numpy as np
import orjson
import pandas as pd
import pytest

from edgar_moe.features.dataset import ResearchDataset
from edgar_moe.modeling.policy_delta import (
    compare_feature_policies,
    verify_feature_policy_report,
)

MANIFEST_KWARGS = {
    "baseline_manifest_sha256": "b" * 64,
    "candidate_manifest_sha256": "c" * 64,
}

_SCRIPT = Path("scripts/compare_xbrl_policy_inputs.py")
_SCRIPT_SPEC = importlib.util.spec_from_file_location("compare_xbrl_policy_inputs", _SCRIPT)
assert _SCRIPT_SPEC is not None and _SCRIPT_SPEC.loader is not None
_SCRIPT_MODULE = importlib.util.module_from_spec(_SCRIPT_SPEC)
_SCRIPT_SPEC.loader.exec_module(_SCRIPT_MODULE)


def _dataset(*, policy: str, identity: str) -> ResearchDataset:
    events = pd.DataFrame(
        {
            "event_id": ["private-1", "private-2", "locked-private-3", "locked-private-4"],
            "entry_date": [
                date(2023, 6, 1),
                date(2024, 6, 1),
                date(2024, 12, 31),
                date(2025, 1, 1),
            ],
            "accepted_at": pd.to_datetime(
                [
                    "2023-05-31T20:00:00Z",
                    "2024-05-31T20:00:00Z",
                    "2024-12-30T20:00:00Z",
                    "2024-12-31T20:00:00Z",
                ]
            ),
            "entry_at": pd.to_datetime(
                [
                    "2023-06-01T13:30:00Z",
                    "2024-06-01T13:30:00Z",
                    "2024-12-31T13:30:00Z",
                    "2025-01-01T13:30:00Z",
                ]
            ),
            "horizon_at": pd.to_datetime(
                [
                    "2023-07-01T20:00:00Z",
                    "2024-07-01T20:00:00Z",
                    "2025-01-01T00:00:00Z",
                    "2025-02-01T20:00:00Z",
                ]
            ),
        }
    )
    return ResearchDataset(
        dataset_id=f"research-2026-07-31-{'a' * 12 if identity == 'v1' else 'b' * 12}",
        as_of=date(2026, 7, 31),
        events=events,
        modalities={
            "text": np.array([[1.0], [2.0], [3.0], [4.0]], dtype=np.float32),
            "fundamental": np.array([[0.1], [0.2], [0.3], [0.4]], dtype=np.float32),
            "market": np.array([[0.4], [0.5], [0.6], [0.7]], dtype=np.float32),
        },
        regime=np.array([[0.7], [0.8], [0.9], [1.0]], dtype=np.float32),
        target=np.array([0.01, 0.02, 0.03, 0.04], dtype=np.float32),
        daily_returns=pd.DataFrame(),
        availability=pd.DataFrame(),
        feature_names={
            "text": ["text_0"],
            "fundamental": ["return_on_assets"],
            "market": ["momentum_5d"],
            "regime": ["VIXCLS"],
        },
        attrition={},
        source_manifest_hash="a" * 64,
        provenance={
            "xbrl_fact_policy": policy,
            "python_runtime": "3.12.13",
            "text_encoder": (
                "finbert-v2:torch=2.14.0:transformers=4.57.6:model=fixture:"
                f"revision={'a' * 40}:tokenizer_revision={'b' * 40}:"
                "chunk_tokens=510:pooling=weighted-cls:max_chunks=12:batch_size=16:"
                "device=cpu:sampling=uniform-v1:sentiment=negative-neutral-positive"
            ),
        },
    )


def _candidate(baseline: ResearchDataset) -> ResearchDataset:
    modalities = {name: values.copy() for name, values in baseline.modalities.items()}
    modalities["fundamental"][0, 0] = 0.3
    return replace(
        baseline,
        dataset_id=f"research-2026-07-31-{'b' * 12}",
        modalities=modalities,
        target=baseline.target.copy(),
        provenance={
            "xbrl_fact_policy": "duration_aware_v2",
            "python_runtime": baseline.provenance["python_runtime"],
            "text_encoder": baseline.provenance["text_encoder"],
        },
    )


def test_policy_delta_is_pretest_only_and_redacted() -> None:
    baseline = _dataset(policy="legacy_v1", identity="v1")
    candidate = _candidate(baseline)
    candidate.modalities["text"][2, 0] = 999.0
    candidate.target[2] = 999.0
    candidate.modalities["text"][3, 0] = 999.0
    candidate.target[3] = 999.0

    report = compare_feature_policies(
        baseline, candidate, test_start=date(2025, 1, 1), **MANIFEST_KWARGS
    )

    assert report["status"] == "isolated_policy_change"
    assert report["baseline_pretest_events"] == 2
    assert report["locked_targets_compared"] is False
    assert report["modalities"]["fundamental"]["changed_rows"] == 1
    assert report["modalities"]["text"]["changed_rows"] == 0
    assert len(report["report_hash"]) == 64
    assert "private-1" not in str(report)
    assert "locked-private-3" not in str(report)
    assert "locked-private-4" not in str(report)
    assert "999" not in str(report)
    verify_feature_policy_report(report)

    altered = {**report, "baseline_pretest_events": 3}
    with pytest.raises(ValueError, match="hash"):
        verify_feature_policy_report(altered)
    leaked = {**report, "private_event_rows": [{"event_id": "private-1"}]}
    with pytest.raises(ValueError, match="fields"):
        verify_feature_policy_report(leaked)


def test_non_xbrl_pretest_change_is_confounded() -> None:
    baseline = _dataset(policy="legacy_v1", identity="v1")
    candidate = _candidate(baseline)
    candidate.modalities["text"][1, 0] = 2.01

    report = compare_feature_policies(
        baseline, candidate, test_start=date(2025, 1, 1), **MANIFEST_KWARGS
    )

    assert report["status"] == "confounded"
    assert report["modalities"]["text"]["changed_cells"] == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("python_runtime", "3.13.0"),
        (
            "text_encoder",
            "finbert-v2:torch=2.14.0:transformers=4.57.6:model=fixture:"
            f"revision={'c' * 40}:tokenizer_revision={'b' * 40}:"
            "chunk_tokens=510:pooling=weighted-cls:max_chunks=12:batch_size=16:"
            "device=cpu:sampling=uniform-v1:sentiment=negative-neutral-positive",
        ),
    ],
)
def test_runtime_mismatch_is_confounded_even_with_shared_checkpoint_and_events(
    field: str, value: str
) -> None:
    baseline = _dataset(policy="legacy_v1", identity="v1")
    candidate = _candidate(baseline)
    candidate_provenance = {**candidate.provenance, field: value}
    candidate = replace(candidate, provenance=candidate_provenance)

    report = compare_feature_policies(
        baseline, candidate, test_start=date(2025, 1, 1), **MANIFEST_KWARGS
    )

    assert report["status"] == "confounded"
    assert report["same_source_checkpoint"] is True
    assert report["same_pretest_event_set"] is True
    assert report["same_python_runtime"] is (field != "python_runtime")
    assert report["same_text_encoder"] is (field != "text_encoder")
    verify_feature_policy_report(report)


@pytest.mark.parametrize("revision_field", ["revision", "tokenizer_revision"])
def test_unresolved_finbert_revision_is_not_a_pinned_runtime(revision_field: str) -> None:
    baseline = _dataset(policy="legacy_v1", identity="v1")
    candidate = _candidate(baseline)
    identity = baseline.provenance["text_encoder"]
    assert isinstance(identity, str)
    digest = "a" * 40 if revision_field == "revision" else "b" * 40
    unresolved = identity.replace(f"{revision_field}={digest}", f"{revision_field}=unresolved")
    baseline = replace(
        baseline,
        provenance={**baseline.provenance, "text_encoder": unresolved},
    )
    candidate = replace(
        candidate,
        provenance={**candidate.provenance, "text_encoder": unresolved},
    )

    report = compare_feature_policies(
        baseline, candidate, test_start=date(2025, 1, 1), **MANIFEST_KWARGS
    )

    assert report["same_text_encoder"] is False
    assert report["status"] == "confounded"
    verify_feature_policy_report(report)


@pytest.mark.parametrize("field", ["python_runtime", "text_encoder"])
def test_matching_unpinned_runtime_identity_is_confounded(field: str) -> None:
    baseline = _dataset(policy="legacy_v1", identity="v1")
    candidate = _candidate(baseline)
    if field == "python_runtime":
        value = "unresolved"
    else:
        identity = baseline.provenance["text_encoder"]
        assert isinstance(identity, str)
        value = identity.replace(":batch_size=16", "")
    baseline = replace(baseline, provenance={**baseline.provenance, field: value})
    candidate = replace(candidate, provenance={**candidate.provenance, field: value})

    report = compare_feature_policies(
        baseline, candidate, test_start=date(2025, 1, 1), **MANIFEST_KWARGS
    )

    flag = "same_python_runtime" if field == "python_runtime" else "same_text_encoder"
    assert report[flag] is False
    assert report["status"] == "confounded"
    verify_feature_policy_report(report)


def test_pretest_rows_align_by_identity_not_storage_order() -> None:
    baseline = _dataset(policy="legacy_v1", identity="v1")
    candidate = _candidate(baseline)
    order = [1, 0, 2, 3]
    candidate = replace(
        candidate,
        events=candidate.events.iloc[order].reset_index(drop=True),
        modalities={name: values[order] for name, values in candidate.modalities.items()},
        regime=candidate.regime[order],
        target=candidate.target[order],
    )

    report = compare_feature_policies(
        baseline, candidate, test_start=date(2025, 1, 1), **MANIFEST_KWARGS
    )

    assert report["status"] == "isolated_policy_change"
    assert report["same_pretest_event_set"] is True


def test_policy_delta_refuses_a_different_test_boundary() -> None:
    baseline = _dataset(policy="legacy_v1", identity="v1")
    candidate = _candidate(baseline)

    with pytest.raises(ValueError, match="pinned locked start 2025-01-01"):
        compare_feature_policies(
            baseline,
            candidate,
            test_start=date(2025, 1, 2),
            **MANIFEST_KWARGS,
        )


def test_policy_delta_verifier_rejects_rehashed_boundary_drift() -> None:
    baseline = _dataset(policy="legacy_v1", identity="v1")
    candidate = _candidate(baseline)
    report = compare_feature_policies(
        baseline, candidate, test_start=date(2025, 1, 1), **MANIFEST_KWARGS
    )
    drifted = {**report, "test_start": "2025-01-02"}
    unsigned = {key: value for key, value in drifted.items() if key != "report_hash"}
    drifted["report_hash"] = hashlib.sha256(
        orjson.dumps(unsigned, option=orjson.OPT_SORT_KEYS)
    ).hexdigest()

    with pytest.raises(ValueError, match="differs from the pinned locked start"):
        verify_feature_policy_report(drifted)


def test_policy_delta_report_writer_uses_owner_only_create_once_files(tmp_path: Path) -> None:
    destination = tmp_path / "private" / "study" / "policy-delta.json"
    _SCRIPT_MODULE._write_private_report(destination, {"status": "confounded"})

    assert stat.S_IMODE(destination.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert orjson.loads(destination.read_bytes()) == {"status": "confounded"}
    with pytest.raises(FileExistsError):
        _SCRIPT_MODULE._write_private_report(destination, {"status": "passed"})
    assert orjson.loads(destination.read_bytes()) == {"status": "confounded"}


def test_policy_delta_writer_refuses_a_broad_report_directory(tmp_path: Path) -> None:
    directory = tmp_path / "shared"
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)

    with pytest.raises(PermissionError, match="owner-only mode 0700"):
        _SCRIPT_MODULE._write_private_report(directory / "report.json", {"status": "confounded"})
    assert list(directory.iterdir()) == []


def test_policy_delta_cli_writes_a_private_verified_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = _dataset(policy="legacy_v1", identity="v1")
    candidate = _candidate(baseline)

    def load_dataset(_cls: object, path: Path) -> ResearchDataset:
        return baseline if path.name == "legacy" else candidate

    def load_config(_cls: object, _path: Path) -> object:
        return SimpleNamespace(evaluation=SimpleNamespace(test_start="2025-01-01"))

    def fake_sha256(path: Path) -> str:
        return ("b" if path.parent.name == "legacy" else "c") * 64

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_SCRIPT_MODULE.ResearchDataset, "load", classmethod(load_dataset))
    monkeypatch.setattr(_SCRIPT_MODULE.ResearchConfig, "from_yaml", classmethod(load_config))
    monkeypatch.setattr(_SCRIPT_MODULE, "sha256_file", fake_sha256)
    destination = Path("data/artifacts/v2-reviews/synthetic/policy-delta.json")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "compare_xbrl_policy_inputs.py",
            "--baseline-dataset",
            "legacy",
            "--candidate-dataset",
            "candidate",
            "--config",
            "config/authenticated-v2.yaml",
            "--output",
            str(destination),
        ],
    )

    _SCRIPT_MODULE.main()

    report_path = tmp_path / destination
    assert stat.S_IMODE(report_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600
    report = orjson.loads(report_path.read_bytes())
    verify_feature_policy_report(report)
    assert report["status"] == "isolated_policy_change"


def test_different_source_or_event_set_is_not_comparable() -> None:
    baseline = _dataset(policy="legacy_v1", identity="v1")
    candidate = _candidate(baseline)
    candidate = replace(candidate, source_manifest_hash="b" * 64)

    report = compare_feature_policies(
        baseline, candidate, test_start=date(2025, 1, 1), **MANIFEST_KWARGS
    )

    assert report["status"] == "not_comparable"
    assert "modalities" not in report
    verify_feature_policy_report(report)


def test_wrong_policy_and_duplicate_pretest_ids_fail_closed() -> None:
    baseline = _dataset(policy="legacy_v1", identity="v1")
    candidate = _candidate(baseline)
    with pytest.raises(ValueError, match="duration_aware_v2"):
        compare_feature_policies(
            baseline,
            replace(candidate, provenance={"xbrl_fact_policy": "legacy_v1"}),
            test_start=date(2025, 1, 1),
            **MANIFEST_KWARGS,
        )
    duplicate = baseline.events.copy()
    duplicate.loc[1, "event_id"] = duplicate.loc[0, "event_id"]
    with pytest.raises(ValueError, match="unique"):
        compare_feature_policies(
            replace(baseline, events=duplicate),
            candidate,
            test_start=date(2025, 1, 1),
            **MANIFEST_KWARGS,
        )


def test_policy_delta_cli_rejects_a_public_output_path(tmp_path: Path) -> None:
    completed = run(
        [
            sys.executable,
            "scripts/compare_xbrl_policy_inputs.py",
            "--baseline-dataset",
            "unused-v1",
            "--candidate-dataset",
            "unused-v2",
            "--output",
            str(tmp_path / "public" / "policy-delta.json"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "ignored data/artifacts/v2-reviews" in completed.stderr
