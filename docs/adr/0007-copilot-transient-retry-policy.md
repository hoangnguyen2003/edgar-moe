# ADR 0007: Bound transient retries for the copilot provider

## Status

Accepted — 2026-09-20

## Context

The operator-run research copilot calls an OpenAI-compatible endpoint from a
bounded agent loop. A provider or network can fail transiently, but retrying
without a strict budget could multiply latency and usage cost. Conversely,
retrying authentication, validation, malformed-response, or oversized-response
errors would hide configuration or contract defects. The answer envelope's
usage telemetry must also describe actual transport attempts rather than only
logical agent turns.

## Decision

The provider retries only HTTP 408, 425, 429, and 5xx responses, plus bounded
transport failures (`URLError`, timeout, or OS errors). The default is two
retries with capped exponential backoff. The maximum retry count and initial
backoff are validated, configurable through runtime settings and both copilot
commands, and never taken from an untrusted `Retry-After` header. All other
failures remain immediate coarse errors.

`ProviderResponse.request_count` records the number of network attempts for one
logical completion. `ResearchCopilot` sums those counts into the private
`usage.request_count`; elapsed duration already surrounds the full completion,
including retries. The report still excludes response bodies, headers,
credentials, and endpoint details.

## Consequences

- Short provider/network interruptions can recover without making the agent loop
  unbounded.
- Operators can audit retry-induced usage and latency without retaining secrets
  or provider payloads.
- A retryable status is not treated as evidence of a successful answer; the
  final provider response still passes the normal envelope verifier.
- The frozen v1 model, snapshot, forecast registry, and public API are
  unchanged.
- Provider-specific rate-limit semantics remain external operational concerns;
  this policy intentionally favors a small deterministic budget over adaptive
  provider headers.
