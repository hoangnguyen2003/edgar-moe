# ADR 0009: Bound cumulative copilot provider context

- Status: Accepted
- Date: 2026-09-20

## Context

The copilot already bounds tool calls, transient retries, provider response
size, and aggregate wall-clock time. Those controls do not bound the serialized
messages sent on later turns: every tool result is appended to the conversation,
so a long or unexpectedly large read-only result could consume provider context,
increase cost, or exceed a model-specific request limit.

## Decision

Every `ResearchCopilot` run has a validated UTF-8 context-size budget. The
default is 512 KiB; operators may configure 16 KiB through a hard maximum of
2 MiB with the CLI or runtime environment. Immediately before each provider
completion, the agent serializes only the current messages and tool schemas with
stable JSON separators and rejects the call if the byte count exceeds the
budget. This is fail-closed and happens before provider contact; a request that
is already in flight is not interrupted.

The content-addressed agent identity records the configured context budget when
the new field is present. The private answer usage record retains only the
numeric `peak_context_bytes`, and benchmark aggregates retain the maximum across
successful answers. Prompts, tool payloads, serialized contexts, and provider
responses are not retained in these telemetry fields. The verifier accepts the
optional field while preserving legacy schema-1 reports and earlier agent
identities without it.

## Consequences

- Growing tool histories fail before an additional provider call, reducing
  token/cost and provider-limit risk for operator runs.
- Peak bytes provide capacity evidence without storing sensitive content.
- Byte size is not a provider-specific token guarantee; token counters remain
  optional provider metadata and the provider may tokenize differently.
- The feature is limited to the private, read-only copilot boundary and does
  not change the frozen model, public snapshot, registry, or deployment assets.
