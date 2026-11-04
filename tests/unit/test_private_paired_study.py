from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "private_paired_study_test", "scripts/run_private_paired_study.py"
)
assert spec is not None and spec.loader is not None
study = importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)


def test_reports_are_create_once_and_different_recomputation_preserves_evidence(tmp_path):
    path = tmp_path / "report.json"
    study.retain_report(path, {"value": 1})
    original = path.read_bytes()
    study.retain_report(path, {"value": 1})
    with pytest.raises(ValueError, match="preserve retained evidence"):
        study.retain_report(path, {"value": 2})
    with pytest.raises(FileExistsError):
        study.write(path, {"value": 2})
    assert path.read_bytes() == original


def test_changed_scope_rejected_before_checkpoint_access(tmp_path, monkeypatch):
    root = tmp_path / "worktree"
    scope = root / "ops/issue275/private-study-scope.json"
    scope.parent.mkdir(parents=True)
    scope.write_text("{}")
    monkeypatch.setattr(
        study, "verify_authenticated_bundle", lambda _: pytest.fail("accessed checkpoint")
    )
    with pytest.raises(ValueError, match="scope changed"):
        study.context(root, tmp_path)


def test_training_rejects_uncommitted_input_audit_before_dataset_load(tmp_path, monkeypatch):
    commitment = tmp_path / "ops/issue275/input-commitment.json"
    commitment.parent.mkdir(parents=True)
    study.write(commitment, {"scope_sha256": study.SCOPE_SHA256})
    study.write(tmp_path / "prepared.json", {"scope_sha256": study.SCOPE_SHA256})
    monkeypatch.setattr(study.subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(study.subprocess, "check_output", lambda *args, **kwargs: b"{}")
    monkeypatch.setattr(study.ResearchDataset, "load", lambda _: pytest.fail("accessed dataset"))
    with pytest.raises(ValueError, match="must be committed before model training"):
        study.run(tmp_path, {}, tmp_path, tmp_path, {})


def test_reviewed_scope_pins_full_roster_and_original_boundary():
    import hashlib

    scope_path = Path("ops/issue275/private-study-scope.json")
    assert hashlib.sha256(scope_path.read_bytes()).hexdigest() == study.SCOPE_SHA256
    scope = study.read(scope_path)
    assert len(scope["roster"]) == 33
    assert scope["bounds"]["test_start"] == "2025-01-01"
    assert scope["bounds"]["walk_forward_years"] == [2023, 2024]
    assert scope["bounds"]["maximum_candidates"] is None
