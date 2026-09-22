from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from edgar_moe.api.repository import (
    SnapshotIntegrityError,
    SnapshotNotFoundError,
    SnapshotRepository,
)


def _write_locked_repository(root: Path) -> tuple[Path, Path]:
    snapshot_path = root / "data" / "demo" / "snapshot.json"
    snapshot_path.parent.mkdir(parents=True)
    snapshot = {
        "metadata": {
            "data_mode": "authenticated_locked_test",
            "as_of": "2026-07-31",
            "selection_hash": "a" * 64,
            "locked_test_hash": "b" * 64,
            "research_only": True,
        },
        "summary": {},
        "predictive_metrics": {},
        "portfolio_scenarios": [],
        "experiments": [],
        "equity_curves": {},
        "events": [],
        "latest_signals": [],
        "methodology": {},
        "freshness": {},
    }
    raw = (json.dumps(snapshot, sort_keys=True) + "\n").encode("utf-8")
    snapshot_path.write_bytes(raw)

    lock_path = root / "config" / "public_snapshot.lock.json"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "path": "data/demo/snapshot.json",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "data_mode": "authenticated_locked_test",
                "as_of": "2026-07-31",
                "selection_hash": "a" * 64,
                "locked_test_hash": "b" * 64,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return snapshot_path, lock_path


def _lock_contents(lock_path: Path) -> dict[str, object]:
    return dict(json.loads(lock_path.read_text(encoding="utf-8")))


def _rewrite_lock(lock_path: Path, **changes: object) -> None:
    """Rewrite the lock with one field changed, leaving the rest verifiable."""

    lock = _lock_contents(lock_path)
    for field, value in changes.items():
        if value is None:
            lock.pop(field, None)
        else:
            lock[field] = value
    lock_path.write_text(json.dumps(lock, sort_keys=True) + "\n", encoding="utf-8")


def test_locked_repository_verifies_snapshot_and_identity(tmp_path: Path) -> None:
    snapshot_path, lock_path = _write_locked_repository(tmp_path)

    repository = SnapshotRepository(snapshot_path, lock_path=lock_path)

    assert repository.load()["metadata"]["as_of"] == "2026-07-31"
    assert (
        repository.frozen_identity()["sha256"]
        == hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
    )


def test_locked_repository_rejects_snapshot_tampering_after_cache_warmup(tmp_path: Path) -> None:
    snapshot_path, lock_path = _write_locked_repository(tmp_path)
    repository = SnapshotRepository(snapshot_path, lock_path=lock_path)
    repository.load()

    snapshot_path.write_bytes(snapshot_path.read_bytes() + b" ")

    with pytest.raises(SnapshotIntegrityError):
        repository.load()


def test_locked_repository_rejects_lock_tampering_after_cache_warmup(tmp_path: Path) -> None:
    snapshot_path, lock_path = _write_locked_repository(tmp_path)
    repository = SnapshotRepository(snapshot_path, lock_path=lock_path)
    repository.load()

    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["as_of"] = "2026-08-01"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")

    with pytest.raises(SnapshotIntegrityError):
        repository.load()


def test_unlocked_fixture_repository_remains_supported(tmp_path: Path) -> None:
    snapshot_path, _ = _write_locked_repository(tmp_path)

    assert SnapshotRepository(snapshot_path).load()["events"] == []


# Each case below is a way production could end up serving something other than
# the reviewed snapshot. R5 in the solution architecture rests on them failing.


def test_an_unreadable_lock_stops_the_snapshot_being_served(tmp_path: Path) -> None:
    snapshot_path, lock_path = _write_locked_repository(tmp_path)
    lock_path.write_text("{not json", encoding="utf-8")

    with pytest.raises(SnapshotIntegrityError, match="not readable"):
        SnapshotRepository(snapshot_path, lock_path=lock_path).load()


