# ADR 0028: Record forward-history reviews separately from diagnostic evidence

- Status: accepted
- Date: 2026-09-24
- Deciders: project maintainer

## Context

Forward diagnostic histories are content-addressed, research-only summaries.
Their `ready` status means only that collection and report checks passed;
snapshot independence is not assessed, and the history is not the official
20-session evaluation. The history v3 contract therefore keeps
`human_review_status` at `not_recorded`. Review evidence should not mutate that
immutable source or make the collection status look like a human approval.

## Decision

- Record any later review in a separate, content-addressed artifact bound to the
  source history's SHA-256 digest and a minimal validated summary.
- Require an explicit self-attestation that the summary counts, maturity,
  coverage, and metrics were reviewed, as well as the short-horizon,
  independence, frozen-v1, and no-promotion/no-retraining boundaries.
- Use only the decisions `acknowledged` and `follow_up_required`; a non-ready
  history cannot be acknowledged without follow-up. Follow-up uses bounded
  reason codes, not free-form notes.
- Keep the reviewer identifier self-reported. SHA-256 verifies content
  integrity but is not a signature and does not authenticate the reviewer.
- Do not retain raw observations, event/forecast IDs, source paths, URLs,
  credentials, or any LLM-generated answer in the review record.

## Alternatives considered

### Rewrite `human_review_status` inside the history

Rejected because it changes the source artifact hash and mixes collection
evidence with a later operator statement. The review must remain independently
verifiable and the original history must remain intact.

### Store free-form comments

Rejected because comments can accidentally disclose identifiers, provider
payloads, or personal information and are difficult to validate consistently.
Stable reason codes preserve useful follow-up classification without that
unbounded data surface.

### Add signed identity or provider authentication

Deferred. It would introduce key management and an identity trust root beyond
the current personal-project scope. The record explicitly avoids claiming
authenticity.

## Consequences and limits

The review record makes a reviewer decision auditable against one exact history
without converting short-horizon metrics into performance evidence. A verifier
can check the record hash alone, or optionally reopen the history to confirm
the binding. The record remains a self-attestation: a person can choose any
allowed identifier, alter the file and recompute its hash, or supply an
inaccurate local clock. Do not treat it as non-repudiation, independent
statistical validation, model promotion, or retraining approval.

## Verification

- `tests/unit/test_forward_diagnostic_history_review.py` covers source binding,
  decisions, reason codes, disclosure boundaries, tampering, non-overwrite, and
  supported history versions 1–3.
- `forward-diagnostic-history-review-verify` checks the review hash and schema;
  `--history` also checks the exact source binding.
- CI runs both CLI help contracts and the full unit/integration suite.
