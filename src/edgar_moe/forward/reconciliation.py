"""Registry-driven verification and repair of forward evidence objects."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from edgar_moe.forward.artifacts import ArtifactReference, ArtifactStore, LocalArtifactStore
from edgar_moe.forward.registry import ForwardRegistry


class ArtifactReconciliationError(RuntimeError):
    """Raised when reconciliation cannot be configured safely."""


def reconcile_registry_artifacts(
    registry: ForwardRegistry,
    local: LocalArtifactStore,
    *,
    mirror: ArtifactStore | None = None,
    repair: bool = False,
) -> dict[str, Any]:
    """Verify registry-referenced local objects and optionally mirror them.

    The registry is read only.  ``repair=True`` writes only bytes that first
    pass the local content hash and size checks, and confirms that the mirror
    returns the same content identity.  A missing or corrupt local object is a
    finding; it is never replaced with guessed data.
    """

    if repair and mirror is None:
        raise ArtifactReconciliationError("repair requires an artifact mirror")

    issues: list[dict[str, Any]] = []
    verified = 0
    mirrored = 0
    artifacts = registry.list_artifacts()
    for record in artifacts:
        reference, issue = _local_reference(record)
        if issue is not None:
            issues.append(issue)
            continue
        assert reference is not None
        try:
            content = local.read_bytes(reference)
        except FileNotFoundError:
            issues.append(_issue(record, "missing_local_object"))
            continue
        except (OSError, ValueError):
            issues.append(_issue(record, "invalid_local_object"))
            continue
        if len(content) != int(record["size_bytes"]):
            issues.append(_issue(record, "size_mismatch"))
            continue

        verified += 1
        if not repair:
            continue
        assert mirror is not None
        logical_name = PurePosixPath(reference.key).name
        try:
            mirrored_reference = mirror.put_bytes(content, logical_name=logical_name)
        except Exception as error:  # noqa: BLE001 - redact provider-specific failures
            issues.append(
                _issue(
                    record,
                    "mirror_write_failed",
                    error_type=type(error).__name__,
                )
            )
            continue
        expected_identity = (reference.sha256, reference.size_bytes, reference.key)
        observed_identity = (
            mirrored_reference.sha256,
            mirrored_reference.size_bytes,
            mirrored_reference.key,
        )
        if observed_identity != expected_identity:
            issues.append(_issue(record, "mirror_identity_mismatch"))
            continue
        mirrored += 1

    status = "passed" if not issues else "failed"
    return {
        "schema_version": 1,
        "reconciliation_type": "registry_referenced_local_artifacts",
        "scope": "local_primary_only",
        "status": status,
        "observed_at": datetime.now(UTC).isoformat(),
        "repair_requested": repair,
        "expected_artifacts": len(artifacts),
        "verified_artifacts": verified,
        "mirrored_artifacts": mirrored,
        "issue_count": len(issues),
        "issues": issues,
        "limitations": [
            "Unreferenced local objects are not evidence and are not included in the report.",
            "A missing local object requires recovery from an independently verified source.",
            "Use the Go evidence auditor to verify the durable R2 object store.",
        ],
    }


def _local_reference(
    record: dict[str, Any],
) -> tuple[ArtifactReference | None, dict[str, Any] | None]:
    uri = str(record["uri"])
    if not uri.startswith("local://"):
        return None, _issue(record, "unsupported_primary_uri", uri_scheme=uri.split(":", 1)[0])
    key = uri.removeprefix("local://")
    if not key or key.startswith("/") or ".." in PurePosixPath(key).parts:
        return None, _issue(record, "invalid_local_reference")
    return (
        ArtifactReference(
            uri=uri,
            sha256=str(record["sha256"]),
            size_bytes=int(record["size_bytes"]),
            key=key,
        ),
        None,
    )


def _issue(record: dict[str, Any], code: str, **extra: Any) -> dict[str, Any]:
    issue: dict[str, Any] = {
        "artifact_id": str(record["artifact_id"]),
        "run_id": str(record["run_id"]),
        "kind": str(record["kind"]),
        "code": code,
        "sha256": str(record["sha256"]),
        "size_bytes": int(record["size_bytes"]),
    }
    issue.update(extra)
    return issue
