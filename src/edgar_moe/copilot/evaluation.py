"""Offline evaluation contracts for research-copilot answer reports.

The evaluator deliberately scores the observable safety and evidence contract
instead of pretending that a lexical metric can prove the truth of an LLM
answer.  It never contacts a provider and it never stores the answer text in
the evaluation report; operators can therefore run it against private reports
without putting prompts or responses into the repository.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from .contracts import content_hash

_CASE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_CASES = 64
_MAX_LIST_ITEMS = 8
EvidenceStatus = Literal["grounded", "uncited"]


class EvaluationInputError(ValueError):
    """Raised when a corpus or answer report violates the evaluation contract."""


@dataclass(frozen=True)
class EvaluationCase:
    """One reviewed question and its observable evidence expectations."""

    case_id: str
    question: str
    expected_evidence_status: EvidenceStatus
    required_tools: tuple[str, ...] = ()
    forbidden_tools: tuple[str, ...] = ()
    required_sources: tuple[str, ...] = ()
    min_citations: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.case_id,
            "question": self.question,
            "expected_evidence_status": self.expected_evidence_status,
            "required_tools": list(self.required_tools),
            "forbidden_tools": list(self.forbidden_tools),
            "required_sources": list(self.required_sources),
            "min_citations": self.min_citations,
        }


@dataclass(frozen=True)
class EvaluationCorpus:
    """Content-addressed collection of reviewed evaluation cases."""

    corpus_id: str
    cases: tuple[EvaluationCase, ...]
    sha256: str

    def case_for_report(self, report: Mapping[str, object]) -> EvaluationCase:
        explicit_case_id = report.get("evaluation_case_id")
        if explicit_case_id is not None:
            if not isinstance(explicit_case_id, str):
                raise EvaluationInputError("evaluation_case_id must be a string")
            for case in self.cases:
                if case.case_id == explicit_case_id:
                    return case
            raise EvaluationInputError(f"unknown evaluation_case_id: {explicit_case_id}")

        question = report.get("question")
        if not isinstance(question, str) or not question.strip():
            raise EvaluationInputError("answer report must include a non-empty question")
        normalized_question = _normalize_question(question)
        matches = [
            case for case in self.cases if _normalize_question(case.question) == normalized_question
        ]
        if len(matches) != 1:
            raise EvaluationInputError(
                "answer question must match exactly one reviewed evaluation case"
            )
        return matches[0]


@dataclass(frozen=True)
class CaseEvaluation:
    """Non-sensitive score for one answer report."""

    case_id: str
    passed: bool
    failures: tuple[str, ...]
    answer_sha256: str
    observed_evidence_status: str | None
    observed_tools: tuple[str, ...]
    observed_sources: tuple[str, ...]
    citation_count: int

    def as_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "failures": list(self.failures),
            "answer_sha256": self.answer_sha256,
            "observed_evidence_status": self.observed_evidence_status,
            "observed_tools": list(self.observed_tools),
            "observed_sources": list(self.observed_sources),
            "citation_count": self.citation_count,
        }


@dataclass(frozen=True)
class EvaluationSuite:
    """Aggregate score for one or more private answer reports."""

    corpus_id: str
    corpus_sha256: str
    cases: tuple[CaseEvaluation, ...]
    missing_case_ids: tuple[str, ...]

    @property
    def passed_count(self) -> int:
        return sum(case.passed for case in self.cases)

    @property
    def pass_rate(self) -> float:
        return self.passed_count / len(self.cases) if self.cases else 0.0

    @property
    def complete(self) -> bool:
        return not self.missing_case_ids

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "corpus_id": self.corpus_id,
            "corpus_sha256": self.corpus_sha256,
            "case_count": len(self.cases),
            "passed_count": self.passed_count,
            "pass_rate": round(self.pass_rate, 6),
            "complete": self.complete,
            "missing_case_ids": list(self.missing_case_ids),
            "cases": [case.as_dict() for case in self.cases],
            "disclaimer": (
                "Structural evidence-contract evaluation only; a passing score does not establish "
                "the truth of an answer, statistical validity, or investment suitability."
            ),
        }


def load_evaluation_corpus(path: Path) -> EvaluationCorpus:
    """Load and validate a reviewed, content-addressed evaluation corpus."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvaluationInputError(f"could not read evaluation corpus: {path}") from error
    if not isinstance(raw, Mapping):
        raise EvaluationInputError("evaluation corpus must be a JSON object")
    schema_version = raw.get("schema_version")
    if schema_version != 1:
        raise EvaluationInputError("evaluation corpus schema_version must be 1")
    corpus_id = raw.get("corpus_id")
    if not isinstance(corpus_id, str) or not _CASE_ID.fullmatch(corpus_id):
        raise EvaluationInputError("corpus_id must be a lowercase identifier")
    raw_cases = raw.get("cases")
    if not isinstance(raw_cases, list) or not 1 <= len(raw_cases) <= _MAX_CASES:
        raise EvaluationInputError(f"cases must contain between 1 and {_MAX_CASES} entries")

    cases: list[EvaluationCase] = []
    seen_ids: set[str] = set()
    for raw_case in raw_cases:
        if not isinstance(raw_case, Mapping):
            raise EvaluationInputError("each evaluation case must be an object")
        case = _parse_case(raw_case)
        if case.case_id in seen_ids:
            raise EvaluationInputError(f"duplicate evaluation case id: {case.case_id}")
        seen_ids.add(case.case_id)
        cases.append(case)
    canonical = {"schema_version": 1, "corpus_id": corpus_id, "cases": [case.as_dict() for case in cases]}
    return EvaluationCorpus(corpus_id=corpus_id, cases=tuple(cases), sha256=content_hash(canonical))


