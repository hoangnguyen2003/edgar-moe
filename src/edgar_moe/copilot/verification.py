"""Offline integrity checks for private research-copilot answer envelopes."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from .contracts import COPILOT_DISCLAIMER


class CopilotVerificationError(ValueError):
    """Raised when a saved copilot answer violates its evidence boundary."""


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_READ_ONLY_TOOLS = frozenset(
    {
        "get_frozen_identity",
        "get_study_summary",
        "get_methodology",
        "get_experiment_results",
        "search_filing_events",
        "get_filing_event",
        "get_governance_status",
        "get_forward_diagnostic",
        "get_forward_diagnostic_history",
        # Unknown or write-like provider requests are retained only as this
        # neutral audit marker; the requested name never enters the envelope.
        "rejected_tool_request",
    }
)
_ANSWER_KEYS = frozenset(
    {
        "answer",
        "citations",
        "created_at",
        "disclaimer",
        "evidence_status",
        "frozen_identity",
        "model",
        "provider",
        "question",
        "research_only",
        "schema_version",
        "tool_trace",
    }
)
_OPTIONAL_KEYS = frozenset({"evaluation_case_id"})
_IDENTITY_KEYS = frozenset(
    {"as_of", "data_mode", "locked_test_hash", "path", "research_only", "selection_hash", "sha256"}
)
_CITATION_KEYS = frozenset({"evidence_sha256", "fields", "label", "source"})
_TRACE_KEYS = frozenset(
    {"arguments_sha256", "call_index", "citation_count", "name", "result_sha256"}
)
_MAX_ANSWER_BYTES = 2_000_000
_MAX_CITATIONS = 64
_MAX_TRACE_ITEMS = 8
_MAX_FIELDS = 64


def verify_copilot_answer_report(report: Mapping[str, Any]) -> None:
    """Verify a private answer envelope without contacting an LLM or printing text."""
    if not isinstance(report, Mapping):
        raise CopilotVerificationError("copilot answer must be a JSON object")
    unknown = sorted(str(key) for key in report if key not in _ANSWER_KEYS | _OPTIONAL_KEYS)
    if unknown:
        raise CopilotVerificationError(
            "copilot answer contains unknown fields: " + ", ".join(unknown)
        )
    missing = sorted(key for key in _ANSWER_KEYS if key not in report)
    if missing:
        raise CopilotVerificationError(
            "copilot answer is missing fields: " + ", ".join(missing)
        )
    if report.get("schema_version") != 1:
        raise CopilotVerificationError("copilot answer schema_version must be 1")
    if report.get("research_only") is not True:
        raise CopilotVerificationError("copilot answer research_only must be true")
    if report.get("disclaimer") != COPILOT_DISCLAIMER:
        raise CopilotVerificationError("copilot answer disclaimer does not match the safety contract")
    question = _safe_text(report.get("question"), "question", max_bytes=2_000)
    _safe_text(report.get("answer"), "answer", max_bytes=_MAX_ANSWER_BYTES)
    if not question:
        raise CopilotVerificationError("copilot answer question must be non-empty")
    _safe_metadata(report.get("model"), "model")
    _safe_metadata(report.get("provider"), "provider")
    _parse_timestamp(report.get("created_at"), "created_at")
    if "evaluation_case_id" in report:
        case_id = report.get("evaluation_case_id")
        if not isinstance(case_id, str) or not _IDENTIFIER.fullmatch(case_id):
            raise CopilotVerificationError("copilot answer evaluation_case_id is invalid")

    _verify_frozen_identity(report.get("frozen_identity"))
    citations = _verify_citations(report.get("citations"))
    trace = _verify_trace(report.get("tool_trace"))
    evidence_status = report.get("evidence_status")
    if evidence_status not in {"grounded", "uncited"}:
        raise CopilotVerificationError("copilot answer evidence_status is invalid")
    if evidence_status == "grounded" and not citations:
        raise CopilotVerificationError("grounded copilot answer must include citations")
    if evidence_status == "uncited" and citations:
        raise CopilotVerificationError("uncited copilot answer must not include citations")
    if sum(item["citation_count"] for item in trace) < len(citations):
        raise CopilotVerificationError("copilot answer trace undercounts citations")


def _verify_frozen_identity(value: object) -> None:
    identity = _exact_mapping(value, _IDENTITY_KEYS, "frozen_identity")
    _safe_relative_path(identity["path"], "frozen_identity.path")
    for key in ("sha256", "selection_hash", "locked_test_hash"):
        _digest(identity[key], f"frozen_identity.{key}")
    for key in ("data_mode", "as_of"):
        _non_empty_string(identity[key], f"frozen_identity.{key}")
    if identity["research_only"] is not True:
        raise CopilotVerificationError("frozen_identity research_only must be true")


def _verify_citations(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > _MAX_CITATIONS:
        raise CopilotVerificationError(
            f"copilot answer citations must be a list of at most {_MAX_CITATIONS} items"
        )
    records: list[dict[str, Any]] = []
    seen: set[tuple[object, ...]] = set()
    for index, item in enumerate(value, start=1):
        citation = _exact_mapping(item, _CITATION_KEYS, f"citation {index}")
        source = _safe_source(citation["source"], f"citation {index}.source")
        label = _non_empty_string(citation["label"], f"citation {index}.label")
        digest = _digest(citation["evidence_sha256"], f"citation {index}.evidence_sha256")
        fields = citation["fields"]
        if not isinstance(fields, list) or len(fields) > _MAX_FIELDS:
            raise CopilotVerificationError(f"citation {index}.fields must be a bounded list")
        if not all(isinstance(field, str) and field.strip() for field in fields):
            raise CopilotVerificationError(f"citation {index}.fields must contain non-empty strings")
        key = (source, label, digest, *fields)
        if key in seen:
            raise CopilotVerificationError(f"citation {index} is a duplicate")
        seen.add(key)
        records.append({"source": source, "label": label, "evidence_sha256": digest})
    return records


def _verify_trace(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > _MAX_TRACE_ITEMS:
        raise CopilotVerificationError(
            f"copilot answer tool_trace must be a list of at most {_MAX_TRACE_ITEMS} items"
        )
    records: list[dict[str, Any]] = []
    for expected_index, item in enumerate(value, start=1):
        trace = _exact_mapping(item, _TRACE_KEYS, f"tool trace {expected_index}")
        call_index = trace["call_index"]
        if isinstance(call_index, bool) or not isinstance(call_index, int) or call_index != expected_index:
            raise CopilotVerificationError("copilot answer tool_trace call indexes must be contiguous")
        name = trace["name"]
        if not isinstance(name, str) or not _TOOL_NAME.fullmatch(name):
            raise CopilotVerificationError(f"tool trace {expected_index} name is invalid")
        if name not in _READ_ONLY_TOOLS:
            raise CopilotVerificationError(f"tool trace {expected_index} uses a non-read-only tool")
        _digest(trace["arguments_sha256"], f"tool trace {expected_index}.arguments_sha256")
        _digest(trace["result_sha256"], f"tool trace {expected_index}.result_sha256")
        citation_count = trace["citation_count"]
        if (
            isinstance(citation_count, bool)
            or not isinstance(citation_count, int)
            or not 0 <= citation_count <= _MAX_CITATIONS
        ):
            raise CopilotVerificationError(
                f"tool trace {expected_index}.citation_count must be a non-negative bounded integer"
            )
        records.append({"name": name, "citation_count": citation_count})
    return records


def _exact_mapping(value: object, keys: frozenset[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CopilotVerificationError(f"copilot answer {label} must be an object")
    unknown = sorted(str(key) for key in value if key not in keys)
    missing = sorted(key for key in keys if key not in value)
    if unknown or missing:
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unknown:
            details.append("unknown " + ", ".join(unknown))
        raise CopilotVerificationError(f"copilot answer {label} fields invalid: {'; '.join(details)}")
    return value


def _safe_text(value: object, label: str, *, max_bytes: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CopilotVerificationError(f"copilot answer {label} must be non-empty text")
    if len(value.encode("utf-8")) > max_bytes:
        raise CopilotVerificationError(f"copilot answer {label} exceeds its size limit")
    return value


def _safe_metadata(value: object, label: str) -> str:
    text = _safe_text(value, label, max_bytes=160)
    if any(char in text for char in "\r\n") or "://" in text or "@" in text:
        raise CopilotVerificationError(f"copilot answer {label} contains unsafe metadata")
    return text


def _safe_source(value: object, label: str) -> str:
    source = _non_empty_string(value, label)
    if (
        not source.startswith(("snapshot:", "api:"))
        or any(char in source for char in "\r\n")
        or "://" in source
        or "@" in source
        or len(source.encode("utf-8")) > 512
    ):
        raise CopilotVerificationError(
            f"copilot answer {label} must be a bounded snapshot or API reference"
        )
    return source


def _safe_relative_path(value: object, label: str) -> str:
    path = _non_empty_string(value, label)
    if path.startswith(("/", "\\")) or ".." in path.split("/") or any(
        char in path for char in "\r\n"
    ):
        raise CopilotVerificationError(f"copilot answer {label} must be a safe relative path")
    return path


def _non_empty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CopilotVerificationError(f"copilot answer {label} must be a non-empty string")
    return value


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise CopilotVerificationError(
            f"copilot answer {label} must be a lowercase SHA-256 digest"
        )
    return value


def _parse_timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise CopilotVerificationError(f"copilot answer {label} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise CopilotVerificationError(
            f"copilot answer {label} must be an ISO-8601 timestamp"
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CopilotVerificationError(f"copilot answer {label} must include a timezone")
    return parsed.astimezone(UTC)
