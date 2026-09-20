from __future__ import annotations

import importlib.util
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from subprocess import run
from typing import Any

import orjson
import pytest

from edgar_moe.platform_readiness import (
    CONTROL_IDS,
    PlatformReadinessError,
    build_platform_readiness,
    verify_platform_readiness,
)


def _load_script(name: str) -> Any:
    path = Path(__file__).parents[2] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"test_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_PLATFORM_BUILDER = _load_script("build_platform_readiness")
_PUBLIC_READINESS = _load_script("public_release_readiness")
build_from_paths = _PLATFORM_BUILDER.build_from_paths
build_public_release_readiness = _PUBLIC_READINESS.build_public_release_readiness


def _reports(*, statuses: dict[str, str] | None = None) -> dict[str, dict[str, Any]]:
    statuses = statuses or {}
    result: dict[str, dict[str, Any]] = {}
    for index, control_id in enumerate(CONTROL_IDS, start=1):
        status = statuses.get(control_id, "ready")
        result[control_id] = {
            "scope": {
                "public_release": "public_release_readiness",
                "operator_evidence": "operator_evidence_readiness",
                "research_copilot": "research_copilot_readiness",
                "prospective_drift": "research_drift_readiness",
            }[control_id],
            "status": status,
            "report_sha256": f"{index:064x}",
            "blocked_reasons": ["needs_review"] if status in {"blocked", "stale"} else [],
            "review_reasons": ["human_review"] if status == "review_required" else [],
        }
    return result


def test_platform_readiness_is_ready_only_when_every_control_is_ready() -> None:
    report = build_platform_readiness(
        _reports(), evaluated_at=datetime(2026, 9, 20, 8, 0, tzinfo=UTC)
    )

    assert report["status"] == "ready"
    assert report["blocked_reasons"] == []
    assert report["review_reasons"] == []
    assert report["v1_immutable"] is True
    assert report["automatic_retraining"] is False
    verify_platform_readiness(report)


def test_blocked_control_wins_over_review_required_control() -> None:
    report = build_platform_readiness(
        _reports(
            statuses={
                "operator_evidence": "blocked",
                "research_copilot": "review_required",
            }
        )
    )

    assert report["status"] == "blocked"
    assert "operator_evidence:blocked" in report["blocked_reasons"]
    assert report["review_reasons"] == [
        "research_copilot:review_required",
        "research_copilot:human_review",
    ]


def test_missing_control_is_explicitly_blocked() -> None:
    reports = _reports()
    reports["prospective_drift"] = None  # type: ignore[assignment]

    report = build_platform_readiness(reports)

    assert report["status"] == "blocked"
    assert report["blocked_reasons"] == [
        "prospective_drift:missing",
        "prospective_drift:report_missing",
    ]


def test_platform_readiness_rejects_tampering_and_unsafe_source_fields() -> None:
    report = build_platform_readiness(_reports(statuses={"operator_evidence": "blocked"}))
    tampered = deepcopy(report)
    tampered["status"] = "ready"
    with pytest.raises(PlatformReadinessError, match="status is inconsistent"):
        verify_platform_readiness(tampered)

    unsafe = _reports()
    unsafe["public_release"]["answer"] = "raw answer text"
    with pytest.raises(PlatformReadinessError, match="unsupported fields"):
        build_platform_readiness(unsafe)


def test_platform_readiness_rejects_invalid_control_maps_and_clock() -> None:
    incomplete = _reports()
    incomplete.pop("public_release")
    with pytest.raises(PlatformReadinessError, match="missing controls"):
        build_platform_readiness(incomplete)

    with pytest.raises(PlatformReadinessError, match="UTC offset"):
        build_platform_readiness(_reports(), evaluated_at=datetime(2026, 9, 20))

    with pytest.raises(PlatformReadinessError, match="must be a JSON object"):
        verify_platform_readiness(None)  # type: ignore[arg-type]

    invalid = build_platform_readiness(_reports())
    invalid.pop("disclaimer")
    with pytest.raises(PlatformReadinessError, match="missing fields"):
        verify_platform_readiness(invalid)

    invalid = build_platform_readiness(_reports())
    invalid["readiness_sha256"] = "0" * 64
    with pytest.raises(PlatformReadinessError, match="content hash mismatch"):
        verify_platform_readiness(invalid)


def test_source_statuses_require_safe_scopes_and_get_default_reason_codes() -> None:
    reports = _reports(
        statuses={
            "operator_evidence": "stale",
            "research_copilot": "review_required",
        }
    )
    reports["operator_evidence"]["blocked_reasons"] = []
    reports["research_copilot"]["review_reasons"] = []
    report = build_platform_readiness(reports)

    assert report["status"] == "blocked"
    assert "operator_evidence:source_report_stale" in report["blocked_reasons"]
    assert report["review_reasons"] == [
        "research_copilot:review_required",
        "research_copilot:source_report_review_required",
    ]

    invalid = _reports()
    invalid["public_release"]["scope"] = "wrong_scope"
    with pytest.raises(PlatformReadinessError, match="scope is invalid"):
        build_platform_readiness(invalid)


def test_platform_readiness_cli_can_retain_and_verify_a_blocked_decision(tmp_path: Path) -> None:
    output = tmp_path / "platform-readiness.json"
    built = run(
        [
            "uv",
            "run",
            "python",
            "scripts/build_platform_readiness.py",
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert built.returncode == 0, built.stderr
    summary = orjson.loads(built.stdout)
    assert summary["readiness_status"] == "blocked"
    assert output.is_file()

    verified = run(
        [
            "uv",
            "run",
            "python",
            "scripts/verify_platform_readiness.py",
            "--report",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert verified.returncode == 0, verified.stderr
    assert orjson.loads(verified.stdout)["readiness_status"] == "blocked"

    refused = run(
        [
            "uv",
            "run",
            "python",
            "scripts/build_platform_readiness.py",
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert refused.returncode == 2


def test_platform_builder_verifies_a_real_public_release_report(tmp_path: Path) -> None:
    public_report = build_public_release_readiness(repo_root=Path.cwd())
    public_path = tmp_path / "public-release.json"
    public_path.write_bytes(orjson.dumps(public_report))

    report = build_from_paths(
        {
            "public_release": public_path,
            "operator_evidence": None,
            "research_copilot": None,
            "prospective_drift": None,
        }
    )

    public_control = next(
        control for control in report["controls"] if control["control_id"] == "public_release"
    )
    assert public_control["report_sha256"] == public_report["readiness_sha256"]
    assert public_control["status"] == "review_required"
