from __future__ import annotations

import hashlib
import stat
from copy import deepcopy
from pathlib import Path

import orjson
import pytest
from typer.testing import CliRunner

from edgar_moe.cli import app
from edgar_moe.copilot.benchmark import run_benchmark, write_benchmark_report
from edgar_moe.copilot.blind_review import (
    MaskedReviewError,
    prepare_masked_review,
    score_masked_review,
    write_private_json,
)
from edgar_moe.copilot.contracts import Citation, CopilotAnswer, ToolTrace, content_hash
from edgar_moe.copilot.evaluation import EvaluationCase, EvaluationCorpus, load_evaluation_corpus


def _corpus() -> EvaluationCorpus:
    return EvaluationCorpus(
        corpus_id="test-corpus",
        cases=tuple(
            EvaluationCase(
                case_id=case_id,
                question=f"Question for {case_id}?",
                expected_evidence_status="grounded",
                required_tools=("get_study_summary",),
                required_sources=("snapshot:data/demo/snapshot.json",),
                min_citations=1,
            )
            for case_id in ("case-one", "case-two")
        ),
        sha256="c" * 64,
    )


class FakeRunner:
    def __init__(self, provider: str, model: str) -> None:
        self.provider = provider
        self.model = model

    def ask(self, question: str) -> CopilotAnswer:
        return CopilotAnswer(
            question=question,
            answer=f"Evidence summary for {question}",
            model=self.model,
            provider=self.provider,
            created_at="2026-09-26T00:00:00Z",
            frozen_identity={
                "path": "data/demo/snapshot.json",
                "sha256": "a" * 64,
                "data_mode": "synthetic_fixture",
                "as_of": "2026-01-01",
                "selection_hash": "b" * 64,
                "locked_test_hash": "d" * 64,
                "research_only": True,
            },
            citations=(Citation("snapshot:data/demo/snapshot.json", "Invented", "e" * 64),),
            trace=(ToolTrace(1, "get_study_summary", "f" * 64, "e" * 64, 1),),
            evidence_status="grounded",
        )


def _benchmark_pair(tmp_path: Path) -> tuple[Path, Path]:
    corpus = _corpus()
    baseline_dir = tmp_path / "baseline"
    copilot_dir = tmp_path / "copilot"
    context = {
        "registry_configured": False,
        "diagnostic_configured": False,
        "diagnostic_history_configured": False,
        "tool_contract_sha256": "f" * 64,
    }
    for directory, provider, model, contacted in (
        (baseline_dir, "none", "deterministic-evidence-navigation-v1", False),
        (copilot_dir, "fake-provider", "fake-model", True),
    ):
        run = run_benchmark(corpus, FakeRunner(provider, model), directory)
        write_benchmark_report(
            directory / "evaluation.json",
            run.as_dict(
                provider=provider,
                model=model,
                provider_contacted=contacted,
                comparison_context=context,
            ),
        )
    return baseline_dir, copilot_dir


def _review(packet: dict) -> dict:
    return {
        "schema_version": 1,
        "packet_sha256": content_hash(packet),
        "corpus_sha256": packet["corpus_sha256"],
        "reviewer": "reviewer-01",
        "reviewed_at": "2026-09-26T01:00:00Z",
        "cases": [
            {
                "case_id": case["case_id"],
                "A": {
                    "task_completion": "pass",
                    "factuality": "pass",
                    "citations": "pass",
                    "safety": "pass",
                    "usefulness": 3,
                },
                "B": {
                    "task_completion": "pass",
                    "factuality": "pass",
                    "citations": "pass",
                    "safety": "pass",
                    "usefulness": 3,
                },
            }
            for case in packet["cases"]
        ],
    }


def test_holdout_corpus_is_separate_and_content_addressed() -> None:
    corpus = load_evaluation_corpus(Path("config/copilot_holdout_cases.json"))
    assert corpus.corpus_id == "copilot-heldout-v1"
    assert len(corpus.cases) == 8
    assert corpus.sha256 == "a3c988f5a7c14d8d770f640c7c57608c50b6cb0c963e162a0ce159ad902f1678"


