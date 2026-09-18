from __future__ import annotations

from pathlib import Path

import pytest

from edgar_moe.forward.artifacts import (
    ArtifactReference,
    ArtifactStore,
    LocalArtifactStore,
    MirroredArtifactStore,
    mirror_local_artifacts,
)


class FailOnceStore:
    """Mirror fixture that fails once, then behaves like a local store."""

    def __init__(self, root: Path) -> None:
        self.delegate = LocalArtifactStore(root)
        self.fail_next = True

    def put_file(
        self, source: str | Path, *, logical_name: str | None = None
    ) -> ArtifactReference:
        return self.delegate.put_file(source, logical_name=logical_name)

    def put_bytes(self, content: bytes, *, logical_name: str) -> ArtifactReference:
        if self.fail_next:
            self.fail_next = False
            raise OSError("injected mirror outage")
        return self.delegate.put_bytes(content, logical_name=logical_name)

    def read_bytes(self, reference: ArtifactReference) -> bytes:
        return self.delegate.read_bytes(reference)


def test_mirrored_store_preserves_primary_identity_and_falls_back(tmp_path: Path) -> None:
    primary = LocalArtifactStore(tmp_path / "primary")
    mirror = LocalArtifactStore(tmp_path / "mirror")
    store = MirroredArtifactStore(primary, mirror)

    reference = store.put_bytes(b"immutable evidence", logical_name="evidence.json")

    assert reference.uri.startswith("local://")
    assert mirror.read_bytes(reference) == b"immutable evidence"
    (primary.root / reference.key).unlink()
    assert store.read_bytes(reference) == b"immutable evidence"


def test_mirror_failure_leaves_primary_content_retryable(tmp_path: Path) -> None:
    primary = LocalArtifactStore(tmp_path / "primary")
    mirror = FailOnceStore(tmp_path / "mirror")
    store: ArtifactStore = MirroredArtifactStore(primary, mirror)

    with pytest.raises(OSError, match="injected mirror outage"):
        store.put_bytes(b"retryable evidence", logical_name="evidence.json")

    # The first attempt does not register an artifact, but its immutable primary
    # bytes remain available. A retry mirrors the same content identity exactly.
    reference = store.put_bytes(b"retryable evidence", logical_name="evidence.json")
    assert primary.read_bytes(reference) == b"retryable evidence"
    assert mirror.read_bytes(reference) == b"retryable evidence"


def test_existing_local_artifacts_are_mirrored_and_verified(tmp_path: Path) -> None:
    local = LocalArtifactStore(tmp_path / "local")
    mirror = LocalArtifactStore(tmp_path / "mirror")
    local.put_bytes(b"one", logical_name="one.json")
    local.put_bytes(b"two", logical_name="two.json")

    result = mirror_local_artifacts(local, mirror)

    assert result == {"objects": 2, "bytes": 6}
    assert len(list((mirror.root / "sha256").rglob("*.json"))) == 2


def test_local_artifact_mirror_rejects_path_hash_mismatch(tmp_path: Path) -> None:
    local = LocalArtifactStore(tmp_path / "local")
    mirror = LocalArtifactStore(tmp_path / "mirror")
    reference = local.put_bytes(b"evidence", logical_name="evidence.json")
    (local.root / reference.key).write_bytes(b"tampered")

    with pytest.raises(ValueError, match="path/hash mismatch"):
        mirror_local_artifacts(local, mirror)
