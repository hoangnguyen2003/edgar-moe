from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import orjson
import pytest
from typer.testing import CliRunner

from edgar_moe.cli import app
from edgar_moe.copilot.paired import PairedBenchmarkError, compare_benchmarks


def _arm(*, provider_contacted: bool) -> dict:
    return {
        "schema_version": 1,
        "corpus_id": "test-corpus",
        "corpus_sha256": "a" * 64,
        "cases": [
            {"case_id": "first", "passed": True},
            {"case_id": "second", "passed": False},
        ],
        "missing_case_ids": [],
        "benchmark": {
            "provider_contacted": provider_contacted,
            "selected_case_ids": ["first", "second"],
            "snapshot_sha256": "b" * 64,
            "comparison_context": {
                "registry_configured": False,
                "diagnostic_configured": False,
                "diagnostic_history_configured": False,
                "tool_contract_sha256": "c" * 64,
            },
            "case_duration_us": [
                {"case_id": "first", "duration_us": 120},
                {"case_id": "second", "duration_us": 340},
            ],
        },
    }


def test_paired_report_preserves_matched_structural_outcomes_without_answers() -> None:
    baseline = _arm(provider_contacted=False)
    copilot = _arm(provider_contacted=True)
    copilot["cases"][1]["passed"] = True
    copilot["benchmark"]["case_duration_us"][0]["duration_us"] = 250_000
    copilot["usage"] = {
        "request_count": 5,
        "duration_ms": 920,
        "prompt_tokens": 200,
        "completion_tokens": None,
        "total_tokens": None,
    }

    report = compare_benchmarks(baseline, copilot)

    assert report["categories"] == {
        "both_pass": 1,
        "baseline_only": 0,
        "copilot_only": 1,
        "neither_or_missing": 0,
    }
    assert report["baseline_latency_us"] == {"median": 230, "p95_nearest_rank": 340}
    assert report["copilot_usage"]["completion_tokens"] is None
    assert "answer" not in str(report)
    assert "question" not in str(report)
    assert "incremental LLM usefulness" in report["interpretation"]


@pytest.mark.parametrize(
    "mutation",
    [
        "corpus",
        "snapshot",
        "tool_context",
        "selected",
        "provider",
        "raw_answer",
        "nested_answer",
        "unsafe_case_id",
        "duration",
        "missing_duration",
        "invalid_usage",
    ],
)
def test_paired_report_rejects_incompatible_or_unsafe_arms(mutation: str) -> None:
    baseline = _arm(provider_contacted=False)
    copilot = _arm(provider_contacted=True)
    if mutation == "corpus":
        copilot["corpus_sha256"] = "c" * 64
    elif mutation == "snapshot":
        copilot["benchmark"]["snapshot_sha256"] = "c" * 64
    elif mutation == "tool_context":
        copilot["benchmark"]["comparison_context"]["registry_configured"] = True
    elif mutation == "selected":
        copilot["benchmark"]["selected_case_ids"].reverse()
    elif mutation == "provider":
        copilot["benchmark"]["provider_contacted"] = False
    elif mutation == "raw_answer":
        copilot["cases"][0]["answer"] = "private response"
    elif mutation == "nested_answer":
        copilot["benchmark"]["private_payload"] = {"answer": "private response"}
    elif mutation == "unsafe_case_id":
        copilot["cases"][0]["case_id"] = "private response text"
    elif mutation == "duration":
        copilot["benchmark"]["case_duration_us"][0]["duration_us"] = -1
    elif mutation == "invalid_usage":
        copilot["usage"] = {"prompt_tokens": 10}
    else:
        copilot["benchmark"]["case_duration_us"].pop()

    with pytest.raises(PairedBenchmarkError):
        compare_benchmarks(deepcopy(baseline), deepcopy(copilot))


def test_paired_cli_writes_only_safe_derived_comparison(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    copilot = tmp_path / "copilot.json"
    output = tmp_path / "comparison.json"
    baseline.write_bytes(orjson.dumps(_arm(provider_contacted=False)))
    copilot.write_bytes(orjson.dumps(_arm(provider_contacted=True)))

    result = CliRunner().invoke(
        app,
        [
            "research-copilot-compare",
            "--baseline",
            str(baseline),
            "--copilot",
            str(copilot),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    report = orjson.loads(output.read_bytes())
    assert report["case_count"] == 2
    assert "answer" not in report
    assert "question" not in report
