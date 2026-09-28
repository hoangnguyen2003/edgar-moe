"""Synthetic, offline evidence-boundary checks for the prospective SEC capture."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.capture_prospective_sec_index import (
    CohortCaptureError,
    build_commitment,
    inspect_index,
    load_policy,
    save_capture,
    verify_capture,
)


def _policy(tmp_path: Path) -> dict[str, object]:
    path = tmp_path / "policy.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "study_id": "sec-filer-cohort-test",
                "source": "sec-edgar-full-index-master",
                "year": 2026,
                "quarter": 3,
                "eligible_forms": ["10-K", "10-Q"],
                "capture_not_before": "2026-10-09T00:00:00Z",
                "capture_before": "2026-10-16T00:00:00Z",
                "research_cutoff": "2026-10-16T00:00:00Z",
                "minimum_eligible_rows": 2,
            }
        ),
        encoding="utf-8",
    )
    return load_policy(path)


def _index(*extra: str) -> bytes:
    return (
        "\n".join(
            [
                "Description: Master Index of EDGAR Dissemination Feed",
                "CIK|Company Name|Form Type|Date Filed|File Name",
                "-------------------------------------------------------",
                "100|Old Name|10-K|2026-08-01|edgar/data/100/0000000100-26-000001.txt",
                "100|Renamed Later|10-Q|2026-09-01|edgar/data/100/0000000100-26-000002.txt",
                "200|Delisted Later|10-Q|2026-09-20|edgar/data/200/0000000200-26-000001.txt",
                "300|Other Form|8-K|2026-09-20|edgar/data/300/0000000300-26-000001.txt",
                *extra,
            ]
        )
        + "\n"
    ).encode()


def test_capture_binds_private_bytes_policy_and_redacted_counts(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    payload = _index()
    now = datetime(2026, 10, 10, 12, tzinfo=UTC)
    commitment = build_commitment(payload, policy, captured_at=now)
    assert commitment["eligible_filing_rows"] == 3
    assert commitment["eligible_filer_ciks"] == 2
    assert commitment["status"] == "filing_cohort_source_only_not_tradable_security_membership"
    for private_identity in ("Old Name", "Renamed Later", "Delisted Later", "0000000100"):
        assert private_identity not in json.dumps(commitment)

    root = tmp_path / "artifacts"
    destination = root / "capture"
    save_capture(payload, commitment, destination, root=root)
    assert verify_capture(destination, policy, root=root) == commitment
    assert (destination / "master.idx").stat().st_mode & 0o777 == 0o600
    assert (destination / "commitment.json").stat().st_mode & 0o777 == 0o600
    with pytest.raises(CohortCaptureError, match="already exists"):
        save_capture(payload, commitment, destination, root=root)

    (destination / "master.idx").write_bytes(payload.replace(b"Old Name", b"New Name"))
    with pytest.raises(CohortCaptureError, match="differ"):
        verify_capture(destination, policy, root=root)


def test_rejects_late_capture_and_retrospective_policy(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    with pytest.raises(CohortCaptureError, match="outside"):
        build_commitment(_index(), policy, captured_at=datetime(2026, 10, 16, tzinfo=UTC))
    policy_path = tmp_path / "retro.json"
    policy["capture_not_before"] = "2026-09-28T00:00:00Z"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    with pytest.raises(CohortCaptureError, match="follow the source quarter"):
        load_policy(policy_path)


@pytest.mark.parametrize(
    "payload",
    [
        b"not an EDGAR index",
        _index("400|Late|10-K|2026-10-01|edgar/data/400/0000000400-26-000001.txt"),
        _index("400|Wrong CIK|10-K|2026-09-20|edgar/data/401/0000000400-26-000001.txt"),
        _index("400|Bad Path|10-K|2026-09-20|edgar/data/400/not-an-accession.txt"),
        _index("100|Duplicate|10-K|2026-08-01|edgar/data/100/0000000100-26-000001.txt"),
    ],
)
def test_rejects_invalid_or_retrospective_rows(tmp_path: Path, payload: bytes) -> None:
    with pytest.raises(CohortCaptureError):
        inspect_index(payload, _policy(tmp_path))


def test_rejects_changed_policy_or_missing_private_bytes(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    payload = _index()
    commitment = build_commitment(payload, policy, captured_at=datetime(2026, 10, 10, tzinfo=UTC))
    root = tmp_path / "artifacts"
    destination = root / "capture"
    save_capture(payload, commitment, destination, root=root)
    changed = {**policy, "study_id": "different-study"}
    with pytest.raises(CohortCaptureError, match="differ"):
        verify_capture(destination, changed, root=root)
    (destination / "master.idx").unlink()
    with pytest.raises(FileNotFoundError):
        verify_capture(destination, policy, root=root)
