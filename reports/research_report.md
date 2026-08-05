# EDGAR-MoE Research Report

> Status: software implementation complete; authenticated market study pending. Do not replace placeholders until the locked-test run is finalized.

## Abstract

This study tests whether the predictive usefulness of SEC filing text, XBRL fundamentals, and market features changes with observable market regimes. It compares a regime-gated Mixture-of-Experts model against linear, tree-based, single-modality, and static-fusion baselines under chronological validation and a locked out-of-sample test.

## Hypotheses

1. Filing-change features add rank information beyond trailing market and fundamental features.
2. A learned regime gate outperforms a static equal-weight ensemble on validation data.
3. Any predictive improvement survives a neutral, overlapping-position portfolio after stated transaction and borrow costs.

## Dataset and attrition

Populate after the authenticated build:

- Retrieved filings: `[N]`
- Mapped issuers: `[N]`
- Mapping exclusions: `[N and reasons]`
- Parsing failures: `[N and reasons]`
- Label censoring: `[N and reasons]`
- Final events by split: `[development / validation / test]`

## Experimental protocol

Record the immutable dataset ID, Git commit, configuration digest, seeds, hardware, model artifact digest, and the time at which the locked test was opened.

## Results

Publish complete predictive, portfolio, ablation, cost, subperiod, sector, and bootstrap tables. Do not report only the winning configuration.

## Failure analysis

Analyze the largest positive and negative errors, parsing failures, missing modalities, regime-gate behavior, turnover concentration, and corporate-action edge cases.

## Conclusion

State whether each hypothesis was supported, unsupported, or inconclusive. Separate statistical evidence from economic interpretation and from production feasibility.
