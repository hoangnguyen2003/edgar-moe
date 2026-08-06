# EDGAR-MoE Research Report

> Status: authenticated collection, frozen-FinBERT feature construction, and expanding-window model development are complete for dataset `research-2026-07-31-3553e7ad78dd`. The frozen pre-test champion is a 75% fundamental / 25% MoE hybrid; the locked test remains sealed. See `reports/walk_forward_report.md`. Do not replace the placeholders below unless the hash-confirmed locked-test protocol is explicitly opened and finalized.

## Abstract

This study tests whether the predictive usefulness of SEC filing text, XBRL fundamentals, and market features changes with observable market regimes. It compares standalone experts and fusion models with a fundamental-anchored regime-gated Mixture-of-Experts model under expanding chronological development folds and a locked out-of-sample test.

## Hypotheses

1. Filing-change features add rank information beyond trailing market and fundamental features.
2. A learned regime gate outperforms a static equal-weight ensemble on validation data.
3. Any predictive improvement survives a neutral, overlapping-position portfolio after stated transaction and borrow costs.

## Dataset and attrition

Authenticated build summary (the final locked report should reproduce these values):

- Retrieved periodic filings: `17,998`
- Included issuers: `421`
- Included audited events: `5,961`
- Text parsing failures: `663`
- Immature labels: `133`
- Final events by split: `1,647 / 2,331 / 1,794`

## Experimental protocol

The dataset ID, source-manifest hash, configuration, seed, 33-candidate comparison, fold metrics, and frozen selection SHA-256 are recorded in `reports/walk_forward_report.md`. The selected hybrid achieved pre-test rank IC 0.0617 in 2023 and 0.0648 in 2024 across 2,305 out-of-fold events. These are iterative development results, not independent confirmation. Record the Git commit, hardware, locked artifact hash, and opening timestamp only if `open-frozen-test` is explicitly run with the reviewed selection hash.

## Results

Publish complete predictive, portfolio, ablation, cost, subperiod, sector, and bootstrap tables. Do not report only the winning configuration.

## Failure analysis

Analyze the largest positive and negative errors, parsing failures, missing modalities, regime-gate behavior, turnover concentration, and corporate-action edge cases.

## Conclusion

State whether each hypothesis was supported, unsupported, or inconclusive. Separate statistical evidence from economic interpretation and from production feasibility.