def test_masked_packet_omits_explicit_arm_identity_and_scores_complete_review(
    tmp_path: Path,
) -> None:
    baseline_dir, copilot_dir = _benchmark_pair(tmp_path)
    packet, key = prepare_masked_review(
        baseline_dir,
        copilot_dir,
        _corpus(),
        arm_a={"case-one": "baseline", "case-two": "copilot"},
    )
    assert key["packet_sha256"] == content_hash(packet)
    assert packet["cases"][0]["A"]["answer"].startswith("Evidence summary")
    assert "provider" not in packet
    assert "model" not in packet
    review = _review(packet)
    review["cases"][0]["B"]["usefulness"] = 4
    review["cases"][1]["B"]["task_completion"] = "fail"
    review["cases"][1]["A"]["usefulness"] = 5
    review["cases"][1]["B"]["usefulness"] = 2

    report = score_masked_review(packet, key, review)

    assert report["paired_quality"] == {
        "copilot_only": 1,
        "baseline_only": 0,
        "both": 1,
        "neither": 0,
    }
    assert report["usefulness_order"] == {
        "copilot_higher": 2,
        "baseline_higher": 0,
        "tie": 0,
    }
    assert "Evidence summary" not in orjson.dumps(report).decode()


@pytest.mark.parametrize(
    "mutation", ["packet", "missing_case", "rating", "mapping", "timestamp", "extra_review_field"]
)
def test_masked_scoring_rejects_tampered_or_incomplete_review(
    tmp_path: Path, mutation: str
) -> None:
    baseline_dir, copilot_dir = _benchmark_pair(tmp_path)
    packet, key = prepare_masked_review(
        baseline_dir,
        copilot_dir,
        _corpus(),
        arm_a={"case-one": "baseline", "case-two": "copilot"},
    )
    packet, key, review = deepcopy(packet), deepcopy(key), _review(packet)
    if mutation == "packet":
        packet["cases"][0]["question"] = "changed"
    elif mutation == "missing_case":
        review["cases"].pop()
    elif mutation == "rating":
        review["cases"][0]["A"]["usefulness"] = 6
    elif mutation == "mapping":
        key["assignments"][0]["B"] = "baseline"
    elif mutation == "timestamp":
        review["reviewed_at"] = "2026-09-26"
    else:
        review["answer"] = "private text"
    with pytest.raises(MaskedReviewError):
        score_masked_review(packet, key, review)


def test_prepare_rejects_a_tampered_answer_hash(tmp_path: Path) -> None:
    baseline_dir, copilot_dir = _benchmark_pair(tmp_path)
    path = copilot_dir / "case-one.json"
    report = orjson.loads(path.read_bytes())
    report["answer"] = "tampered but structurally valid"
    path.write_bytes(orjson.dumps(report))
    with pytest.raises(MaskedReviewError, match="hash mismatch"):
        prepare_masked_review(baseline_dir, copilot_dir, _corpus())


def test_private_writer_refuses_overwrite_and_sets_owner_only_mode(tmp_path: Path) -> None:
    path = tmp_path / "private.json"
    write_private_json(path, {"schema_version": 1})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(FileExistsError):
        write_private_json(path, {"schema_version": 2})
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_mask_and_score_cli_keep_private_answers_out_of_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline_dir, copilot_dir = _benchmark_pair(tmp_path)
    monkeypatch.setattr(
        "edgar_moe.copilot.evaluation.load_evaluation_corpus", lambda _path: _corpus()
    )
    output_dir = tmp_path / "masked"
    prepared = CliRunner().invoke(
        app,
        [
            "research-copilot-mask-review",
            "--baseline-dir",
            str(baseline_dir),
            "--copilot-dir",
            str(copilot_dir),
            "--output-dir",
            str(output_dir),
        ],
    )
    assert prepared.exit_code == 0, prepared.output
    assert stat.S_IMODE(output_dir.stat().st_mode) == 0o700
    assert "Evidence summary" not in prepared.output
    packet = orjson.loads((output_dir / "packet.json").read_bytes())
    assert stat.S_IMODE((output_dir / "mapping.json").stat().st_mode) == 0o600
    review_path = tmp_path / "review.json"
    write_private_json(review_path, _review(packet))
    score_path = tmp_path / "score.json"
    scored = CliRunner().invoke(
        app,
        [
            "research-copilot-score-masked-review",
            "--packet",
            str(output_dir / "packet.json"),
            "--mapping",
            str(output_dir / "mapping.json"),
            "--review",
            str(review_path),
            "--output",
            str(score_path),
        ],
    )
    assert scored.exit_code == 0, scored.output
    assert "Evidence summary" not in scored.output
    assert orjson.loads(score_path.read_bytes())["case_count"] == 2
    assert stat.S_IMODE(score_path.stat().st_mode) == 0o600
    rerun = CliRunner().invoke(
        app,
        [
            "research-copilot-mask-review",
            "--baseline-dir",
            str(baseline_dir),
            "--copilot-dir",
            str(copilot_dir),
            "--output-dir",
            str(output_dir),
        ],
    )
    assert rerun.exit_code != 0
