# ADR 0005: Pin the copilot agent boundary

## Status

Accepted — 2026-09-20

## Context

The optional research copilot is an agentic workflow: a provider chooses from a
bounded read-only tool schema, receives sanitized evidence, and returns a
reviewable answer envelope. Citations and tool traces already identify the
evidence used, but they do not identify which system policy or exact tool schema
was offered to the provider. A later policy edit could therefore make two
answers difficult to compare even when their frozen snapshot identity is the
same.

## Decision

Every newly generated answer records a non-secret `agent_identity` containing:

- the stable `research-copilot-v1` policy ID;
- a SHA-256 digest of the system policy text;
- a SHA-256 digest of the exact OpenAI-compatible tool schema for that run; and
- the configured maximum tool-call budget.

The answer verifier checks the policy ID/digest, digest formats, and budget. The
tool contract is hashed rather than expanded in the report because optional
diagnostic capabilities legitimately change the allowlist. Older schema-1
reports without the optional identity remain readable for backward-compatible
review.

## Consequences

- Agent behavior can be compared and reproduced at the policy/tool-boundary
  level without retaining provider payloads, endpoints, prompts, or secrets.
- A policy change becomes visible in private evidence review and can be tied to
  a new benchmark/review cycle.
- The identity does not prove that the provider followed the policy or that
  generated prose is true; citations, structural evaluation, and human review
  remain required.
- The public API and frozen v1 model are unchanged.
