# ADR 0025: Coordinate bounded multi-profile copilot review panels

Status: Accepted

Date: 2026-09-23

## Context

The operator-run research copilot already supports bounded `research`, `quant`,
`architect`, and `operations` perspectives. A single answer can still hide a
blind spot: a quant review may miss an operational dependency, while an
architecture review may miss point-in-time or label-maturity concerns. The
project needs a useful agentic workflow without adding write authority,
unbounded delegation, or a second decision-maker that could be mistaken for a
model-promotion gate.

## Decision

Add a `research-copilot-panel` command that coordinates at most one run of each
of the four existing profiles over the same question and read-only toolset.
Profiles run sequentially through the existing provider and per-agent budgets.
Each answer is verified using the normal copilot envelope contract and is
written only to an operator-selected private directory.

The panel aggregate is a separate, content-addressed report. It retains only:

- the question hash and selected/successful/failed profile IDs;
- each private answer's hash, evidence status, citation/tool counts, and
  non-secret agent identity;
- the shared frozen-identity and tool-contract hashes when consistent; and
- coarse failure types plus bounded aggregate usage counters.

The aggregate contains no answer text, prompts, provider payloads, endpoints, or
credentials. A panel is `complete` only when every selected profile succeeds
under one frozen identity and one tool contract. Partial runs are non-zero and
`mixed` boundaries are `review_required`; neither status authorizes a forecast,
model promotion, deployment, or trade.

## Alternatives considered

### Run one general-purpose copilot

Rejected for architecture reviews because one perspective can omit a material
quantitative or operational concern.

### Add autonomous specialist agents with an LLM arbiter

Deferred. An arbiter would add another paid model boundary, create an
opportunity for unsupported synthesis, and make identity/review evidence harder
to interpret. Human comparison of independently cited envelopes is sufficient
for the current portfolio scope.

### Expose the panel through the public API

Rejected. It would require a browser/provider-key boundary, anonymous abuse
controls, and a different data-retention policy. The panel remains operator-only.

## Consequences

The project demonstrates practical agentic coordination while preserving the
same read-only trust boundary and frozen-v1 invariant. A panel can cost up to
four bounded copilot runs, so the command keeps each profile's time, context,
tool-call, retry, and provider-response limits. Reviewers can inspect child
answers privately and share the aggregate without leaking answer text.

## Verification

- `tests/unit/test_copilot_panel.py` exercises complete, partial, failed, hash-
  tampered, and invalid-profile panels.
- `research-copilot-panel --plan-only` proves the selected tool contract without
  contacting a provider.
- `verify_panel_report` checks outcome coverage, child hashes, identities,
  boundaries, disclaimers, usage bounds, and the aggregate hash.
- CI checks that the command is runnable; existing copilot envelope verification
  remains the child-answer gate.
