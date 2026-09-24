"""Allowlisted, read-only research tools exposed to the copilot."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from edgar_moe.api.repository import SnapshotRepository
from edgar_moe.forward.registry import ForwardRegistry

from .contracts import Citation, ToolDefinition, ToolResult, content_hash
from .diagnostics import DiagnosticSummaryError, read_forward_diagnostic_summary

_ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_MAX_EVENT_ROWS = 10


class ToolInputError(ValueError):
    """Raised when a model requests an invalid or unsafe tool argument."""


@dataclass(frozen=True)
class ReadOnlyToolset:
    """A small capability boundary; there are intentionally no write tools."""

    repository: SnapshotRepository
    registry: ForwardRegistry | None = None
    diagnostic_path: Path | None = None
    diagnostic_history_path: Path | None = None

    def definitions(self) -> tuple[ToolDefinition, ...]:
        definitions = [
            ToolDefinition(
                name="get_frozen_identity",
                description=(
                    "Read the content-addressed identity and research-only status of the frozen v1 "
                    "snapshot. Use this before making claims about the locked study."
                ),
                parameters={"type": "object", "properties": {}, "additionalProperties": False},
            ),
            ToolDefinition(
                name="get_study_summary",
                description=(
                    "Read the frozen study summary, predictive metrics, and cost scenarios. "
                    "Do not treat these derived results as investment advice."
                ),
                parameters={"type": "object", "properties": {}, "additionalProperties": False},
            ),
            ToolDefinition(
                name="get_methodology",
                description="Read the frozen study target, split, model, portfolio, costs, and limitations.",
                parameters={"type": "object", "properties": {}, "additionalProperties": False},
            ),
            ToolDefinition(
                name="get_experiment_results",
                description="Read the model-selection experiment table from the frozen snapshot.",
                parameters={"type": "object", "properties": {}, "additionalProperties": False},
            ),
            ToolDefinition(
                name="search_filing_events",
                description=(
                    "Read a bounded page of derived filing events from the frozen snapshot. "
                    "Filter by ticker or direction when the question asks about examples."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "ticker": {"type": "string", "maxLength": 16},
                        "direction": {"type": "string", "enum": ["long", "short", "neutral"]},
                        "limit": {"type": "integer", "minimum": 1, "maximum": _MAX_EVENT_ROWS},
                    },
                    "additionalProperties": False,
                },
            ),
            ToolDefinition(
                name="get_filing_event",
                description=(
                    "Read one derived filing event by SEC accession number. This does not fetch raw "
                    "filing text or any external URL."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "accession_number": {
                            "type": "string",
                            "pattern": r"^\d{10}-\d{2}-\d{6}$",
                        }
                    },
                    "required": ["accession_number"],
                    "additionalProperties": False,
                },
            ),
            ToolDefinition(
                name="get_governance_status",
                description=(
                    "Read frozen-v1 governance controls and the optional append-only forward registry "
                    "status. Never infer provider evidence that is marked pending."
                ),
                parameters={"type": "object", "properties": {}, "additionalProperties": False},
            ),
        ]
        if self.diagnostic_path is not None:
            definitions.append(
                ToolDefinition(
                    name="get_forward_diagnostic",
                    description=(
                        "Read a redacted summary of the operator-supplied short-horizon forward "
                        "diagnostic. It is not the official 20-session evaluation and contains "
                        "no forecast or filing observations."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                )
            )
        if self.diagnostic_history_path is not None:
            definitions.append(
                ToolDefinition(
                    name="get_forward_diagnostic_history",
                    description=(
                        "Read a verified, redacted history of repeated short-horizon forward "
                        "diagnostics. It is research-only, does not replace the official "
                        "20-session evaluation, and contains no forecast or filing observations. "
                        "Its ready status only means the configured snapshot count and report "
                        "checks passed; snapshots may overlap, independence is not assessed, and "
                        "the history is never model-promotion or retraining evidence."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                )
            )
        return tuple(definitions)

    def execute(self, name: str, arguments: dict[str, object]) -> ToolResult:
        """Execute exactly one allowlisted read operation."""
        if name == "get_frozen_identity":
            payload = self.repository.frozen_identity()
            return _result(
                name,
                payload,
                source="snapshot:data/demo/snapshot.json",
                label="Frozen v1 snapshot identity",
                fields=("sha256", "data_mode", "as_of", "selection_hash", "locked_test_hash"),
            )
        if name == "get_study_summary":
            payload = self.repository.summary()
            return _result(
                name,
                payload,
                source="snapshot:data/demo/snapshot.json",
                label="Frozen study summary and cost scenarios",
                fields=("metadata", "summary", "predictive_metrics", "portfolio_scenarios"),
            )
        if name == "get_methodology":
            payload = self.repository.methodology()
            return _result(
                name,
                payload,
                source="snapshot:data/demo/snapshot.json",
                label="Frozen study methodology",
                fields=("target", "split", "model", "portfolio", "costs", "limitations"),
            )
        if name == "get_experiment_results":
            payload = {"experiments": self.repository.experiments()}
            return _result(
                name,
                payload,
                source="snapshot:data/demo/snapshot.json",
                label="Frozen model-selection experiments",
                fields=("experiments",),
            )
        if name == "search_filing_events":
            payload = self._search_events(arguments)
            return _result(
                name,
                payload,
                source="api:/api/v1/events",
                label="Derived filing-event search",
                fields=("items", "total"),
            )
        if name == "get_filing_event":
            accession_number = _required_string(arguments, "accession_number", max_length=20)
            if not _ACCESSION.fullmatch(accession_number):
                raise ToolInputError("accession_number must be an SEC accession number")
            event_payload = self.repository.event(accession_number)
            event_result: dict[str, object]
            if event_payload is None:
                event_result = {"found": False, "accession_number": accession_number}
            else:
                event_result = {"found": True, "event": event_payload}
            return _result(
                name,
                event_result,
                source="api:/api/v1/events/{accession_number}",
                label=f"Derived filing event {accession_number}",
                fields=("found", "event"),
            )
        if name == "get_governance_status":
            payload = self._governance_status()
            return _result(
                name,
                payload,
                source="api:/api/v1/governance",
                label="Frozen-v1 governance and forward status",
                fields=("frozen_v1", "public_data", "controls", "forward_status"),
            )
        if name == "get_forward_diagnostic":
            if self.diagnostic_path is None:
                raise ToolInputError("forward diagnostic is not configured")
            try:
                payload = read_forward_diagnostic_summary(self.diagnostic_path)
            except DiagnosticSummaryError:
                payload = {
                    "available": False,
                    "reason": "diagnostic_report_unavailable_or_invalid",
                }
                return _result(
                    name,
                    payload,
                    source="snapshot:forward-diagnostic",
                    label="Forward diagnostic availability",
                    fields=("available", "reason"),
                )
            return _result(
                name,
                payload,
                source="snapshot:forward-diagnostic",
                label="Redacted short-horizon forward diagnostic",
                fields=(
                    "status",
                    "as_of",
                    "forecast_count",
                    "matured_count",
                    "pending_count",
                    "coverage",
                    "rank_ic",
                    "rmse",
                    "mae",
                    "directional_accuracy",
                    "unique_event_evaluation",
                    "source_sha256",
                ),
            )
        if name == "get_forward_diagnostic_history":
            if self.diagnostic_history_path is None:
                raise ToolInputError("forward diagnostic history is not configured")
            from edgar_moe.forward.diagnostic_history import (
                DiagnosticHistoryError,
                read_forward_diagnostic_history,
            )

            try:
                payload = read_forward_diagnostic_history(self.diagnostic_history_path)
            except DiagnosticHistoryError:
                payload = {
                    "available": False,
                    "reason": "diagnostic_history_unavailable_or_invalid",
                }
                return _result(
                    name,
                    payload,
                    source="snapshot:forward-diagnostic-history",
                    label="Forward diagnostic history availability",
                    fields=("available", "reason"),
                )
            fields = [
                "status",
                "minimum_reports",
                "report_count",
                "horizon_sessions",
                "latest_as_of",
                "disclaimer",
                "research_only",
                "v1_immutable",
                "automatic_retraining",
                "official_evaluation_untouched",
                "observations",
                "history_sha256",
            ]
            if "snapshot_independence" in payload:
                fields.extend(("snapshot_independence", "promotion_eligible"))
            return _result(
                name,
                payload,
                source="snapshot:forward-diagnostic-history",
                label="Verified redacted short-horizon forward diagnostic history",
                fields=tuple(fields),
            )
        raise ToolInputError(f"tool is not allowlisted: {name}")

    def _search_events(self, arguments: dict[str, object]) -> dict[str, object]:
        ticker = _optional_string(arguments, "ticker", max_length=16)
        direction = _optional_string(arguments, "direction", max_length=8)
        if direction is not None and direction not in {"long", "short", "neutral"}:
            raise ToolInputError("direction must be long, short, or neutral")
        limit = _optional_integer(arguments, "limit", default=_MAX_EVENT_ROWS)
        if not 1 <= limit <= _MAX_EVENT_ROWS:
            raise ToolInputError(f"limit must be between 1 and {_MAX_EVENT_ROWS}")
        return self.repository.event_page(
            ticker=ticker,
            direction=direction,
            limit=limit,
        )

    def _governance_status(self) -> dict[str, object]:
        frozen_identity = self.repository.frozen_identity()
        forward_status = _forward_status(self.registry)
        return {
            "schema_version": 1,
            "frozen_v1": frozen_identity,
            "public_data": {
                "raw_sources_public": False,
                "derived_output_public": True,
                "redistribution_status": "operator_review_required",
            },
            "controls": [
                {
                    "key": "frozen_v1_identity",
                    "status": "enforced",
                    "owner": "repository",
                    "summary": "Model, selection, locked result, and snapshot identity remain content-addressed.",
                },
                {
                    "key": "pre_entry_forecasts",
                    "status": "enforced",
                    "owner": "repository",
                    "summary": "Prospective forecasts require a recorded timestamp before tradable entry.",
                },
                {
                    "key": "append_only_outcomes",
                    "status": "enforced",
                    "owner": "repository",
                    "summary": "Outcomes are appended after maturity; the frozen v1 result is not overwritten.",
                },
                {
                    "key": "provider_operations",
                    "status": "pending_operator_evidence",
                    "owner": "operator",
                    "summary": "Provider grants, backups, restore timing, and object-store failure evidence require an external exercise.",
                },
            ],
            "forward_status": forward_status,
        }


def _result(
    name: str,
    payload: object,
    *,
    source: str,
    label: str,
    fields: tuple[str, ...],
) -> ToolResult:
    return ToolResult(
        name=name,
        payload=payload,
        citations=(
            Citation(
                source=source,
                label=label,
                evidence_sha256=content_hash(payload),
                fields=fields,
            ),
        ),
    )


def _forward_status(registry: ForwardRegistry | None) -> dict[str, object]:
    if registry is None:
        return {
            "configured": False,
            "available": False,
            "message": "Forward registry is not configured in this deployment.",
        }
    try:
        return {
            "configured": True,
            "available": True,
            "status": registry.status(),
            "performance": registry.performance(),
            "message": "Append-only forward registry is available.",
        }
    except SQLAlchemyError:
        return {
            "configured": True,
            "available": False,
            "message": "Forward registry is configured but currently unavailable.",
        }


def _required_string(arguments: dict[str, object], key: str, *, max_length: int) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise ToolInputError(f"{key} must be a non-empty string of at most {max_length} characters")
    return value.strip()


def _optional_string(arguments: dict[str, object], key: str, *, max_length: int) -> str | None:
    if key not in arguments or arguments[key] is None:
        return None
    return _required_string(arguments, key, max_length=max_length)


def _optional_integer(arguments: dict[str, object], key: str, *, default: int) -> int:
    value = arguments.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolInputError(f"{key} must be an integer")
    return value
