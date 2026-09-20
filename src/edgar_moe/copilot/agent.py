"""Bounded, evidence-grounded agent loop for the optional research copilot."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from json import JSONDecodeError
from time import monotonic, sleep
from typing import Any, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .contracts import (
    Citation,
    CopilotAnswer,
    CopilotUsage,
    ToolDefinition,
    ToolResult,
    ToolTrace,
    content_hash,
)
from .policy import COPILOT_SYSTEM_PROMPT, build_agent_identity
from .tools import ReadOnlyToolset, ToolInputError
from .verification import verify_copilot_answer_report

_MAX_QUESTION_LENGTH = 2_000
_MAX_PROVIDER_RESPONSE_BYTES = 2_000_000
_MAX_PROVIDER_USAGE_TOKENS = 100_000_000
_MAX_RETRIES = 3
_MAX_RETRY_BACKOFF_SECONDS = 5.0
_DEFAULT_MAX_DURATION_SECONDS = 300.0
_MAX_DURATION_SECONDS = 900.0
_DEFAULT_MAX_CONTEXT_BYTES = 512 * 1024
_MIN_CONTEXT_BYTES = 16 * 1024
_MAX_CONTEXT_BYTES = 2 * 1024 * 1024
_DEFAULT_PROVIDER_ALLOWED_HOSTS = ("api.openai.com",)
_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_RETRYABLE_HTTP_STATUSES = frozenset({408, 425, 429}) | frozenset(range(500, 600))
_REJECTED_TOOL_TRACE_NAME = "rejected_tool_request"


class CopilotError(RuntimeError):
    """Base error for safe, operator-visible copilot failures."""


class CopilotProviderError(CopilotError):
    """Raised when an LLM provider cannot produce a valid response."""


class _NoRedirectHandler(HTTPRedirectHandler):
    """Fail closed instead of following a provider-controlled redirect."""

    def redirect_request(
        self,
        req: Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> Request:
        del req, fp, code, msg, headers, newurl
        raise CopilotProviderError("copilot provider redirects are not allowed")


def _open_provider_request(request: Request, *, timeout: float) -> Any:
    """Open one provider request without following redirects."""
    return build_opener(_NoRedirectHandler()).open(request, timeout=timeout)


@dataclass(frozen=True)
class ProviderToolCall:
    call_id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ProviderUsage:
    """The small numeric usage subset accepted from a provider response."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class ProviderResponse:
    content: str
    tool_calls: tuple[ProviderToolCall, ...]
    model: str
    usage: ProviderUsage | None = None
    request_count: int = 1

    def as_assistant_message(self) -> dict[str, object]:
        payload: dict[str, object] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": call.call_id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in self.tool_calls
            ]
        return payload


class CopilotProvider(Protocol):
    """Minimal provider contract so agent behavior can be tested without a network."""

    model: str
    provider_name: str

    def complete(
        self,
        messages: Sequence[dict[str, object]],
        tools: Sequence[ToolDefinition],
    ) -> ProviderResponse:
        ...


