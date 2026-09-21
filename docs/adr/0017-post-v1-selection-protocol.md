# ADR 0017: Revise the model-selection protocol after v1

- Status: accepted
- Date: 2026-09-21
- Deciders: project maintainer

## Context

Three properties of the v1 selection code made pre-test evidence look stronger
or less precise than it was:

1. Each walk-forward fold early-stopped the MoE on the same validation fold
   whose predictions became the out-of-fold record used for champion
   selection, so the pre-test rank IC (0.0632) was not a clean out-of-sample
   estimate.
2. Elastic Net comparisons used a fixed `alpha=0.02` on raw 20-session returns
   (standard deviation near 0.08). The frozen artifact shows the effect: the
   Text-Only baseline kept 0 of 776 coefficients, the all-feature Elastic Net 3
   of 807, and the Fundamental-Only expert 1 of 16. The ablation "text has no
   rank IC" was a regularization artifact.
3. The reported Sharpe ratio used geometric annual return over volatility,
   while its block-bootstrap interval resampled an arithmetic mean over
   standard deviation, so the interval did not describe the point estimate.
   Sortino used the standard deviation of clipped returns instead of downside
   deviation.

## Decision

For studies after v1:

- the MoE early-stops on a purged chronological holdout: the most recent 15%
  of each fold's training window, with inner-training labels maturing before it
  begins. The scored validation fold is untouched until prediction;
- Elastic Net baselines (and the fundamental expert) are fit on a standardized
  target, and their coefficients are rescaled so predictions and persisted
  weights stay in return units;
- the bootstrap interval resamples the same statistic as the reported Sharpe
  ratio, and Sortino uses root-mean-square downside deviation.

Walk-forward selection artifacts record these revisions in
`protocol_revisions`; frozen v1 predates the field.

## Consequences

- v1's artifacts, selection, and locked result are unchanged (ADR 0002); the
  forward runner scores v1 from its stored coefficients, not a refit.
- Future pre-test metrics are less optimistic, and baselines are real
  comparisons rather than intercept-only models.
- Champion selection over many candidates on the same out-of-fold predictions
  remains a multiple-comparison risk; the locked test stays the only
  independent confirmation.

## Verification

Integration tests check that each holdout is disjoint from the scored fold,
that inner-training labels mature before it, and that selection artifacts
record the revisions. Unit tests reproduce the degenerate raw-target fit and
confirm the standardized fit keeps signal in return units. Metric tests confirm
the bootstrap interval collapses onto the reported Sharpe estimator.
