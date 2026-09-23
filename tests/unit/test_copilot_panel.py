from __future__ import annotations

import json
from pathlib import Path

import pytest

from edgar_moe.api.repository import SnapshotRepository
from edgar_moe.copilot.agent import ProviderResponse, ProviderToolCall, ResearchCopilot
from edgar_moe.copilot.panel import (
    PanelInputError,
    normalize_panel_profiles,
    run_panel,
    verify_panel_report,
)
from edgar_moe.copilot.tools import ReadOnlyToolset


class FakeProvider:
    model = "fake-model"
    provider_name = "test-provider"

    def complete(self, messages: object, tools: object) -> ProviderResponse:
        del messages, tools
        if not hasattr(self, "called"):
            self.called = True
            return ProviderResponse(
                content="",
                tool_calls=(ProviderToolCall("call-1", "get_study_summary", "{}"),),
                model=self.model,
            )
        return ProviderResponse(
            content="The cited study summary is research-only evidence.",
            tool_calls=(),
            model=self.model,
        )


class FailingRunner:
    def ask(self, question: str):
        del question
        raise RuntimeError("provider payload must not enter the aggregate")


def _runner_factory(snapshot: Path, failing: set[str] | None = None):
    failing = failing or set()
    toolset = ReadOnlyToolset(SnapshotRepository(snapshot))

    def factory(profile: str):
        if profile in failing:
            return FailingRunner()
        return ResearchCopilot(
            provider=FakeProvider(),
            toolset=toolset,
            profile=profile,
        )

    return factory


def test_panel_runs_specialists_and_retains_only_safe_aggregate(tmp_path: Path) -> None:
    output_dir = tmp_path / "panel"
    aggregate = run_panel(
        "What did the frozen study measure?",
        ["quant", "architect"],
        _runner_factory(Path("data/demo/snapshot.json")),
        output_dir,
    )

    assert aggregate["status"] == "complete"
    assert aggregate["boundary_status"] == "consistent"
    assert aggregate["successful_profiles"] == ["quant", "architect"]
    assert aggregate["failed_profiles"] == []
    assert "What did the frozen study measure?" not in json.dumps(aggregate)
    verify_panel_report(aggregate)
    for profile in ("quant", "architect"):
        child = json.loads((output_dir / f"{profile}.json").read_text(encoding="utf-8"))
        assert child["evidence_status"] == "grounded"
        assert child["agent_identity"]["profile_id"] == profile


def test_panel_retains_coarse_failure_and_marks_partial(tmp_path: Path) -> None:
    aggregate = run_panel(
        "Which controls remain pending?",
        ["quant", "operations"],
        _runner_factory(Path("data/demo/snapshot.json"), {"operations"}),
        tmp_path / "panel",
    )

    assert aggregate["status"] == "partial"
    assert aggregate["successful_profiles"] == ["quant"]
    assert aggregate["failed_profiles"] == [
        {"profile_id": "operations", "error_type": "RuntimeError"}
    ]
    assert "provider payload" not in json.dumps(aggregate)
    verify_panel_report(aggregate)


def test_panel_with_no_successful_profiles_is_verifiable(tmp_path: Path) -> None:
    aggregate = run_panel(
        "What is the status?",
        ["research"],
        _runner_factory(Path("data/demo/snapshot.json"), {"research"}),
        tmp_path / "panel",
    )

    assert aggregate["status"] == "failed"
    assert aggregate["successful_profiles"] == []
    verify_panel_report(aggregate)


def test_panel_verifier_rejects_hash_drift(tmp_path: Path) -> None:
    aggregate = run_panel(
        "What was measured?",
        ["quant"],
        _runner_factory(Path("data/demo/snapshot.json")),
        tmp_path / "panel",
    )
    aggregate["panel_sha256"] = "0" * 64
    with pytest.raises(PanelInputError, match="hash"):
        verify_panel_report(aggregate)


def test_panel_verifier_rejects_identity_digest_drift(tmp_path: Path) -> None:
    aggregate = run_panel(
        "What was measured?",
        ["quant"],
        _runner_factory(Path("data/demo/snapshot.json")),
        tmp_path / "panel",
    )
    aggregate["frozen_identity_sha256"] = "0" * 64
    with pytest.raises(PanelInputError, match="frozen identity digest"):
        verify_panel_report(aggregate)


def test_panel_profiles_are_bounded_and_duplicate_free() -> None:
    assert normalize_panel_profiles(["quant", "architect"]) == ("quant", "architect")
    with pytest.raises(PanelInputError, match="duplicates"):
        normalize_panel_profiles(["quant", "quant"])
    with pytest.raises(PanelInputError, match="one of"):
        normalize_panel_profiles(["trader"])
