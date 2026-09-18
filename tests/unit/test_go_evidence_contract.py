"""Both Go and Python consume the same byte-level artifact identity fixture."""

import json
from pathlib import Path

from edgar_moe.forward.artifacts import LocalArtifactStore


def test_go_fixture_matches_python_artifact_identity(tmp_path: Path) -> None:
    fixture = Path(__file__).resolve().parents[2] / "tools/evidence-auditor/testdata"
    manifest = json.loads((fixture / "manifest.json").read_text())
    expected = manifest["artifacts"][0]
    actual = LocalArtifactStore(tmp_path).put_bytes(
        (fixture / "evidence.json").read_bytes(), logical_name="forecast-batch.json"
    )
    assert actual.uri == expected["uri"]
    assert actual.sha256 == expected["sha256"]
    assert actual.size_bytes == expected["size_bytes"]
