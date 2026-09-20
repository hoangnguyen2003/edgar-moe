# ADR 0003: Compose independent readiness gates into one platform decision

- Status: accepted
- Date: 2026-09-20
- Scope: operator and architecture review metadata only

## Context

EDGAR-MoE has separate controls for the public release surface, provider-side
operations, the evidence-grounded copilot, and prospective feature drift. Each
control owns its own schema and verifier. A reviewer still needs one concise,
hash-pinned answer to “what is ready, what is pending, and what blocks reliance?”
without copying credentials, provider payloads, answer text, or source data into
a new artifact.

The frozen v1 model must remain immutable. A readiness summary must never become
an implicit model-promotion, retraining, trading, or redistribution authorization.

## Decision

Add `platform_readiness`, a thin composition boundary that runs after the four
subsystem verifiers. It retains only each control's scope, status, content hash,
and safe reason codes. The aggregate is itself content-addressed and can be
verified without reopening the source reports.

The policy is intentionally conservative:

- `ready` for every control is required for an aggregate `ready` decision;
- `blocked`, `stale`, or `missing` makes the aggregate `blocked`;
- `review_required` remains visible and produces `review_required` when no
  control blocks it;
- `v1_immutable: true` and `automatic_retraining: false` are mandatory schema
  invariants.

The aggregate is a review artifact, not a gate that changes runtime behavior.

## Options considered

1. **Keep independent reports only.** Lowest implementation cost, but every
   review must manually reconcile incompatible statuses and it is easy to miss a
   stale provider packet.
2. **Let one subsystem own all controls.** Simpler presentation, but creates a
   cross-domain coupling and encourages one verifier to become a second source
   of truth.
3. **Compose verified summaries at an explicit boundary (chosen).** Adds a
   small schema and verifier while preserving subsystem ownership, immutable
   identities, and safe failure semantics.

## Failure behavior

Missing reports are explicit blocked controls. Invalid source reports fail the
build rather than being downgraded to “missing.” A tampered aggregate, unknown
control, unsafe reason code, or inconsistent status fails verification. A valid
blocked aggregate is retained as evidence of the review state; it is not called
ready.

## Verification and cost

Unit tests cover ready, review-required, blocked, missing, tampered, and invalid
source cases. CI checks both command interfaces, lint, type checking, and the
existing coverage threshold. The implementation uses the existing Python
runtime and has no paid service or provider dependency. The operator still must
run provider-side exercises and obtain legal/human review where the source
reports require them.

## Revisit condition

Add a new control only with its own versioned schema and verifier. Revisit the
status policy if a future deployment requires staged environments or separate
release and research decisions; do not weaken the `missing`/`stale` behavior to
make a dashboard green.