def test_a_missing_lock_is_reported_as_unavailable(tmp_path: Path) -> None:
    snapshot_path, lock_path = _write_locked_repository(tmp_path)
    lock_path.unlink()

    with pytest.raises(SnapshotIntegrityError, match="unavailable"):
        SnapshotRepository(snapshot_path, lock_path=lock_path).load()


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"sha256": None}, "schema is invalid"),
        ({"unexpected": "field"}, "schema is invalid"),
        ({"schema_version": 2}, "identity is invalid"),
        ({"path": "data/demo/other.json"}, "identity is invalid"),
        ({"sha256": "c" * 64}, "bytes do not match"),
        ({"sha256": "A" * 64}, "bytes do not match"),
        ({"sha256": "z" * 64}, "bytes do not match"),
        ({"sha256": "abc"}, "bytes do not match"),
        ({"data_mode": "synthetic_demo"}, "does not match its lock"),
        ({"as_of": "2026-06-30"}, "does not match its lock"),
        ({"selection_hash": "d" * 64}, "does not match its lock"),
        ({"locked_test_hash": "e" * 64}, "does not match its lock"),
    ],
)
def test_a_lock_that_does_not_describe_this_snapshot_is_refused(
    tmp_path: Path,
    changes: dict[str, object],
    message: str,
) -> None:
    snapshot_path, lock_path = _write_locked_repository(tmp_path)
    _rewrite_lock(lock_path, **changes)

    with pytest.raises(SnapshotIntegrityError, match=message):
        SnapshotRepository(snapshot_path, lock_path=lock_path).load()


def test_a_snapshot_outside_the_locked_path_is_refused(tmp_path: Path) -> None:
    # The lock names data/demo/snapshot.json relative to its own parent's parent,
    # so a copy served from elsewhere must not pass, even byte-identical.
    snapshot_path, lock_path = _write_locked_repository(tmp_path)
    elsewhere = tmp_path / "data" / "demo" / "copy.json"
    elsewhere.write_bytes(snapshot_path.read_bytes())

    with pytest.raises(SnapshotIntegrityError, match="path does not match"):
        SnapshotRepository(elsewhere, lock_path=lock_path).load()


def test_a_snapshot_that_is_not_research_only_is_refused(tmp_path: Path) -> None:
    snapshot_path, lock_path = _write_locked_repository(tmp_path)
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["metadata"]["research_only"] = False
    raw = (json.dumps(snapshot, sort_keys=True) + "\n").encode("utf-8")
    snapshot_path.write_bytes(raw)
    _rewrite_lock(lock_path, sha256=hashlib.sha256(raw).hexdigest())

    with pytest.raises(SnapshotIntegrityError, match="not research-only"):
        SnapshotRepository(snapshot_path, lock_path=lock_path).load()


