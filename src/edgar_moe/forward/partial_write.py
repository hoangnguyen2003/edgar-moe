"""Preserve the primary reference before recording a mirror-write failure."""

from __future__ import annotations

from edgar_moe.forward.artifacts import ArtifactWriteError
from edgar_moe.forward.registry import ForwardRegistry


def record_partial_artifact(
    registry: ForwardRegistry, run_id: str, *, kind: str, error: ArtifactWriteError
) -> None:
    """Shared production/rehearsal path; never relabel a failed run as successful."""
    try:
        registry.register_artifact(run_id, kind=kind, reference=error.primary_reference)
    except Exception as registration_error:  # noqa: BLE001 - preserve original failure
        registry.fail_run(
            run_id,
            error_message=(
                "ArtifactWriteError: mirror write failed and primary artifact "
                "registration failed "
                f"({type(registration_error).__name__})"
            ),
        )
        return
    registry.fail_run(
        run_id,
        error_message=(
            "ArtifactWriteError: mirror write failed; primary artifact was "
            "recorded for reconciliation "
            f"(cause={error.cause_type})"
        ),
    )
