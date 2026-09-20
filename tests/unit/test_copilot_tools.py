from __future__ import annotations

from pathlib import Path

import pytest

from edgar_moe.api.repository import SnapshotRepository
from edgar_moe.copilot.contracts import content_hash
from edgar_moe.copilot.tools import ReadOnlyToolset, ToolInputError

SNAPSHOT = Path("data/demo/snapshot.json")


def test_toolset_exposes_only_bounded_read_operations() -> None:
    toolset = ReadOnlyToolset(SnapshotRepository(SNAPSHOT))

    names = {definition.name for definition in toolset.definitions()}

    assert names == {
        "get_frozen_identity",
        "get_study_summary",
        "get_methodology",
        "get_experiment_results",
        "search_filing_events",
        "get_filing_event",
        "get_governance_status",
    }
    assert not any("write" in name or "update" in name for name in names)


def test_tool_result_is_citation_backed_and_forward_status_is_explicitly_unconfigured() -> None:
    toolset = ReadOnlyToolset(SnapshotRepository(SNAPSHOT))

    summary = toolset.execute("get_study_summary", {})
    governance = toolset.execute("get_governance_status", {})

    assert summary.citations[0].evidence_sha256 == content_hash(summary.payload)
    assert governance.payload["forward_status"] == {
        "configured": False,
        "available": False,
        "message": "Forward registry is not configured in this deployment.",
    }


def test_event_tool_rejects_unbounded_or_malformed_identifiers() -> None:
    toolset = ReadOnlyToolset(SnapshotRepository(SNAPSHOT))

    with pytest.raises(ToolInputError):
        toolset.execute(
            "get_filing_event",
            {"accession_number": "../../etc/passwd"},
        )
    with pytest.raises(ToolInputError):
        toolset.execute(
            "search_filing_events",
            {"ticker": "A" * 17, "limit": 1},
        )
    with pytest.raises(ToolInputError):
        toolset.execute("get_filing_event", {"accession_number": "0000000000-00-00000"})


def test_unknown_tool_is_rejected() -> None:
    toolset = ReadOnlyToolset(SnapshotRepository(SNAPSHOT))

    with pytest.raises(ToolInputError, match="not allowlisted"):
        toolset.execute("execute_trade", {})
