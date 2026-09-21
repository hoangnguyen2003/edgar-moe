from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "check_provider_evidence_prerequisites.py"
_SPEC = importlib.util.spec_from_file_location(
    "check_provider_evidence_prerequisites", _SCRIPT_PATH
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def _configured_environment() -> dict[str, str]:
    values = {
        name: f"configured-{index}"
        for index, name in enumerate(_MODULE.GROUPS["restore_rehearsal"])
    }
    values.update({name: "configured-reader" for name in _MODULE.READER_SECRETS})
    values.update(
        {
            "EDGAR_MOE_REGISTRY_DATABASE_URL": "writer-database-url",
            "EDGAR_MOE_R2_ACCESS_KEY_ID": "writer-access-key",
            "EDGAR_MOE_R2_SECRET_ACCESS_KEY": "writer-secret-key",
        }
    )
    return values


def test_complete_secret_groups_are_reported_without_values() -> None:
    environment = _configured_environment()

    report = _MODULE.check_prerequisites(environment)

    assert report["status"] == "ready_to_run"
    assert report["blocked_groups"] == []
    assert all(group["status"] == "configured" for group in report["groups"])
    serialized = json.dumps(report)
    assert "writer-database-url" not in serialized
    assert "writer-secret-key" not in serialized


def test_missing_secret_names_are_reported_and_values_are_never_echoed() -> None:
    environment = {"EDGAR_MOE_REGISTRY_READ_DATABASE_URL": "reader-password-in-url"}

    report = _MODULE.check_prerequisites(environment)

    assert report["status"] == "blocked"
    assert set(report["blocked_groups"]) == {
        "r2_read_audit",
        "restore_rehearsal",
        "partial_write_reconciliation",
    }
    reader = next(group for group in report["groups"] if group["group_id"] == "reader_contract")
    assert reader["status"] == "configured"
    assert (
        "EDGAR_MOE_R2_AUDITOR_SECRET_ACCESS_KEY"
        in next(group for group in report["groups"] if group["group_id"] == "r2_read_audit")[
            "missing_secret_names"
        ]
    )
    assert "reader-password-in-url" not in json.dumps(report)


def test_restore_identity_conflicts_block_preflight_without_disclosing_urls() -> None:
    environment = _configured_environment()
    environment["EDGAR_MOE_RESTORE_TARGET_DATABASE_URL"] = environment[
        "EDGAR_MOE_RESTORE_SOURCE_DATABASE_URL"
    ]
    environment["EDGAR_MOE_RESTORE_TARGET_AUDITOR_DATABASE_URL"] = environment[
        "EDGAR_MOE_RESTORE_SOURCE_AUDITOR_DATABASE_URL"
    ]

    report = _MODULE.check_prerequisites(environment)

    restore = next(group for group in report["groups"] if group["group_id"] == "restore_rehearsal")
    assert restore["status"] == "blocked"
    assert restore["violations"] == [
        "restore_source_target_database_identity_conflict",
        "restore_source_target_auditor_identity_conflict",
    ]
    assert report["status"] == "blocked"
    assert environment["EDGAR_MOE_RESTORE_SOURCE_DATABASE_URL"] not in json.dumps(report)
