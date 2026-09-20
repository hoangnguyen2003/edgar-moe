from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "public_release_readiness.py"
_SPEC = importlib.util.spec_from_file_location("public_release_readiness", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
PublicReleaseReadinessError = _MODULE.PublicReleaseReadinessError
build_public_release_readiness = _MODULE.build_public_release_readiness
verify_public_release_readiness = _MODULE.verify_public_release_readiness


def _copy_release_inputs(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    shutil.copytree(Path("public"), repo_root / "public")
    (repo_root / "data" / "demo").mkdir(parents=True)
    shutil.copy2("data/demo/snapshot.json", repo_root / "data/demo/snapshot.json")
    (repo_root / "config").mkdir()
    shutil.copy2(
        "config/public_snapshot.lock.json",
        repo_root / "config/public_snapshot.lock.json",
    )
    return repo_root


def test_current_public_release_requires_redistribution_review() -> None:
    report = build_public_release_readiness(repo_root=Path.cwd())

    assert report["status"] == "review_required"
    assert report["checks"][0]["status"] == "passed"
    assert report["checks"][1]["status"] == "passed"
    assert report["checks"][2]["status"] == "review_required"
    verify_public_release_readiness(report)


def test_public_release_blocks_missing_immutable_inputs(tmp_path: Path) -> None:
    repo_root = _copy_release_inputs(tmp_path)
    (repo_root / "config" / "public_snapshot.lock.json").unlink()

    report = build_public_release_readiness(repo_root=repo_root)

    assert report["status"] == "blocked"
    assert "snapshot_lock_contract_failed" in report["blocked_reasons"]


def test_public_release_is_ready_only_for_explicit_approval(tmp_path: Path) -> None:
    repo_root = _copy_release_inputs(tmp_path)
    manifest_path = repo_root / "public" / "data-provenance.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["review"] = {
        "redistribution_status": "approved",
        "legal_approval": True,
        "last_reviewed_at": "2026-09-20T00:00:00Z",
    }
    for source in manifest["sources"]:
        source["redistribution_status"] = "approved"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    report = build_public_release_readiness(repo_root=repo_root)

    assert report["status"] == "ready"
    assert report["review"]["status"] == "passed"
    assert report["review"]["source_review_required"] == 0


def test_public_release_rejects_tampered_hash_and_unknown_fields() -> None:
    report = build_public_release_readiness(repo_root=Path.cwd())

    tampered = dict(report)
    tampered["identity"] = {"path": "data/demo/snapshot.json", "sha256": "b" * 64}
    with pytest.raises(PublicReleaseReadinessError, match="content hash mismatch"):
        verify_public_release_readiness(tampered)

    unknown = dict(report)
    unknown["unexpected"] = True
    with pytest.raises(PublicReleaseReadinessError, match="unknown fields"):
        verify_public_release_readiness(unknown)