class OpenAICompatibleProvider:
    """Small standard-library client for OpenAI-compatible chat-completions APIs.

    The endpoint is deliberately explicit and constrained: HTTPS is required for
    remote providers, while plain HTTP is accepted only for loopback local
    runtimes such as Ollama. Provider responses are never written to the report.
    """

    provider_name = "openai-compatible"

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        model: str,
        allowed_hosts: str | Sequence[str] = _DEFAULT_PROVIDER_ALLOWED_HOSTS,
        timeout_seconds: float = 30.0,
        max_tokens: int = 800,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.25,
    ) -> None:
        self.allowed_hosts = normalize_provider_allowed_hosts(allowed_hosts)
        self.endpoint = normalize_provider_endpoint(
            endpoint,
            allowed_hosts=self.allowed_hosts,
        )
        self.api_key = api_key
        self.model = model.strip()
        if not self.model or len(self.model) > 160:
            raise ValueError("copilot model must be a non-empty name of at most 160 characters")
        if timeout_seconds <= 0 or timeout_seconds > 120:
            raise ValueError("copilot provider timeout must be between 0 and 120 seconds")
        if not 1 <= max_tokens <= 8_000:
            raise ValueError("copilot max tokens must be between 1 and 8000")
        if (
            isinstance(max_retries, bool)
            or not isinstance(max_retries, int)
            or not 0 <= max_retries <= _MAX_RETRIES
        ):
            raise ValueError(f"copilot max retries must be between 0 and {_MAX_RETRIES}")
        if (
            isinstance(retry_backoff_seconds, bool)
            or not isinstance(retry_backoff_seconds, (int, float))
            or not math.isfinite(retry_backoff_seconds)
            or not 0 <= retry_backoff_seconds <= _MAX_RETRY_BACKOFF_SECONDS
        ):
            raise ValueError(
                "copilot retry backoff must be between 0 and "
                f"{_MAX_RETRY_BACKOFF_SECONDS} seconds"
            )
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds

    def complete(
        self,
        messages: Sequence[dict[str, object]],
        tools: Sequence[ToolDefinition],
    ) -> ProviderResponse:
        body = {
            "model": self.model,
            "messages": list(messages),
            "tools": [tool.as_provider_schema() for tool in tools],
            "tool_choice": "auto",
            "temperature": 0,
            "max_tokens": self.max_tokens,
        }
        request = Request(
            self.endpoint,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        attempts = 0
        while True:
            attempts += 1
            try:
                with _open_provider_request(request, timeout=self.timeout_seconds) as response:
                    raw = response.read(_MAX_PROVIDER_RESPONSE_BYTES + 1)
            except HTTPError as error:
                if error.code not in _RETRYABLE_HTTP_STATUSES or attempts > self.max_retries:
                    raise CopilotProviderError(
                        f"copilot provider returned HTTP {error.code}"
                    ) from error
                _sleep_before_retry(attempts, self.retry_backoff_seconds)
                continue
            except (URLError, TimeoutError, OSError) as error:
                if attempts > self.max_retries:
                    raise CopilotProviderError("copilot provider request failed") from error
                _sleep_before_retry(attempts, self.retry_backoff_seconds)
                continue
            break
        if len(raw) > _MAX_PROVIDER_RESPONSE_BYTES:
            raise CopilotProviderError("copilot provider response exceeded the size limit")
        try:
            payload = json.loads(raw)
        except JSONDecodeError as error:
            raise CopilotProviderError("copilot provider returned invalid JSON") from error
        parsed = _parse_provider_response(payload, fallback_model=self.model)
        return ProviderResponse(
            content=parsed.content,
            tool_calls=parsed.tool_calls,
            model=parsed.model,
            usage=parsed.usage,
            request_count=attempts,
        )

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers


class ResearchCopilot:
    """Run a bounded read-only agent against the frozen research evidence."""

    def __init__(
        self,
        *,
        provider: CopilotProvider,
        toolset: ReadOnlyToolset,
        max_tool_calls: int = 4,
        max_duration_seconds: float = _DEFAULT_MAX_DURATION_SECONDS,
        max_context_bytes: int = _DEFAULT_MAX_CONTEXT_BYTES,
    ) -> None:
        if not 1 <= max_tool_calls <= 8:
            raise ValueError("max_tool_calls must be between 1 and 8")
        if (
            isinstance(max_duration_seconds, bool)
            or not isinstance(max_duration_seconds, (int, float))
            or not math.isfinite(max_duration_seconds)
            or not 1 <= max_duration_seconds <= _MAX_DURATION_SECONDS
        ):
            raise ValueError(
                "max_duration_seconds must be between 1 and "
                f"{_MAX_DURATION_SECONDS:g} seconds"
            )
        if (
            isinstance(max_context_bytes, bool)
            or not isinstance(max_context_bytes, int)
            or not _MIN_CONTEXT_BYTES <= max_context_bytes <= _MAX_CONTEXT_BYTES
        ):
            raise ValueError(
                "max_context_bytes must be between "
                f"{_MIN_CONTEXT_BYTES} and {_MAX_CONTEXT_BYTES} bytes"
            )
        self.provider = provider
        self.toolset = toolset
        self.max_tool_calls = max_tool_calls
        self.max_duration_seconds = max_duration_seconds
        self.max_context_bytes = max_context_bytes

    def ask(self, question: str) -> CopilotAnswer:
        normalized_question = question.strip()
        if not normalized_question or len(normalized_question) > _MAX_QUESTION_LENGTH:
            raise ValueError(
                f"question must be between 1 and {_MAX_QUESTION_LENGTH} characters"
            )

        messages: list[dict[str, object]] = [
            {"role": "system", "content": COPILOT_SYSTEM_PROMPT},
            {"role": "user", "content": normalized_question},
        ]
        run_started = monotonic()
        citations: list[Citation] = []
        trace: list[ToolTrace] = []
        tool_calls_seen = 0
        evidence_tool_call_seen = False
        peak_context_bytes = 0
        provider_usages: list[ProviderUsage | None] = []
        provider_request_counts: list[int] = []
        tool_definitions = self.toolset.definitions()
        agent_identity = build_agent_identity(
            tool_definitions,
            self.max_tool_calls,
            self.max_duration_seconds,
            self.max_context_bytes,
        )
        allowed_tool_names = {tool.name for tool in tool_definitions}

        for _ in range(self.max_tool_calls + 1):
            if monotonic() - run_started >= self.max_duration_seconds:
                raise CopilotError("copilot execution time budget exceeded")
            context_bytes = _serialized_context_bytes(messages, tool_definitions)
            peak_context_bytes = max(peak_context_bytes, context_bytes)
            if context_bytes > self.max_context_bytes:
                raise CopilotError("copilot context budget exceeded")
            response = self.provider.complete(messages, tool_definitions)
            if (
                isinstance(response.request_count, bool)
                or not 1 <= response.request_count <= _MAX_RETRIES + 1
            ):
                raise CopilotProviderError("copilot provider request count was invalid")
            provider_usages.append(response.usage)
            provider_request_counts.append(response.request_count)
            messages.append(response.as_assistant_message())
            if not response.tool_calls:
                answer = response.content.strip()
                if not answer:
                    raise CopilotProviderError("copilot provider returned an empty answer")
                if evidence_tool_call_seen and not citations:
                    raise CopilotError(
                        "copilot answer omitted citations after evidence tool use"
                    )
                envelope = CopilotAnswer(
                    question=normalized_question,
                    answer=answer,
                    model=response.model or self.provider.model,
                    provider=self.provider.provider_name,
                    created_at=datetime.now(UTC).isoformat(),
                    frozen_identity=self.toolset.repository.frozen_identity(),
                    citations=_unique_citations(citations),
                    trace=tuple(trace),
                    evidence_status="grounded" if citations else "uncited",
                    usage=CopilotUsage(
                        request_count=sum(provider_request_counts),
                        duration_ms=max(0, round((monotonic() - run_started) * 1000)),
                        prompt_tokens=_sum_usage(provider_usages, "prompt_tokens"),
                        completion_tokens=_sum_usage(provider_usages, "completion_tokens"),
                        total_tokens=_sum_usage(provider_usages, "total_tokens"),
                        peak_context_bytes=peak_context_bytes,
                    ),
                    agent_identity=agent_identity,
                )
                # Treat verification as part of the generation boundary. A
                # caller must never be able to persist an answer envelope that
                # bypasses the same integrity checks used before sharing.
                verify_copilot_answer_report(envelope.as_dict())
                return envelope

            for call in response.tool_calls:
                tool_calls_seen += 1
                if tool_calls_seen > self.max_tool_calls:
                    raise CopilotError("copilot tool-call budget exceeded")
                result, arguments = self._execute_call(call)
                _verify_tool_result_name(call.name, result)
                if call.name in allowed_tool_names:
                    _verify_evidence_tool_result(result)
                    evidence_tool_call_seen = True
                elif result.citations:
                    raise CopilotError("rejected tool request returned citations")
                citations.extend(result.citations)
                trace.append(
                    ToolTrace(
                        call_index=tool_calls_seen,
                        name=(
                            call.name
                            if call.name in allowed_tool_names
                            else _REJECTED_TOOL_TRACE_NAME
                        ),
                        arguments_sha256=content_hash(arguments),
                        result_sha256=content_hash(result.payload),
                        citation_count=len(result.citations),
                    )
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.call_id or f"call-{tool_calls_seen}",
                        "content": result.as_provider_content(),
                    }
                )

        raise CopilotError("copilot did not produce an answer within the tool-call budget")

    def _execute_call(
        self, call: ProviderToolCall
    ) -> tuple[ToolResult, dict[str, object]]:
        try:
            decoded = json.loads(call.arguments or "{}")
        except JSONDecodeError:
            decoded = {}
        if not isinstance(decoded, dict) or not all(
            isinstance(key, str) for key in decoded
        ):
            decoded = {}
        arguments = {str(key): value for key, value in decoded.items()}
        try:
            return self.toolset.execute(call.name, arguments), arguments
        except ToolInputError:
            rejected = ToolResult(
                name=call.name,
                payload={"error": "tool request rejected by the read-only contract"},
                citations=(),
            )
            return rejected, arguments


