"""Check provider-evidence secret groups without reading or printing values.

The provider audit workflows are intentionally manual and fail closed when a
secret is absent. This preflight gives an operator one cheap, local decision
point before starting those workflows. It reports only secret names and
stable issue codes; secret values are used in memory solely for presence and
identity-conflict checks.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

READER_SECRETS = ("EDGAR_MOE_REGISTRY_READ_DATABASE_URL",)
AUDITOR_SECRETS = (
    "EDGAR_MOE_REGISTRY_AUDITOR_DATABASE_URL",
    "EDGAR_MOE_R2_ENDPOINT_URL",
    "EDGAR_MOE_R2_BUCKET",
    "EDGAR_MOE_R2_AUDITOR_ACCESS_KEY_ID",
    "EDGAR_MOE_R2_AUDITOR_SECRET_ACCESS_KEY",
)
RESTORE_SECRETS = (
    "EDGAR_MOE_RESTORE_SOURCE_DATABASE_URL",
    "EDGAR_MOE_RESTORE_TARGET_DATABASE_URL",
    "EDGAR_MOE_RESTORE_SOURCE_AUDITOR_DATABASE_URL",
    "EDGAR_MOE_RESTORE_TARGET_AUDITOR_DATABASE_URL",
)
WRITER_SECRETS = (
    "EDGAR_MOE_REGISTRY_DATABASE_URL",
    "EDGAR_MOE_R2_ENDPOINT_URL",
    "EDGAR_MOE_R2_BUCKET",
    "EDGAR_MOE_R2_ACCESS_KEY_ID",
    "EDGAR_MOE_R2_SECRET_ACCESS_KEY",
)

GROUPS: dict[str, tuple[str, ...]] = {
    "reader_contract": READER_SECRETS,
    "r2_read_audit": AUDITOR_SECRETS,
    "restore_rehearsal": (*RESTORE_SECRETS, *AUDITOR_SECRETS),
    "partial_write_reconciliation": (*WRITER_SECRETS, *AUDITOR_SECRETS[:1], *AUDITOR_SECRETS[3:]),
}

# Endpoint and bucket names are intentionally shared by the writer and the
# auditor. Database URLs and access keys are identities, however, so reusing
# one silently widens the blast radius of a serving or audit credential.
IDENTITY_CONFLICTS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "reader_contract": (
        (
            "EDGAR_MOE_REGISTRY_READ_DATABASE_URL",
            "EDGAR_MOE_REGISTRY_DATABASE_URL",
            "reader_writer_database_identity_conflict",
        ),
    ),
    "r2_read_audit": (
        (
            "EDGAR_MOE_REGISTRY_AUDITOR_DATABASE_URL",
            "EDGAR_MOE_REGISTRY_DATABASE_URL",
            "auditor_writer_database_identity_conflict",
        ),
        (
            "EDGAR_MOE_R2_AUDITOR_ACCESS_KEY_ID",
            "EDGAR_MOE_R2_ACCESS_KEY_ID",
            "auditor_writer_r2_access_key_identity_conflict",
        ),
        (
            "EDGAR_MOE_R2_AUDITOR_SECRET_ACCESS_KEY",
            "EDGAR_MOE_R2_SECRET_ACCESS_KEY",
            "auditor_writer_r2_secret_key_identity_conflict",
        ),
    ),
    "partial_write_reconciliation": (
        (
            "EDGAR_MOE_REGISTRY_AUDITOR_DATABASE_URL",
            "EDGAR_MOE_REGISTRY_DATABASE_URL",
            "auditor_writer_database_identity_conflict",
        ),
        (
            "EDGAR_MOE_R2_AUDITOR_ACCESS_KEY_ID",
            "EDGAR_MOE_R2_ACCESS_KEY_ID",
            "auditor_writer_r2_access_key_identity_conflict",
        ),
        (
            "EDGAR_MOE_R2_AUDITOR_SECRET_ACCESS_KEY",
            "EDGAR_MOE_R2_SECRET_ACCESS_KEY",
            "auditor_writer_r2_secret_key_identity_conflict",
        ),
    ),
}


def check_prerequisites(environment: Mapping[str, str]) -> dict[str, Any]:
    """Return a non-sensitive readiness report for the provider workflows."""

    groups: list[dict[str, Any]] = []
    for group_id, secret_names in GROUPS.items():
        missing = [name for name in secret_names if not environment.get(name, "").strip()]
        violations: list[str] = []
        for left_name, right_name, code in IDENTITY_CONFLICTS.get(group_id, ()):
            _append_identity_conflict(violations, environment, left_name, right_name, code)
        if group_id == "restore_rehearsal":
            _append_identity_conflict(
                violations,
                environment,
                "EDGAR_MOE_RESTORE_SOURCE_DATABASE_URL",
                "EDGAR_MOE_RESTORE_TARGET_DATABASE_URL",
                "restore_source_target_database_identity_conflict",
            )
            _append_identity_conflict(
                violations,
                environment,
                "EDGAR_MOE_RESTORE_SOURCE_AUDITOR_DATABASE_URL",
                "EDGAR_MOE_RESTORE_TARGET_AUDITOR_DATABASE_URL",
                "restore_source_target_auditor_identity_conflict",
            )
            _append_identity_conflict(
                violations,
                environment,
                "EDGAR_MOE_RESTORE_SOURCE_DATABASE_URL",
                "EDGAR_MOE_RESTORE_SOURCE_AUDITOR_DATABASE_URL",
                "restore_source_writer_auditor_identity_conflict",
            )
            _append_identity_conflict(
                violations,
                environment,
                "EDGAR_MOE_RESTORE_TARGET_DATABASE_URL",
                "EDGAR_MOE_RESTORE_TARGET_AUDITOR_DATABASE_URL",
                "restore_target_writer_auditor_identity_conflict",
            )

        status = "configured" if not missing and not violations else "blocked"
        groups.append(
            {
                "group_id": group_id,
                "status": status,
                "required_secret_names": list(secret_names),
                "missing_secret_names": missing,
                "violations": violations,
            }
        )

    blocked_groups = [group["group_id"] for group in groups if group["status"] != "configured"]
    return {
        "schema_version": 1,
        "status": "ready_to_run" if not blocked_groups else "blocked",
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "groups": groups,
        "blocked_groups": blocked_groups,
        "provider_assertions": {
            "database_grants": "not_observed",
            "restore_target_is_isolated": "requires_workflow_confirmation",
            "r2_permissions": "not_observed",
            "backup_rpo_rto": "not_observed",
        },
        "note": (
            "Configured secret names do not prove provider grants, network reachability, "
            "backup behavior, or target isolation. Run the corresponding manual workflow "
            "and retain its redacted artifact."
        ),
    }


def _append_identity_conflict(
    violations: list[str],
    environment: Mapping[str, str],
    left_name: str,
    right_name: str,
    code: str,
) -> None:
    left = environment.get(left_name, "").strip()
    right = environment.get(right_name, "").strip()
    if left and right and left == right:
        violations.append(code)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        help="optional JSON report destination; the report never contains secret values",
    )
    args = parser.parse_args()
    report = check_prerequisites(os.environ)
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
        print(f"Wrote provider evidence preflight to {args.output}")
    print(serialized, end="")
    return 0 if report["status"] == "ready_to_run" else 1


if __name__ == "__main__":
    raise SystemExit(main())