def evaluate_reports(
    reports: tuple[Mapping[str, object], ...], corpus: EvaluationCorpus
) -> EvaluationSuite:
    """Evaluate private answer envelopes without retaining their answer text."""
    evaluations: list[CaseEvaluation] = []
    seen_case_ids: set[str] = set()
    for report in reports:
        case = corpus.case_for_report(report)
        if case.case_id in seen_case_ids:
            raise EvaluationInputError(f"duplicate answer report for case: {case.case_id}")
        seen_case_ids.add(case.case_id)
        evaluations.append(evaluate_report(report, case))
    missing = tuple(case.case_id for case in corpus.cases if case.case_id not in seen_case_ids)
    return EvaluationSuite(
        corpus_id=corpus.corpus_id,
        corpus_sha256=corpus.sha256,
        cases=tuple(evaluations),
        missing_case_ids=missing,
    )


def evaluate_report(report: Mapping[str, object], case: EvaluationCase) -> CaseEvaluation:
    """Evaluate one answer envelope against a reviewed case."""
    answer = report.get("answer")
    failures: list[str] = []
    if not isinstance(answer, str) or not answer.strip():
        failures.append("answer is missing or empty")
        answer_text = ""
    else:
        answer_text = answer
    if report.get("schema_version") != 1:
        failures.append("schema_version must be 1")
    if report.get("research_only") is not True:
        failures.append("research_only must be true")

    raw_status = report.get("evidence_status")
    observed_status = raw_status if isinstance(raw_status, str) else None
    if observed_status != case.expected_evidence_status:
        failures.append(
            f"evidence_status expected {case.expected_evidence_status!r}, got {observed_status!r}"
        )

    citations = _citation_records(report.get("citations"), failures)
    traces = _trace_records(report.get("tool_trace"), failures)
    observed_tools = _unique_strings(trace.get("name") for trace in traces)
    observed_sources = _unique_strings(citation.get("source") for citation in citations)
    citation_count = len(citations)
    missing_tools = sorted(set(case.required_tools) - set(observed_tools))
    forbidden_tools = sorted(set(case.forbidden_tools) & set(observed_tools))
    missing_sources = sorted(set(case.required_sources) - set(observed_sources))
    if missing_tools:
        failures.append(f"missing required tools: {', '.join(missing_tools)}")
    if forbidden_tools:
        failures.append(f"forbidden tools used: {', '.join(forbidden_tools)}")
    if missing_sources:
        failures.append(f"missing required citation sources: {', '.join(missing_sources)}")
    if citation_count < case.min_citations:
        failures.append(f"expected at least {case.min_citations} citations, got {citation_count}")
    if case.expected_evidence_status == "grounded" and citation_count == 0:
        failures.append("grounded answer must include at least one citation")
    if case.expected_evidence_status == "uncited" and citation_count:
        failures.append("uncited answer must not include citations")

    return CaseEvaluation(
        case_id=case.case_id,
        passed=not failures,
        failures=tuple(failures),
        answer_sha256=hashlib.sha256(answer_text.encode("utf-8")).hexdigest(),
        observed_evidence_status=observed_status,
        observed_tools=observed_tools,
        observed_sources=observed_sources,
        citation_count=citation_count,
    )


