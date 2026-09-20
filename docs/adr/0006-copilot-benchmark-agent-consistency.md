# ADR 0006: Gate copilot readiness on agent consistency

## Status

Accepted — 2026-09-20

## Context

Individual research-copilot answer envelopes now pin the policy, exact tool
contract, and tool-call budget used for the run. A benchmark aggregate can
contain answers from more than one run, however: it may include older schema-1
answers without an identity or answers produced after a policy/tool change.
Treating the aggregate as one reproducible evaluation in either case would
overstate what the evidence establishes.

## Decision

The offline evaluator classifies the identities of all evaluated answer
envelopes as:

- `consistent` when every answer has the same verified identity;
- `legacy` when every answer omits the optional identity (backward-compatible
  schema-1 evidence); or
- `mixed` when identity-bearing and legacy answers are combined, or when
  identity-bearing answers disagree.

Only a `consistent` aggregate retains its one non-secret `agent_identity`.
Legacy and mixed aggregates retain `null`. The benchmark/review validator
checks these fields, while old aggregates without either field remain readable
as legacy. The readiness report carries the same status and adds an explicit
agent-identity check; `legacy` and `mixed` therefore produce `review_required`
instead of `ready`, even if the structural score and human review history
otherwise pass.

## Consequences

- Readiness cannot silently combine evidence produced under different agent
  boundaries.
- Existing schema-1 answer and aggregate files remain usable for migration and
  review, but they must be rerun with a pinned identity before readiness can be
  `ready`.
- The retained aggregate contains only a verified non-secret identity or an
  explicit null; prompts, answer text, provider payloads, and credentials are
  unaffected.
- This is a reproducibility/evidence gate, not a claim that a provider follows
  the policy or that generated prose is true. Human review and statistical
  evaluation remain necessary.
- The public API, frozen v1 model, forecast registry, and deployment snapshot
  are unchanged.
