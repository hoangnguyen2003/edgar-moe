# ADR 0008: Bound copilot run-level execution time

## Status

Accepted — 2026-09-20

## Context

The operator-run copilot already bounds tool calls and retries per provider
completion. Those controls do not by themselves express a total allowance for
a multi-tool run: slow read tools, repeated provider calls, and retry backoff
could consume more time and budget than an operator intended. A provider call
that is already in flight cannot be safely interrupted by the agent loop, but
the loop can refuse to start another call once its run budget is exhausted.

## Decision

Every new `ResearchCopilot` run uses a validated aggregate wall-clock budget,
300 seconds by default and at most 900 seconds. The loop checks elapsed time
before each provider completion and raises a coarse execution-budget error
instead of starting another call after the deadline. The budget is configurable
through `RuntimeSettings`, `--max-duration-seconds`, and
`EDGAR_MOE_COPILOT_MAX_DURATION_SECONDS` for both operator copilot commands.

The private answer envelope records total local elapsed milliseconds in `usage`
and includes `max_duration_seconds` in the content-addressed agent identity.
The identity verifier accepts legacy envelopes without that optional field, so
old reports remain structurally verifiable; mixed legacy/current benchmark
identities remain review-required. Request telemetry remains bounded for the
maximum eight tool calls plus final completion and up to three retries per
completion.

## Consequences

- A long or degraded provider session cannot begin another paid request after
  the declared run budget.
- Operators can compare the declared boundary with total elapsed/request
  telemetry without retaining prompts, responses, credentials, or endpoints.
- An in-flight provider request may still finish after the budget; this policy
  is a pre-call admission control, not a hard socket cancellation guarantee.
- The frozen v1 model, prospective registry, public API, and public assets are
  unchanged. This control does not establish provider quotas or an availability
  SLO.
