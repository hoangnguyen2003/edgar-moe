from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_restore_fixture_seed_and_count_export_are_deterministic(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'registry.sqlite3'}"
    artifact_root = tmp_path / "artifacts"

    seed = subprocess.run(
        [
            sys.executable,
            "scripts/seed_forward_restore_fixture.py",
            "--database-url",
            database_url,
            "--artifact-root",
            str(artifact_root),
            "--allow-synthetic",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    counts_path = tmp_path / "counts.json"
    subprocess.run(
        [
            sys.executable,
            "scripts/export_forward_registry_counts.py",
            "--database-url",
            database_url,
            "--output",
            str(counts_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(seed.stdout)
    counts = json.loads(counts_path.read_text(encoding="utf-8"))

    assert result["run_id"] == "restore-fixture-run"
    assert result["forecast_count"] == 1
    assert counts["forward_runs"] == 1
    assert counts["forward_forecasts"] == 1
    assert counts["forward_artifacts"] == 1
    assert counts["forward_labels"] == 0


def test_restore_fixture_rejects_nonempty_artifact_root(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    (root / "unexpected").write_bytes(b"do not overwrite")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/seed_forward_restore_fixture.py",
            "--database-url",
            f"sqlite:///{tmp_path / 'registry.sqlite3'}",
            "--artifact-root",
            str(root),
            "--allow-synthetic",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "must be empty" in result.stderr
