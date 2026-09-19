from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from uuid import uuid4

from edgar_moe.settings import RuntimeSettings


@dataclass(frozen=True)
class ArtifactReference:
    uri: str
    sha256: str
    size_bytes: int
    key: str


class ArtifactWriteError(OSError):
    """Raised when a mirrored write fails after the primary write succeeded.

    The primary reference is intentionally retained so the caller can register
    the durable local bytes and let the reconciliation job repair the mirror.
    The public error text stays provider-neutral; the concrete exception type is
    enough for private logs and failure classification without leaking endpoint
    names or credential-bearing SDK messages into the registry.
    """

    def __init__(self, *, primary_reference: ArtifactReference, cause_type: str) -> None:
        super().__init__("Artifact mirror write failed after the primary write")
        self.primary_reference = primary_reference
        self.cause_type = cause_type


class ArtifactStore(Protocol):
    def put_file(
        self, source: str | Path, *, logical_name: str | None = None
    ) -> ArtifactReference: ...

    def put_bytes(self, content: bytes, *, logical_name: str) -> ArtifactReference: ...

    def read_bytes(self, reference: ArtifactReference) -> bytes: ...


class LocalArtifactStore:
    """Atomic, content-addressed artifact storage for development and CI."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put_file(
        self,
        source: str | Path,
        *,
        logical_name: str | None = None,
    ) -> ArtifactReference:
        source_path = Path(source)
        digest, size_bytes = _hash_file(source_path)
        key = _content_key(digest, logical_name or source_path.name)
        destination = self._path(key)
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
            try:
                shutil.copyfile(source_path, temporary)
                copied_digest, copied_size = _hash_file(temporary)
                if copied_digest != digest or copied_size != size_bytes:
                    raise OSError("Artifact changed while it was being copied")
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
        return ArtifactReference(
            uri=f"local://{key}",
            sha256=digest,
            size_bytes=size_bytes,
            key=key,
        )

    def put_bytes(self, content: bytes, *, logical_name: str) -> ArtifactReference:
        digest = hashlib.sha256(content).hexdigest()
        key = _content_key(digest, logical_name)
        destination = self._path(key)
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
            try:
                temporary.write_bytes(content)
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
        return ArtifactReference(
            uri=f"local://{key}",
            sha256=digest,
            size_bytes=len(content),
            key=key,
        )

    def read_bytes(self, reference: ArtifactReference) -> bytes:
        path = self._path(reference.key)
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != reference.sha256:
            raise ValueError("Stored artifact hash verification failed")
        return content

    def _path(self, key: str) -> Path:
        destination = (self.root / key).resolve()
        if not destination.is_relative_to(self.root):
            raise ValueError("Artifact key escapes the configured root")
        return destination


class R2ArtifactStore:
    """Cloudflare R2 adapter using the S3-compatible API."""

    def __init__(
        self,
        *,
        endpoint_url: str,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
    ) -> None:
        if not all((endpoint_url, bucket, access_key_id, secret_access_key)):
            raise ValueError("R2 endpoint, bucket, access key, and secret key are required")
        try:
            import boto3
        except ImportError as error:  # pragma: no cover - optional dependency path
            raise RuntimeError(
                "Install operations dependencies with `uv sync --extra operations`"
            ) from error
        self.bucket = bucket
        self.client: Any = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
        )

    def put_file(
        self,
        source: str | Path,
        *,
        logical_name: str | None = None,
    ) -> ArtifactReference:
        source_path = Path(source)
        digest, size_bytes = _hash_file(source_path)
        key = _content_key(digest, logical_name or source_path.name)
        if not self._exists_with_hash(key, digest):
            self.client.upload_file(
                str(source_path),
                self.bucket,
                key,
                ExtraArgs={"Metadata": {"sha256": digest}},
            )
        return ArtifactReference(
            uri=f"r2://{self.bucket}/{key}",
            sha256=digest,
            size_bytes=size_bytes,
            key=key,
        )

    def put_bytes(self, content: bytes, *, logical_name: str) -> ArtifactReference:
        digest = hashlib.sha256(content).hexdigest()
        key = _content_key(digest, logical_name)
        if not self._exists_with_hash(key, digest):
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=content,
                Metadata={"sha256": digest},
            )
        return ArtifactReference(
            uri=f"r2://{self.bucket}/{key}",
            sha256=digest,
            size_bytes=len(content),
            key=key,
        )

    def read_bytes(self, reference: ArtifactReference) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=reference.key)
        content = bytes(response["Body"].read())
        if hashlib.sha256(content).hexdigest() != reference.sha256:
            raise ValueError("Stored R2 artifact hash verification failed")
        return content

    def _exists_with_hash(self, key: str, digest: str) -> bool:
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=key)
        except self.client.exceptions.ClientError as error:
            status = int(error.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
            if status == 404:
                return False
            raise
        stored_hash = str(response.get("Metadata", {}).get("sha256", ""))
        if stored_hash != digest:
            raise ValueError("Existing R2 object has unexpected hash metadata")
        return True


class MirroredArtifactStore:
    """Write through to a primary store and a hash-equivalent durable mirror."""

    def __init__(self, primary: ArtifactStore, mirror: ArtifactStore) -> None:
        self.primary = primary
        self.mirror = mirror

    def put_file(
        self,
        source: str | Path,
        *,
        logical_name: str | None = None,
    ) -> ArtifactReference:
        primary = self.primary.put_file(source, logical_name=logical_name)
        try:
            mirror = self.mirror.put_file(source, logical_name=logical_name)
        except Exception as error:  # noqa: BLE001 - preserve primary for reconciliation
            raise ArtifactWriteError(
                primary_reference=primary,
                cause_type=type(error).__name__,
            ) from error
        try:
            _require_equivalent_references(primary, mirror)
        except ValueError as error:
            raise ArtifactWriteError(
                primary_reference=primary,
                cause_type="identity_mismatch",
            ) from error
        return primary

    def put_bytes(self, content: bytes, *, logical_name: str) -> ArtifactReference:
        primary = self.primary.put_bytes(content, logical_name=logical_name)
        try:
            mirror = self.mirror.put_bytes(content, logical_name=logical_name)
        except Exception as error:  # noqa: BLE001 - preserve primary for reconciliation
            raise ArtifactWriteError(
                primary_reference=primary,
                cause_type=type(error).__name__,
            ) from error
        try:
            _require_equivalent_references(primary, mirror)
        except ValueError as error:
            raise ArtifactWriteError(
                primary_reference=primary,
                cause_type="identity_mismatch",
            ) from error
        return primary

    def read_bytes(self, reference: ArtifactReference) -> bytes:
        try:
            return self.primary.read_bytes(reference)
        except (FileNotFoundError, OSError):
            return self.mirror.read_bytes(reference)


def artifact_store_from_settings(settings: RuntimeSettings) -> ArtifactStore:
    backend = settings.edgar_moe_artifact_backend.strip().lower()
    if backend == "local":
        primary: ArtifactStore = LocalArtifactStore(settings.edgar_moe_artifact_dir)
    elif backend == "r2":
        primary = _r2_store(settings)
    else:
        raise ValueError(f"Unsupported artifact backend: {backend}")

    mirror_backend = settings.edgar_moe_artifact_mirror_backend.strip().lower()
    if mirror_backend in {"", "none"}:
        return primary
    if mirror_backend != "r2":
        raise ValueError(f"Unsupported artifact mirror backend: {mirror_backend}")
    if backend == "r2":
        raise ValueError("Primary and mirror artifact backends must be different")
    return MirroredArtifactStore(primary, _r2_store(settings))


def mirror_local_artifacts(
    local: LocalArtifactStore,
    mirror: ArtifactStore,
) -> dict[str, int]:
    """Copy every valid local content-addressed object to a verified mirror."""
    objects = 0
    bytes_mirrored = 0
    content_root = local.root / "sha256"
    if not content_root.exists():
        return {"objects": 0, "bytes": 0}
    for source in sorted(path for path in content_root.rglob("*") if path.is_file()):
        relative = source.relative_to(local.root)
        parts = relative.parts
        if len(parts) != 4 or parts[0] != "sha256":
            raise ValueError(f"Unexpected local artifact path: {relative}")
        expected_digest = parts[2]
        digest, size_bytes = _hash_file(source)
        if len(expected_digest) != 64 or digest != expected_digest:
            raise ValueError(f"Local artifact path/hash mismatch: {relative}")
        local_reference = ArtifactReference(
            uri=f"local://{relative.as_posix()}",
            sha256=digest,
            size_bytes=size_bytes,
            key=relative.as_posix(),
        )
        mirror_reference = mirror.put_file(source, logical_name=source.name)
        _require_equivalent_references(local_reference, mirror_reference)
        objects += 1
        bytes_mirrored += size_bytes
    return {"objects": objects, "bytes": bytes_mirrored}


def _r2_store(settings: RuntimeSettings) -> R2ArtifactStore:
    return R2ArtifactStore(
        endpoint_url=settings.edgar_moe_r2_endpoint_url,
        bucket=settings.edgar_moe_r2_bucket,
        access_key_id=settings.edgar_moe_r2_access_key_id,
        secret_access_key=settings.edgar_moe_r2_secret_access_key,
    )


def _require_equivalent_references(
    primary: ArtifactReference,
    mirror: ArtifactReference,
) -> None:
    expected = (primary.sha256, primary.size_bytes, primary.key)
    observed = (mirror.sha256, mirror.size_bytes, mirror.key)
    if observed != expected:
        raise ValueError("Artifact mirror returned a different content identity")


def _content_key(digest: str, logical_name: str) -> str:
    name = PurePosixPath(logical_name).name
    if not name or name in {".", ".."}:
        raise ValueError("Artifact logical name must contain a file name")
    return f"sha256/{digest[:2]}/{digest}/{name}"


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size_bytes = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size_bytes += len(chunk)
    return digest.hexdigest(), size_bytes
