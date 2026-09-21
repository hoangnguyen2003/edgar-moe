# ADR 0015: Version the XBRL fact-selection policy

- Status: accepted
- Date: 2026-09-21
- Deciders: project maintainer

## Context

A review of the frozen v1 dataset (`research-2026-07-31-3553e7ad78dd`) found two
defects in how SEC companyfacts become fundamental inputs:

1. Flow concepts ignored the reporting period. Among 10-Q events, the selected
   `NetIncomeLoss` covered 3 months in 34% of cases, 6 months year-to-date in
   31%, and 9 months year-to-date in 32%, so `return_on_assets` scaled roughly
   1x/2x/3x with fiscal quarter. The frozen fundamental expert kept exactly one
   of 16 inputs, `return_on_assets`, and supplies 75% of the champion score.
2. Nothing bounded a fact's age. The contract-revenue fallback fired only when
   `Revenues` was absent entirely, so 41.4% of events used a `Revenues` value
   more than 180 days older than the filing's own period (for example, 2018
   revenue for Amphenol's 2026-Q2 10-Q, and 2012 revenue for Trane
   Technologies').

ADR 0002 freezes v1: its model, selection, and locked result cannot change, and
the prospective evaluation must score v1 with the features it was trained on.

## Decision

Make fact selection an explicit, versioned setting,
`features.xbrl_fact_policy`:

- `legacy_v1` reproduces the frozen selection exactly. Rebuilding the frozen
  study's 5,961 x 16 fundamental matrix through the refactored code matches the
  stored array bit for bit.
- `duration_aware_v2` is the default for new studies. Facts must be available by
  the cutoff and end within 120 days of the filing's period. Flows need a 60-380
  day reporting period; the latest period end wins, the quarter is preferred
  over year-to-date, and values are annualized. `Revenues` and
  `RevenueFromContractWithCustomerExcludingAssessedTax` compete as one input.

`config/authenticated-free.yaml`, the v1 protocol that the prospective runner
uses, pins `legacy_v1`; `config/authenticated-v2.yaml` is the free-tier protocol
for later studies. Every new dataset records its policy in both its immutable
identity and its provenance; datasets without the field are `legacy_v1`.

Mixing policies fails closed. New frozen artifacts record their training
policy, and artifacts without it (including v1) are `legacy_v1`. The frozen
predictor refuses to score a dataset built under a different policy, and
research-drift reports refuse to compare datasets across policies.

## Consequences

- v1 forecasts keep their exact feature definition; its reported results stand,
  now with the defects disclosed in the model card and README.
- New studies get comparable, current fundamentals. On the frozen study's own
  inputs, median |ROA| by selected period is about 0.070/0.078/0.082 instead of
  0.017/0.036/0.050, while net-margin coverage falls from 5,721 to 5,109 events
  because stale revenue is no longer used; missingness indicators capture that.
- A v2 model needs a new reviewed model ID and prospective protocol (ADR 0002).

## Verification

Unit tests cover quarter-vs-year-to-date selection, annualization, stale and
discontinued concepts, alias precedence, missing periods, the point-in-time
cutoff, and policy defaults. Integration tests build both policies end to end
and confirm distinct dataset identities; inference and drift tests confirm that
cross-policy use fails closed.
