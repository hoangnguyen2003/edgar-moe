"""What happens to evidence when the durable mirror misbehaves.

Run artifacts are the evidence behind every recorded forecast. The mirrored
store exists so a local copy and a provider copy hold the same bytes, and its
failure handling is deliberate: the primary reference survives a mirror failure
so reconciliation can repair the copy instead of the run losing its evidence.
None of those branches were covered, so the guarantee was only a comment.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from edgar_moe.forward.artifacts import (
    ArtifactReference,
    ArtifactWriteError,
    LocalArtifactStore,
    MirroredArtifactStore,
    artifact_store_from_settings,
    mirror_local_artifacts,
)
from edgar_moe.settings import RuntimeSettings


class _FailingStore:
    """A mirror that refuses every write, the way an outage or a bad key does."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def put_file(self, source: str | Path, *, logical_name: str | None = None) -> ArtifactReference:
        raise self.error

    def put_bytes(self, content: bytes, *, logical_name: str) -> ArtifactReference:
        raise self.error

    def read_bytes(self, reference: ArtifactReference) -> bytes:
        raise self.error


class _WrongIdentityStore:
    """A mirror that accepts the write but reports different content."""

    def __init__(self, **overrides: object) -> None:
        self.overrides = overrides

    def _reference(self) -> ArtifactReference:
        base = {
            "uri": "mirror://bucket/sha256/aa/" + "a" * 64 + "/run.json",
            "sha256": "a" * 64,
            "size_bytes": 11,
            "key": "sha256/aa/" + "a" * 64 + "/run.json",
        }
        base.update(self.overrides)  # type: ignore[arg-type]
        return ArtifactReference(**base)  # type: ignore[arg-type]

    def put_file(self, source: str | Path, *, logical_name: str | None = None) -> ArtifactReference:
        return self._reference()

    def put_bytes(self, content: bytes, *, logical_name: str) -> ArtifactReference:
        return self._reference()

    def read_bytes(self, reference: ArtifactReference) -> bytes:
        return b"mirror copy"


def _source(tmp_path: Path, content: bytes = b"run evidence") -> Path:
    path = tmp_path / "run.json"
    path.write_bytes(content)
    return path


def test_a_write_reaches_both_stores_and_reports_the_primary(tmp_path: Path) -> None:
    primary = LocalArtifactStore(tmp_path / "primary")
    mirror = LocalArtifactStore(tmp_path / "mirror")
    store = MirroredArtifactStore(primary, mirror)

    reference = store.put_file(_source(tmp_path))

    assert reference.uri.startswith("local://")
    assert primary.read_bytes(reference) == b"run evidence"
    assert mirror.read_bytes(reference) == b"run evidence"


def test_a_mirror_failure_keeps_the_primary_reference_for_reconciliation(tmp_path: Path) -> None:
    store = MirroredArtifactStore(
        LocalArtifactStore(tmp_path / "primary"),
        _FailingStore(TimeoutError("provider timed out")),
    )

    with pytest.raises(ArtifactWriteError) as raised:
        store.put_file(_source(tmp_path))

    # The local bytes are durable, so the run can still register its evidence.
    assert raised.value.primary_reference.sha256
    assert raised.value.cause_type == "TimeoutError"
    # The message must not carry an endpoint, a bucket, or an SDK error body.
    assert str(raised.value) == "Artifact mirror write failed after the primary write"
    assert "provider timed out" not in str(raised.value)


def test_a_mirror_failure_on_bytes_is_reported_the_same_way(tmp_path: Path) -> None:
    store = MirroredArtifactStore(
        LocalArtifactStore(tmp_path / "primary"),
        _FailingStore(ConnectionResetError("reset")),
    )

    with pytest.raises(ArtifactWriteError) as raised:
        store.put_bytes(b"run evidence", logical_name="run.json")

    assert raised.value.cause_type == "ConnectionResetError"
    assert raised.value.primary_reference.size_bytes == len(b"run evidence")