@pytest.mark.parametrize(
    ("metadata_change", "message"),
    [
        ({"research_only": False}, "must be research-only"),
        ({"selection_hash": 7}, "identity metadata is incomplete"),
        ({"as_of": None}, "identity metadata is incomplete"),
    ],
)
def test_the_published_identity_refuses_incomplete_metadata(
    tmp_path: Path,
    metadata_change: dict[str, object],
    message: str,
) -> None:
    # An unlocked deployment still must not publish an identity it cannot stand
    # behind, because the governance page and the smoke check both read it.
    snapshot_path, _ = _write_locked_repository(tmp_path)
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["metadata"].update(metadata_change)
    snapshot_path.write_text(json.dumps(snapshot, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        SnapshotRepository(snapshot_path).frozen_identity()


def test_the_published_identity_refuses_a_snapshot_without_metadata(tmp_path: Path) -> None:
    snapshot_path, _ = _write_locked_repository(tmp_path)
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["metadata"] = ["not", "an", "object"]
    snapshot_path.write_text(json.dumps(snapshot, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="metadata must be an object"):
        SnapshotRepository(snapshot_path).frozen_identity()


def _repository_with_events(root: Path) -> SnapshotRepository:
    """An unlocked repository holding three filings that differ in every filter."""

    snapshot_path, _ = _write_locked_repository(root)
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["events"] = [
        {
            "accession_number": "0000000001-24-000001",
            "ticker": "AAA",
            "company_name": "Alpha Industries",
            "form": "10-K",
            "direction": "long",
            "entry_date": "2026-01-15",
        },
        {
            "accession_number": "0000000002-24-000002",
            "ticker": "BBB",
            "company_name": "Beta Corporation",
            "form": "10-Q",
            "direction": "short",
            "entry_date": "2026-03-20",
        },
        {
            "accession_number": "0000000003-24-000003",
            "ticker": "CCC",
            "company_name": "Gamma Holdings",
            "form": "10-K",
            "direction": "neutral",
            "entry_date": "2026-06-10",
        },
    ]
    snapshot["latest_signals"] = [
        {
            "accession_number": "0000000009-24-000009",
            "ticker": "ZZZ",
            "company_name": "Omega Limited",
            "form": "10-K",
            "direction": "long",
            "entry_date": "2026-07-01",
        }
    ]
    snapshot_path.write_text(json.dumps(snapshot, sort_keys=True) + "\n", encoding="utf-8")
    return SnapshotRepository(snapshot_path)


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({"ticker": "bbb"}, ["BBB"]),  # matching is case-insensitive
        ({"form": "10-K"}, ["AAA", "CCC"]),
        ({"direction": "neutral"}, ["CCC"]),
        ({"query": "  BETA "}, ["BBB"]),  # trimmed, and matches the company name
        ({"query": "gamma"}, ["CCC"]),
        ({"from_date": date(2026, 3, 1)}, ["BBB", "CCC"]),
        ({"to_date": date(2026, 3, 1)}, ["AAA"]),
        ({"from_date": date(2026, 2, 1), "to_date": date(2026, 4, 1)}, ["BBB"]),
        ({"form": "10-K", "direction": "long"}, ["AAA"]),
        ({"ticker": "AAA", "form": "10-Q"}, []),
    ],
)
def test_each_filing_filter_selects_what_it_names(
    tmp_path: Path,
    filters: dict[str, object],
    expected: list[str],
) -> None:
    repository = _repository_with_events(tmp_path)

    page = repository.event_page(**filters)  # type: ignore[arg-type]

    assert [row["ticker"] for row in page["items"]] == expected
    assert page["total"] == len(expected)


def test_paging_walks_the_filtered_set_and_then_stops(tmp_path: Path) -> None:
    repository = _repository_with_events(tmp_path)

    first = repository.event_page(limit=2)
    assert [row["ticker"] for row in first["items"]] == ["AAA", "BBB"]
    assert first["next_cursor"] == "2"

    second = repository.event_page(limit=2, cursor=first["next_cursor"])
    assert [row["ticker"] for row in second["items"]] == ["CCC"]
    # Nothing is left, so the page does not invite another request.
    assert second["next_cursor"] is None
    assert second["total"] == 3


@pytest.mark.parametrize("cursor", ["-1", "abc", "1.5", ""])
def test_a_cursor_that_is_not_a_position_is_rejected(tmp_path: Path, cursor: str) -> None:
    repository = _repository_with_events(tmp_path)

    with pytest.raises(ValueError, match="non-negative integer"):
        repository.event_page(cursor=cursor)


def test_a_filing_is_found_among_the_latest_signals_too(tmp_path: Path) -> None:
    # The final study cohort is served from its own list, but a link to one of
    # those filings must still resolve.
    repository = _repository_with_events(tmp_path)

    assert repository.event("0000000003-24-000003")["ticker"] == "CCC"
    assert repository.event("0000000009-24-000009")["ticker"] == "ZZZ"
    assert repository.event("0000000000-00-000000") is None
    assert [row["ticker"] for row in repository.latest_signals()] == ["ZZZ"]


def test_an_unsupported_cost_scenario_is_refused(tmp_path: Path) -> None:
    repository = _repository_with_events(tmp_path)

    with pytest.raises(KeyError, match="Unsupported cost scenario: 15"):
        repository.equity_curve(15)


def test_a_missing_snapshot_is_reported_as_missing_not_as_tampering(tmp_path: Path) -> None:
    # A deployment that shipped without its snapshot is a different problem from
    # one serving the wrong bytes, and the API maps the two to different answers.
    with pytest.raises(SnapshotNotFoundError):
        SnapshotRepository(tmp_path / "data" / "demo" / "snapshot.json").load()


def test_a_snapshot_missing_a_whole_section_is_refused(tmp_path: Path) -> None:
    snapshot_path, _ = _write_locked_repository(tmp_path)
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    del snapshot["equity_curves"]
    del snapshot["methodology"]
    snapshot_path.write_text(json.dumps(snapshot, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"missing keys: \['equity_curves', 'methodology'\]"):
        SnapshotRepository(snapshot_path).load()
