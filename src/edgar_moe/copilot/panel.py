"""Bounded multi-profile review panels for the operator-only copilot.

The panel is intentionally a coordinator, not a second decision-making agent.
It runs a small set of independent read-only copilot perspectives over the
same question and evidence boundary.  Individual answer envelopes stay in a
private output directory; the aggregate report keeps only hashes, structural
counts, identities, and coarse failure/usage metadata.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol, cast

import orjson

from .contracts import MAX_QUESTION_BYTES, CopilotAnswer, content_hash
from .policy import (
    COPILOT_PROFILES,
    CopilotProfile,
    normalize_copilot_profile,
    validate_agent_identity,
)
from .verification import verify_copilot_answer_report

_PANEL_SCHEMA_VERSION = 1
_PANEL_STATUSES = frozenset({"complete", "partial", "failed", "review_required"})
_BOUNDARY_STATUSES = frozenset({"consistent", "mixed"})
_PROFILE_ID = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_ERROR_TYPE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_PROFILES = len(COPILOT_PROFILES)
_MAX_REPORT_FILES = 4
_MAX_USAGE_REQUESTS = _MAX_PROFILES * 36
_MAX_USAGE_DURATION_MS = _MAX_PROFILES * 2_000_000
_MAX_USAGE_TOKENS = 1_000_000_000
_MAX_USAGE_CONTEXT_BYTES = 2 * 1024 * 1024
_PANEL_DISCLAIMER = (
    "Multi-profile evidence review only; panel output is not a forecast, investment advice, "
    "model-promotion decision, or deployment authorization."
)


class PanelInputError(ValueError):
    """Raised when a panel aggregate violates its safe structural contract."""


class PanelRunner(Protocol):
    """Minimal runner contract used by the panel and its offline tests."""

    def ask(self, question: str) -> CopilotAnswer: ...


PanelRunnerFactory = Callable[[CopilotProfile], PanelRunner]


class PanelFailure:
    """Coarse, non-sensitive failure metadata for one profile."""

    __slots__ = ("profile_id", "error_type")

    def __init__(self, profile_id: CopilotProfile, error_type: str) -> None:
        if not _PROFILE_ID.fullmatch(profile_id):
            raise PanelInputError("panel failure profile_id is invalid")
        if not _ERROR_TYPE.fullmatch(error_type):
            raise PanelInputError("panel failure error_type is invalid")
        self.profile_id = profile_id
        self.error_type = error_type

    def as_dict(self) -> dict[str, str]:
        return {"profile_id": self.profile_id, "error_type": self.error_type}


def run_panel(
    question: str,
    profiles: Sequence[str],
    runner_factory: PanelRunnerFactory,
    output_dir: Path,
) -> dict[str, object]:
    """Run bounded specialist profiles and write private child envelopes.

    The factory is called once per selected profile.  Exceptions are reduced to
    their type name so provider payloads, prompts, URLs, and credentials never
    enter the aggregate report.  A failed profile does not prevent the other
    perspectives from producing independently verifiable evidence.
    """

    normalized_question = question.strip()
    if not normalized_question:
        raise PanelInputError("panel question must be non-empty")
    if len(normalized_question.encode("utf-8")) > MAX_QUESTION_BYTES:
        raise PanelInputError(f"panel question must be at most {MAX_QUESTION_BYTES} UTF-8 bytes")
    selected_profiles = normalize_panel_profiles(profiles)
    output_dir.mkdir(parents=True, exist_ok=True)

    reports: list[dict[str, object]] = []
    summaries: list[dict[str, object]] = []
    failures: list[PanelFailure] = []
    for profile in selected_profiles:
        report_path = output_dir / f"{profile}.json"
        try:
            answer = runner_factory(profile).ask(normalized_question)
            report = answer.as_dict()
            verify_copilot_answer_report(report)
            summary = _summarize_answer(profile, report, report_path.name)
            _write_json(report_path, report)
            reports.append(report)
            summaries.append(summary)
        except Exception as error:
            failures.append(PanelFailure(profile, type(error).__name__))

    aggregate = _build_panel_report(
        question=normalized_question,
        selected_profiles=selected_profiles,
        reports=reports,
        summaries=summaries,
        failures=failures,
    )
    write_panel_report(output_dir / "panel.json", aggregate)
    return aggregate


def normalize_panel_profiles(profiles: Sequence[str]) -> tuple[CopilotProfile, ...]:
    """Normalize a bounded, duplicate-free list of review perspectives."""

    if not profiles or len(profiles) > _MAX_PROFILES:
        raise PanelInputError(f"panel profiles must contain between 1 and {_MAX_PROFILES} items")
    normalized: list[CopilotProfile] = []
    for raw_profile in profiles:
        try:
            profile = normalize_copilot_profile(raw_profile)
        except (AttributeError, ValueError) as error:
            raise PanelInputError(str(error)) from error
        if profile in normalized:
            raise PanelInputError("panel profiles must not contain duplicates")
        normalized.append(profile)
    return tuple(normalized)


def write_panel_report(path: Path, report: Mapping[str, object]) -> None:
    """Verify and atomically write a content-addressed panel aggregate."""

    verify_panel_report(report)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(
        orjson.dumps(dict(report), option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS)
    )
    temporary.replace(path)


def verify_panel_report(report: Mapping[str, object]) -> None:
    """Verify a panel aggregate without reopening private child answer text."""

    if not isinstance(report, Mapping):
        raise PanelInputError("copilot panel must be a JSON object")
    expected_keys = {
        "schema_version",
        "research_only",
        "provider_contacted",
        "provider",
        "model",
        "question_sha256",
        "selected_profiles",
        "successful_profiles",
        "failed_profiles",
        "answer_summaries",
        "status",
        "boundary_status",
        "frozen_identity_sha256",
        "tool_contract_sha256",
        "usage",
        "disclaimer",
        "panel_sha256",
    }
    unknown = sorted(str(key) for key in report if key not in expected_keys)
    missing = sorted(key for key in expected_keys if key not in report)
    if unknown or missing:
        detail = []
        if missing:
            detail.append("missing " + ", ".join(missing))
        if unknown:
            detail.append("unknown " + ", ".join(unknown))
        raise PanelInputError("copilot panel fields invalid: " + "; ".join(detail))
    if report["schema_version"] != _PANEL_SCHEMA_VERSION:
        raise PanelInputError("copilot panel schema_version must be 1")
    if report["research_only"] is not True or report["provider_contacted"] is not True:
        raise PanelInputError("copilot panel safety flags are invalid")
    _safe_metadata(report["provider"], "provider")
    _safe_metadata(report["model"], "model")
    _digest(report["question_sha256"], "question_sha256")
    selected = _profile_list(report["selected_profiles"], "selected_profiles")
    successful = _profile_list(
        report["successful_profiles"], "successful_profiles", allow_empty=True
    )
    failed = _profile_list(
        [
            item.get("profile_id")
            for item in _mapping_list(report["failed_profiles"], "failed_profiles")
        ],
        "failed_profiles",
        allow_empty=True,
    )
    if set(successful) & set(failed):
        raise PanelInputError("panel successful and failed profiles overlap")
    if set(successful) | set(failed) != set(selected):
        raise PanelInputError("panel profile outcomes do not cover selected profiles")
    answer_summaries = _mapping_list(report["answer_summaries"], "answer_summaries")
    if len(answer_summaries) != len(successful):
        raise PanelInputError("panel answer_summaries must match successful_profiles")
    _verify_answer_summaries(answer_summaries, successful)
    _verify_failures(report["failed_profiles"], failed)
    summary_frozen_hashes = {str(summary["frozen_identity_sha256"]) for summary in answer_summaries}
    summary_tool_hashes = {
        str(cast(Mapping[str, object], summary["agent_identity"])["tool_contract_sha256"])
        for summary in answer_summaries
    }
    expected_boundary_status = (
        "consistent"
        if len(summary_frozen_hashes) <= 1 and len(summary_tool_hashes) <= 1
        else "mixed"
    )
    status = report["status"]
    if status not in _PANEL_STATUSES:
        raise PanelInputError("copilot panel status is invalid")
    boundary_status = report["boundary_status"]
    if boundary_status not in _BOUNDARY_STATUSES:
        raise PanelInputError("copilot panel boundary_status is invalid")
    if boundary_status != expected_boundary_status:
        raise PanelInputError(
            "copilot panel boundary_status is inconsistent with answer identities"
        )
    expected_frozen_hash = (
        next(iter(summary_frozen_hashes)) if len(summary_frozen_hashes) == 1 else None
    )
    expected_tool_hash = next(iter(summary_tool_hashes)) if len(summary_tool_hashes) == 1 else None
    if report["frozen_identity_sha256"] != expected_frozen_hash:
        raise PanelInputError("copilot panel frozen identity digest is inconsistent")
    if report["tool_contract_sha256"] != expected_tool_hash:
        raise PanelInputError("copilot panel tool contract digest is inconsistent")
    expected_status = (
        "failed"
        if not successful
        else "review_required"
        if boundary_status == "mixed"
        else "partial"
        if failed
        else "complete"
    )
    if status != expected_status:
        raise PanelInputError("copilot panel status is inconsistent with its outcomes")
    if report["disclaimer"] != _PANEL_DISCLAIMER:
        raise PanelInputError("copilot panel disclaimer does not match the safety contract")
    _optional_digest(report["frozen_identity_sha256"], "frozen_identity_sha256")
    _optional_digest(report["tool_contract_sha256"], "tool_contract_sha256")
    _verify_usage(report["usage"], expected_answer_count=len(successful))
    _digest(report["panel_sha256"], "panel_sha256")
    unsigned = dict(report)
    unsigned.pop("panel_sha256")
    if content_hash(unsigned) != report["panel_sha256"]:
        raise PanelInputError("copilot panel hash does not match its content")


def _build_panel_report(
    *,
    question: str,
    selected_profiles: tuple[CopilotProfile, ...],
    reports: Sequence[Mapping[str, object]],
    summaries: Sequence[Mapping[str, object]],
    failures: Sequence[PanelFailure],
) -> dict[str, object]:
    successful_profiles = tuple(
        cast(CopilotProfile, summary["profile_id"]) for summary in summaries
    )
    failed_profiles = tuple(failure.profile_id for failure in failures)
    frozen_hashes = {
        str(frozen_identity["sha256"])
        for report in reports
        if isinstance(report.get("frozen_identity"), Mapping)
        for frozen_identity in [cast(Mapping[str, object], report["frozen_identity"])]
    }
    tool_hashes = {
        str(agent_identity["tool_contract_sha256"])
        for report in reports
        if isinstance(report.get("agent_identity"), Mapping)
        for agent_identity in [cast(Mapping[str, object], report["agent_identity"])]
    }
    boundary_status = "consistent" if len(frozen_hashes) <= 1 and len(tool_hashes) <= 1 else "mixed"
    status = (
        "failed"
        if not successful_profiles
        else "review_required"
        if boundary_status == "mixed"
        else "partial"
        if failed_profiles
        else "complete"
    )
    provider_values = {str(report["provider"]) for report in reports}
    model_values = {str(report["model"]) for report in reports}
    provider = next(iter(provider_values), "unknown") if len(provider_values) == 1 else "mixed"
    model = next(iter(model_values), "unknown") if len(model_values) == 1 else "mixed"
    aggregate: dict[str, object] = {
        "schema_version": _PANEL_SCHEMA_VERSION,
        "research_only": True,
        "provider_contacted": True,
        "provider": provider,
        "model": model,
        "question_sha256": content_hash(question),
        "selected_profiles": list(selected_profiles),
        "successful_profiles": list(successful_profiles),
        "failed_profiles": [failure.as_dict() for failure in failures],
        "answer_summaries": list(summaries),
        "status": status,
        "boundary_status": boundary_status,
        "frozen_identity_sha256": (
            next(iter(frozen_hashes), None) if len(frozen_hashes) == 1 else None
        ),
        "tool_contract_sha256": (next(iter(tool_hashes), None) if len(tool_hashes) == 1 else None),
        "usage": _aggregate_usage(reports),
        "disclaimer": _PANEL_DISCLAIMER,
    }
    aggregate["panel_sha256"] = content_hash(aggregate)
    return aggregate


def _summarize_answer(
    profile: CopilotProfile, report: Mapping[str, object], report_file: str
) -> dict[str, object]:
    identity = report.get("agent_identity")
    if not isinstance(identity, Mapping):
        raise PanelInputError("copilot panel answers must carry an agent identity")
    normalized_identity = validate_agent_identity(identity)
    if normalized_identity.get("profile_id") != profile:
        raise PanelInputError("copilot panel answer profile does not match its identity")
    frozen_identity = report.get("frozen_identity")
    if not isinstance(frozen_identity, Mapping) or not isinstance(
        frozen_identity.get("sha256"), str
    ):
        raise PanelInputError("copilot panel answer is missing a frozen identity digest")
    return {
        "profile_id": profile,
        "report_file": report_file,
        "answer_sha256": content_hash(report),
        "evidence_status": report["evidence_status"],
        "citation_count": len(cast(list[object], report["citations"])),
        "tool_call_count": len(cast(list[object], report["tool_trace"])),
        "frozen_identity_sha256": frozen_identity["sha256"],
        "agent_identity": normalized_identity,
    }


def _aggregate_usage(reports: Sequence[Mapping[str, object]]) -> dict[str, object] | None:
    if not reports:
        return None
    usages = [report.get("usage") for report in reports]
    if not all(isinstance(usage, Mapping) for usage in usages):
        return None
    records = cast(list[Mapping[str, object]], usages)
    required = ("request_count", "duration_ms")
    if any(not all(isinstance(record.get(field), int) for record in records) for field in required):
        return None

    def optional_sum(field: str) -> int | None:
        values = [record.get(field) for record in records]
        if any(value is None for value in values):
            return None
        if not all(isinstance(value, int) and not isinstance(value, bool) for value in values):
            return None
        return sum(cast(int, value) for value in values)

    contexts = [record.get("peak_context_bytes") for record in records]
    peak_context_bytes = (
        max(cast(int, value) for value in contexts)
        if contexts
        and all(isinstance(value, int) and not isinstance(value, bool) for value in contexts)
        else None
    )
    return {
        "answer_count": len(records),
        "request_count": sum(cast(int, record["request_count"]) for record in records),
        "duration_ms": sum(cast(int, record["duration_ms"]) for record in records),
        "prompt_tokens": optional_sum("prompt_tokens"),
        "completion_tokens": optional_sum("completion_tokens"),
        "total_tokens": optional_sum("total_tokens"),
        "peak_context_bytes": peak_context_bytes,
    }


def _verify_answer_summaries(value: object, successful: Sequence[str]) -> None:
    summaries = _mapping_list(value, "answer_summaries")
    expected = set(successful)
    seen: set[str] = set()
    for summary in summaries:
        keys = {
            "profile_id",
            "report_file",
            "answer_sha256",
            "evidence_status",
            "citation_count",
            "tool_call_count",
            "frozen_identity_sha256",
            "agent_identity",
        }
        _exact_keys(summary, keys, "answer summary")
        profile = summary["profile_id"]
        if not isinstance(profile, str) or profile not in expected or profile in seen:
            raise PanelInputError("panel answer summary profile is invalid")
        seen.add(profile)
        report_file = summary["report_file"]
        if report_file != f"{profile}.json":
            raise PanelInputError("panel answer summary report_file is invalid")
        _digest(summary["answer_sha256"], "answer summary answer_sha256")
        _digest(
            summary["frozen_identity_sha256"],
            "answer summary frozen_identity_sha256",
        )
        if summary["evidence_status"] not in {"grounded", "uncited"}:
            raise PanelInputError("panel answer summary evidence_status is invalid")
        _bounded_int(summary["citation_count"], "answer summary citation_count", 0, 64)
        _bounded_int(summary["tool_call_count"], "answer summary tool_call_count", 0, 8)
        identity = summary["agent_identity"]
        try:
            normalized = validate_agent_identity(identity)
        except ValueError as error:
            raise PanelInputError(str(error)) from error
        if normalized.get("profile_id") != profile:
            raise PanelInputError("panel answer summary identity profile is invalid")
    if seen != expected:
        raise PanelInputError("panel answer summaries do not cover successful profiles")


def _verify_failures(value: object, profiles: Sequence[str]) -> None:
    failures = _mapping_list(value, "failed_profiles")
    expected = set(profiles)
    seen: set[str] = set()
    for failure in failures:
        _exact_keys(failure, {"profile_id", "error_type"}, "panel failure")
        profile = failure["profile_id"]
        error_type = failure["error_type"]
        if not isinstance(profile, str) or profile not in expected or profile in seen:
            raise PanelInputError("panel failure profile is invalid")
        if not isinstance(error_type, str) or not _ERROR_TYPE.fullmatch(error_type):
            raise PanelInputError("panel failure error_type is invalid")
        seen.add(profile)


def _verify_usage(value: object, *, expected_answer_count: int) -> None:
    if value is None:
        return
    if not isinstance(value, Mapping):
        raise PanelInputError("copilot panel usage must be an object or null")
    required = {
        "answer_count",
        "request_count",
        "duration_ms",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "peak_context_bytes",
    }
    _exact_keys(value, required, "panel usage")
    _bounded_int(value["answer_count"], "panel usage answer_count", 1, _MAX_PROFILES)
    if value["answer_count"] != expected_answer_count:
        raise PanelInputError("panel usage answer_count is inconsistent with successful profiles")
    _bounded_int(value["request_count"], "panel usage request_count", 1, _MAX_USAGE_REQUESTS)
    _bounded_int(value["duration_ms"], "panel usage duration_ms", 0, _MAX_USAGE_DURATION_MS)
    for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
        if value[field] is not None:
            _bounded_int(value[field], f"panel usage {field}", 0, _MAX_USAGE_TOKENS)
    if value["peak_context_bytes"] is not None:
        _bounded_int(
            value["peak_context_bytes"],
            "panel usage peak_context_bytes",
            0,
            _MAX_USAGE_CONTEXT_BYTES,
        )


def _profile_list(value: object, label: str, *, allow_empty: bool = False) -> list[str]:
    minimum = 0 if allow_empty else 1
    if not isinstance(value, list) or not minimum <= len(value) <= _MAX_PROFILES:
        raise PanelInputError(f"copilot panel {label} must be a bounded profile list")
    profiles: list[str] = []
    for item in value:
        if not isinstance(item, str) or not _PROFILE_ID.fullmatch(item):
            raise PanelInputError(f"copilot panel {label} contains an invalid profile")
        try:
            normalized = normalize_copilot_profile(item)
        except ValueError as error:
            raise PanelInputError(f"copilot panel {label} contains an invalid profile") from error
        if normalized in profiles:
            raise PanelInputError(f"copilot panel {label} contains duplicate profiles")
        profiles.append(normalized)
    return profiles


def _mapping_list(value: object, label: str) -> list[Mapping[str, object]]:
    if not isinstance(value, list) or len(value) > _MAX_REPORT_FILES:
        raise PanelInputError(f"copilot panel {label} must be a bounded list")
    if not all(isinstance(item, Mapping) for item in value):
        raise PanelInputError(f"copilot panel {label} must contain objects")
    return cast(list[Mapping[str, object]], value)


def _exact_keys(value: Mapping[str, object], keys: set[str], label: str) -> None:
    actual = {str(key) for key in value}
    if actual != keys:
        raise PanelInputError(f"copilot panel {label} fields are invalid")


def _safe_metadata(value: object, label: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > 160:
        raise PanelInputError(f"copilot panel {label} is invalid")
    if any(char in value for char in "\r\n") or "://" in value or "@" in value:
        raise PanelInputError(f"copilot panel {label} contains unsafe metadata")


def _digest(value: object, label: str) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise PanelInputError(f"copilot panel {label} must be a lowercase SHA-256 digest")


def _optional_digest(value: object, label: str) -> None:
    if value is not None:
        _digest(value, label)


def _bounded_int(value: object, label: str, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise PanelInputError(f"copilot panel {label} is out of bounds")


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(
        orjson.dumps(dict(payload), option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS)
    )
    temporary.replace(path)