@pytest.mark.parametrize(
    "overrides",
    [
        {"sha256": "b" * 64},
        {"size_bytes": 12},
        {"key": "sha256/aa/" + "a" * 64 + "/other.json"},
    ],
)
def test_a_mirror_that_stored_something_else_is_a_write_failure(
    tmp_path: Path,
    overrides: dict[str, object],
) -> None:
    # A mirror reporting a different digest, size, or key has not made the
    # second copy the evidence claim depends on.
    store = MirroredArtifactStore(
        LocalArtifactStore(tmp_path / "primary"),
        _WrongIdentityStore(**overrides),
    )

    with pytest.raises(ArtifactWriteError) as raised:
        store.put_bytes(b"run evidence", logical_name="run.json")

    assert raised.value.cause_type == "identity_mismatch"
    assert raised.value.primary_reference.uri.startswith("local://")


def test_a_file_write_checks_the_mirror_identity_too(tmp_path: Path) -> None:
    store = MirroredArtifactStore(
        LocalArtifactStore(tmp_path / "primary"),
        _WrongIdentityStore(sha256="c" * 64),
    )

    with pytest.raises(ArtifactWriteError) as raised:
        store.put_file(_source(tmp_path))

    assert raised.value.cause_type == "identity_mismatch"


def test_a_read_falls_back_to_the_mirror_when_the_primary_is_gone(tmp_path: Path) -> None:
    primary = LocalArtifactStore(tmp_path / "primary")
    store = MirroredArtifactStore(primary, _WrongIdentityStore())
    reference = primary.put_bytes(b"run evidence", logical_name="run.json")

    for path in (primary.root / "sha256").rglob("*"):
        if path.is_file():
            path.unlink()

    assert store.read_bytes(reference) == b"mirror copy"


def test_local_artifacts_are_mirrored_once_each_and_counted(tmp_path: Path) -> None:
    local = LocalArtifactStore(tmp_path / "primary")
    mirror = LocalArtifactStore(tmp_path / "mirror")
    local.put_bytes(b"first", logical_name="a.json")
    local.put_bytes(b"second evidence", logical_name="b.json")

    summary = mirror_local_artifacts(local, mirror)

    assert summary == {"objects": 2, "bytes": len(b"first") + len(b"second evidence")}


def test_mirroring_an_empty_store_is_not_an_error(tmp_path: Path) -> None:
    local = LocalArtifactStore(tmp_path / "primary")

    assert mirror_local_artifacts(local, LocalArtifactStore(tmp_path / "mirror")) == {
        "objects": 0,
        "bytes": 0,
    }


def test_a_local_object_whose_path_disagrees_with_its_bytes_stops_the_mirror(
    tmp_path: Path,
) -> None:
    # A content-addressed path that no longer matches its contents means the
    # local store was edited; copying it would spread the damage.
    local = LocalArtifactStore(tmp_path / "primary")
    reference = local.put_bytes(b"run evidence", logical_name="run.json")
    (local.root / reference.key).write_bytes(b"tampered")

    with pytest.raises(ValueError, match="path/hash mismatch"):
        mirror_local_artifacts(local, LocalArtifactStore(tmp_path / "mirror"))


def test_an_unexpected_local_layout_stops_the_mirror(tmp_path: Path) -> None:
    local = LocalArtifactStore(tmp_path / "primary")
    stray = local.root / "sha256" / "stray.json"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(b"stray")

    with pytest.raises(ValueError, match="Unexpected local artifact path"):
        mirror_local_artifacts(local, LocalArtifactStore(tmp_path / "mirror"))


def _settings(**overrides: object) -> RuntimeSettings:
    return RuntimeSettings(**overrides)  # type: ignore[arg-type]


def test_the_default_configuration_writes_locally_with_no_mirror(tmp_path: Path) -> None:
    store = artifact_store_from_settings(_settings(edgar_moe_artifact_dir=tmp_path / "artifacts"))

    assert isinstance(store, LocalArtifactStore)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"edgar_moe_artifact_backend": "s3"}, "Unsupported artifact backend"),
        ({"edgar_moe_artifact_mirror_backend": "gcs"}, "Unsupported artifact mirror backend"),
        (
            {
                "edgar_moe_artifact_backend": "r2",
                "edgar_moe_artifact_mirror_backend": "r2",
            },
            "must be different",
        ),
    ],
)
def test_an_unusable_artifact_configuration_is_refused(
    tmp_path: Path,
    overrides: dict[str, object],
    message: str,
) -> None:
    settings = _settings(edgar_moe_artifact_dir=tmp_path / "artifacts", **overrides)

    with pytest.raises(ValueError, match=message):
        artifact_store_from_settings(settings)
