# ADR 0012: Require citation closure after copilot evidence-tool use

- Status: accepted
- Date: 2026-09-20
- Deciders: project maintainer

## Context

The operator-run research copilot exposes a bounded allowlist of read-only
evidence tools. Each successful tool result is expected to carry a
content-addressed citation. Before this decision, a provider could call an
allowlisted tool and then return an answer without retaining any citation. The
answer would be labeled `uncited`, which made a broken evidence chain look like
a valid envelope and weakened the distinction between a refusal and a factual
answer.

## Decision

Fail closed when an allowlisted tool result contains no tool-generated citation,
and when the final provider answer follows allowlisted evidence use without any
retained citation. The generation boundary raises a `CopilotError`, so no
answer envelope is produced. The offline envelope verifier repeats both
invariants for saved reports. A response that only contains unknown or
write-like requests, represented by the neutral
`rejected_tool_request` trace marker, may remain explicitly `uncited` because it
obtained no evidence.

## Consequences

- Evidence-backed tool results and answers cannot silently lose their provenance
  before review.
- Provider refusals remain possible without fabricating a citation.
- A defective or incomplete read-only adapter fails visibly and must be fixed
  rather than being treated as a safe uncited response.
- Existing grounded reports remain valid; legacy reports are still accepted
  unless their trace claims an evidence-tool call without citations.
- This is an answer-integrity control, not a claim that citations prove the
  truth of generated prose or replace human review.

## Verification

Unit tests cover both the generation boundary and offline verification. The
normal Python lint, type, coverage, benchmark-contract, and immutable-snapshot
gates remain required. Provider-side evidence and human review for issue #38
remain separate operational requirements.