def _verify_tool_result_name(call_name: str, result: ToolResult) -> None:
    """Keep a tool result bound to the provider-requested capability."""
    if result.name != call_name:
        raise CopilotError("copilot tool result name did not match the requested tool")


def _verify_evidence_tool_result(result: ToolResult) -> None:
    """Require every evidence citation to hash the exact sanitized tool payload."""
    if not result.citations:
        raise CopilotError("copilot evidence tool result omitted citations")
    expected_hash = content_hash(result.payload)
    if any(citation.evidence_sha256 != expected_hash for citation in result.citations):
        raise CopilotError("copilot evidence citation hash did not match tool payload")


def normalize_provider_allowed_hosts(value: str | Sequence[str]) -> tuple[str, ...]:
    """Validate exact DNS hostnames used for remote provider egress."""
    raw_hosts = value.split(",") if isinstance(value, str) else list(value)
    normalized: list[str] = []
    for raw_host in raw_hosts:
        if not isinstance(raw_host, str):
            raise ValueError("copilot provider allowed hosts must be hostnames")
        host = raw_host.strip().lower()
        if (
            not host
            or len(host) > 253
            or any(character.isspace() for character in host)
            or any(marker in host for marker in ("://", "/", "@", ":", "*"))
        ):
            raise ValueError(
                "copilot provider allowed hosts must be exact hostnames without URLs, ports, or wildcards"
            )
        labels = host.split(".")
        if any(not _HOST_LABEL.fullmatch(label) for label in labels):
            raise ValueError("copilot provider allowed hosts must be valid DNS hostnames")
        if host not in normalized:
            normalized.append(host)
    if not normalized:
        raise ValueError("copilot provider allowed hosts must not be empty")
    return tuple(normalized)