def _parse_case(raw: Mapping[str, object]) -> EvaluationCase:
    case_id = raw.get("id")
    question = raw.get("question")
    status = raw.get("expected_evidence_status")
    if not isinstance(case_id, str) or not _CASE_ID.fullmatch(case_id):
        raise EvaluationInputError("case id must be a lowercase identifier")
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 2_000:
        raise EvaluationInputError(f"case {case_id} question must contain 1-2000 characters")
    if status not in {"grounded", "uncited"}:
        raise EvaluationInputError(
            f"case {case_id} expected_evidence_status must be grounded or uncited"
        )
    required_tools = _string_list(raw.get("required_tools", []), f"case {case_id} required_tools")
    forbidden_tools = _string_list(raw.get("forbidden_tools", []), f"case {case_id} forbidden_tools")
    required_sources = _string_list(
        raw.get("required_sources", []), f"case {case_id} required_sources"
    )
    min_citations = raw.get("min_citations", 0)
    if isinstance(min_citations, bool) or not isinstance(min_citations, int) or not 0 <= min_citations <= 8:
        raise EvaluationInputError(f"case {case_id} min_citations must be an integer from 0 to 8")
    return EvaluationCase(
        case_id=case_id,
        question=question.strip(),
        expected_evidence_status=cast(EvidenceStatus, status),
        required_tools=required_tools,
        forbidden_tools=forbidden_tools,
        required_sources=required_sources,
        min_citations=min_citations,
    )


def _string_list(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > _MAX_LIST_ITEMS:
        raise EvaluationInputError(f"{label} must be a list of at most {_MAX_LIST_ITEMS} strings")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise EvaluationInputError(f"{label} must contain non-empty strings")
    items = tuple(item.strip() for item in value)
    if len(set(items)) != len(items):
        raise EvaluationInputError(f"{label} must not contain duplicates")
    return items


def _citation_records(value: object, failures: list[str]) -> list[dict[str, object]]:
    if not isinstance(value, list):
        failures.append("citations must be a list")
        return []
    records: list[dict[str, object]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            failures.append(f"citation {index} must be an object")
            continue
        source = item.get("source")
        digest = item.get("evidence_sha256")
        if not isinstance(source, str) or not source.strip():
            failures.append(f"citation {index} source must be non-empty")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            failures.append(f"citation {index} evidence_sha256 must be a lowercase SHA-256 digest")
        records.append({"source": source.strip() if isinstance(source, str) else ""})
    return records


def _trace_records(value: object, failures: list[str]) -> list[dict[str, object]]:
    if not isinstance(value, list):
        failures.append("tool_trace must be a list")
        return []
    records: list[dict[str, object]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            failures.append(f"tool trace {index} must be an object")
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            failures.append(f"tool trace {index} name must be non-empty")
        records.append({"name": name.strip() if isinstance(name, str) else ""})
    return records


def _unique_strings(values: Any) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if isinstance(value, str) and value and value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)


def _normalize_question(value: str) -> str:
    return " ".join(value.split()).casefold()
