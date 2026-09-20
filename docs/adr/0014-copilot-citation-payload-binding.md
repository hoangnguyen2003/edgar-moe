# ADR 0014: Bind copilot citations to tool payloads

- Status: accepted
- Date: 2026-09-20
- Deciders: project maintainer

## Context

The operator-run copilot already limits tools to read-only capabilities, hashes
their results, requires citation closure, and verifies the saved answer
envelope. A malformed or compromised tool adapter could nevertheless return a
valid-looking citation whose digest described a different payload. The answer
would then appear grounded even though its retained provenance did not identify
the data sent to the provider.

## Decision

At the live agent boundary, require every allowlisted tool result to satisfy
both bindings below before its content is appended to the provider context:

1. the result name must exactly match the requested tool name; and
2. every citation digest must equal the canonical SHA-256 content hash of the
   sanitized result payload.

Allowlisted results without citations, rejected/unknown tool results with
citations, mismatched names, and mismatched payload hashes fail closed. The
existing answer-envelope verifier continues to enforce citation presence,
source safety, and final citation closure for saved reports.

## Consequences

- A grounded answer cannot rely on a citation that points at a different tool
  payload or capability.
- Custom test doubles and future adapters must produce citations from the same
  canonical payload they return.
- The provider receives only a result that has passed the local provenance
  binding; no new secret, network, or public-API surface is introduced.
- The binding does not prove that an LLM statement is true; it proves only that
  retained provenance identifies the exact evidence payload supplied to it.

## Verification

Unit tests cover missing citations, tampered payload digests, mismatched result
names, and rejected tool calls. The full lint, type, coverage, immutable
snapshot, web, container, database, and Go CI gates remain required. This
control complements the provider redirect, exact-host, and citation-closure
decisions; it does not change the frozen v1 model or authorize copilot outputs
to affect forecasts.