def normalize_provider_endpoint(
    value: str,
    *,
    allowed_hosts: str | Sequence[str] = _DEFAULT_PROVIDER_ALLOWED_HOSTS,
) -> str:
    """Validate an endpoint without credentials, remote HTTP, or host drift."""
    parsed = urlsplit(value.strip())
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower().rstrip(".")
    loopback = host in {"localhost", "127.0.0.1", "::1"}
    if (
        not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or scheme not in {"https", "http"}
        or (scheme == "http" and not loopback)
    ):
        raise ValueError("copilot provider endpoint must be HTTPS or loopback HTTP without credentials")
    if scheme == "https" and not loopback:
        normalized_hosts = normalize_provider_allowed_hosts(allowed_hosts)
        if host not in normalized_hosts:
            raise ValueError("copilot provider endpoint host is not allowlisted")
    return urlunsplit((scheme, parsed.netloc, parsed.path, "", ""))


def _parse_provider_response(payload: object, *, fallback_model: str) -> ProviderResponse:
    if not isinstance(payload, Mapping):
        raise CopilotProviderError("copilot provider response was not an object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise CopilotProviderError("copilot provider response did not include a choice")
    message = choices[0].get("message")
    if not isinstance(message, Mapping):
        raise CopilotProviderError("copilot provider response did not include a message")
    content = message.get("content", "")
    if not isinstance(content, str):
        content = ""
    raw_tool_calls = message.get("tool_calls", [])
    if not isinstance(raw_tool_calls, list):
        raw_tool_calls = []
    calls: list[ProviderToolCall] = []
    for raw_call in raw_tool_calls:
        if not isinstance(raw_call, Mapping):
            continue
        function = raw_call.get("function")
        if not isinstance(function, Mapping):
            continue
        name = function.get("name")
        arguments = function.get("arguments", "{}")
        if not isinstance(name, str) or not name or not isinstance(arguments, str):
            continue
        call_id = raw_call.get("id")
        calls.append(
            ProviderToolCall(
                call_id=call_id if isinstance(call_id, str) else "",
                name=name,
                arguments=arguments,
            )
        )
    model = payload.get("model")
    return ProviderResponse(
        content=content,
        tool_calls=tuple(calls),
        model=model if isinstance(model, str) and model else fallback_model,
        usage=_parse_provider_usage(payload.get("usage")),
    )


def _sleep_before_retry(attempt: int, backoff_seconds: float) -> None:
    delay = min(backoff_seconds * (2 ** (attempt - 1)), _MAX_RETRY_BACKOFF_SECONDS)
    if delay > 0:
        sleep(delay)


def _parse_provider_usage(value: object) -> ProviderUsage | None:
    """Keep only bounded standard token counters from an untrusted response."""
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise CopilotProviderError("copilot provider usage was invalid")
    try:
        return ProviderUsage(
            prompt_tokens=_usage_count(value.get("prompt_tokens")),
            completion_tokens=_usage_count(value.get("completion_tokens")),
            total_tokens=_usage_count(value.get("total_tokens")),
        )
    except ValueError as error:
        raise CopilotProviderError("copilot provider usage was invalid") from error


def _usage_count(value: object) -> int | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= _MAX_PROVIDER_USAGE_TOKENS
    ):
        raise ValueError("invalid provider usage count")
    return value


def _sum_usage(usages: Sequence[ProviderUsage | None], field: str) -> int | None:
    """Sum a counter only when every provider response reported that counter."""
    values = [
        cast(int | None, getattr(usage, field)) if usage is not None else None
        for usage in usages
    ]
    if not values or any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def _serialized_context_bytes(
    messages: Sequence[dict[str, object]],
    tool_definitions: Sequence[ToolDefinition],
) -> int:
    """Measure the provider context without retaining prompts or tool output."""
    encoded = json.dumps(
        {
            "messages": list(messages),
            "tools": [tool.as_provider_schema() for tool in tool_definitions],
        },
        default=str,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return len(encoded)


def _unique_citations(citations: Sequence[Citation]) -> tuple[Citation, ...]:
    seen: set[tuple[str, str, str]] = set()
    unique = []
    for citation in citations:
        key = (citation.source, citation.label, citation.evidence_sha256)
        if key not in seen:
            seen.add(key)
            unique.append(citation)
    return tuple(unique)
