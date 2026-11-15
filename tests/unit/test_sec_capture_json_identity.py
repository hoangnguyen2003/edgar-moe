"""Offline regressions for ambiguous or type-substituted source commitments."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from scripts.capture_prospective_sec_index import (
    CohortCaptureError,
    build_commitment,
    load_policy,
    save_capture,
    verify_capture,
)

INDEX = (
    b"CIK|Company Name|Form Type|Date Filed|File Name\n"
    b"100|Synthetic|10-K|2026-09-01|edgar/data/100/0000000100-26-000001.txt\n"
)
CAPTURED = datetime(2026, 10, 10, tzinfo=UTC)


def _fixture() -> tuple[dict[str, Any], dict[str, Any]]:
    policy = load_policy()
    policy["minimum_eligible_rows"] = 1
    return policy, build_commitment(INDEX, policy, captured_at=CAPTURED)


@pytest.mark.parametrize(
    "field",
    [
        "schema_version",
        "source_year",
        "source_quarter",
        "index_byte_count",
        "filer_roster_byte_count",
        "eligible_filing_rows",
        "eligible_filer_ciks",
    ],
)
@pytest.mark.parametrize("boundary", ["save", "verify"])
def test_integer_to_float_substitution_rejected(tmp_path: Path, field: str, boundary: str) -> None:
    policy, commitment = _fixture()
    changed = {**commitment, field: float(commitment[field])}
    # Ordinary Python equality ignores this JSON identity change.
    assert changed == commitment
    destination = tmp_path / "capture"
    if boundary == "save":
        with pytest.raises(CohortCaptureError):
            save_capture(INDEX, changed, destination, policy, root=tmp_path)
        assert not destination.exists()
    else:
        save_capture(INDEX, commitment, destination, policy, root=tmp_path)
        (destination / "commitment.json").write_text(json.dumps(changed))
        with pytest.raises(CohortCaptureError):
            verify_capture(destination, policy, root=tmp_path)


@pytest.mark.parametrize("field", ["eligible_filing_rows", "eligible_filer_ciks"])
@pytest.mark.parametrize("boundary", ["save", "verify"])
def test_boolean_count_substitution_rejected(tmp_path: Path, field: str, boundary: str) -> None:
    policy, commitment = _fixture()
    changed = {**commitment, field: True}
    assert changed == commitment
    destination = tmp_path / "capture"
    if boundary == "save":
        with pytest.raises(CohortCaptureError):
            save_capture(INDEX, changed, destination, policy, root=tmp_path)
        assert not destination.exists()
    else:
        save_capture(INDEX, commitment, destination, policy, root=tmp_path)
        (destination / "commitment.json").write_text(json.dumps(changed))
        with pytest.raises(CohortCaptureError):
            verify_capture(destination, policy, root=tmp_path)


@pytest.mark.parametrize("nested", [False, True])
def test_duplicate_policy_keys_rejected(tmp_path: Path, nested: bool) -> None:
    policy, _ = _fixture()
    encoded = json.dumps(policy)
    if nested:
        encoded = encoded.replace('"issue_number":', '"issue_number": 1, "issue_number":')
    else:
        encoded = '{"study_id":"conflicting-study",' + encoded[1:]
    path = tmp_path / "policy.json"
    path.write_text(encoded)
    with pytest.raises(CohortCaptureError, match="duplicate"):
        load_policy(path)


def test_duplicate_commitment_key_rejected(tmp_path: Path) -> None:
    policy, commitment = _fixture()
    destination = tmp_path / "capture"
    save_capture(INDEX, commitment, destination, policy, root=tmp_path)
    encoded = '{"index_sha256":"conflicting-hash",' + json.dumps(commitment)[1:]
    (destination / "commitment.json").write_text(encoded)
    with pytest.raises(CohortCaptureError, match="duplicate"):
        verify_capture(destination, policy, root=tmp_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", True),
        ("schema_version", 1.0),
        ("quarter", 3.0),
        ("minimum_eligible_rows", True),
    ],
)
def test_policy_rejects_numeric_type_substitution(
    tmp_path: Path, field: str, value: object
) -> None:
    policy, _ = _fixture()
    policy[field] = value
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy))
    with pytest.raises(CohortCaptureError):
        load_policy(path)


def test_valid_commitment_formatting_does_not_change_identity(tmp_path: Path) -> None:
    policy, commitment = _fixture()
    destination = tmp_path / "capture"
    save_capture(INDEX, commitment, destination, policy, root=tmp_path)
    compact = json.dumps(dict(reversed(list(commitment.items()))), separators=(",", ":"))
    (destination / "commitment.json").write_text(compact)
    assert verify_capture(destination, policy, root=tmp_path) == commitment
