# ADR 0022: Use content-addressed review profiles for the research copilot

- Status: Accepted
- Date: 2026-09-22

## Context

The operator-run research copilot already provides bounded, citation-backed
evidence navigation. A single generic instruction set is useful for factual
questions but makes the same agent less useful during a quant review, a
solution-architecture review, or an operations review. Adding independent
agents or write-capable tools would increase cost and the trust boundary without
improving the frozen research result.

## Decision

Expose four bounded instruction profiles through the existing copilot command:

- `research` (the default neutral perspective);
- `quant` for point-in-time, leakage, label maturity, cost, and uncertainty
  review;
- `architect` for system boundaries, source-of-truth choices, SLOs, failure
  modes, and cost/reliability trade-offs; and
- `operations` for scheduler, deployment, database/artifact, alert, and
  recovery review.

The profile is part of the non-secret `agent_identity` when a new answer is
generated. Its policy digest covers the profile instructions, so changing the
perspective is distinguishable from changing only the provider response. The
read-only tool definitions, tool-call budget, duration budget, context budget,
citation rules, and envelope verifier remain shared across profiles. Reports
without a profile identity remain valid as legacy reports under the original
policy digest.

Profiles are perspectives, not separate autonomous actors. They cannot write
forecasts, labels, registry rows, artifacts, deployments, or credentials, and
they cannot authorize model promotion or an investment decision.

## Alternatives considered

### Add a second public chatbot endpoint

Rejected. It would require exposing a provider credential or introducing a new
authentication and rate-limit boundary. The copilot is intentionally
operator-run and private.

### Give the model write-capable tools

Rejected. A research explanation layer should not mutate prospective evidence,
deployment state, or model identity. Human-reviewed change workflows remain the
authority for those actions.

### Run separate autonomous specialist agents

Deferred. Multiple agents could provide a later comparison experiment, but the
current need is bounded review perspective. Profiles add practical value while
preserving one auditable policy/tool boundary and a zero-recurring-service
deployment model.

## Consequences

- Architecture review questions can be run with `--profile architect` and
  produce an identity that records that perspective.
- Existing private reports and review histories remain verifiable.
- The reviewed copilot benchmark can be rerun under any profile, but results
  from different profiles must not be combined as one consistent agent
  identity.
- The feature remains provider-neutral and does not require a new SDK or
  public deployment secret.
