# CV entry

## Recommended one-page version

**EDGAR-MoE — Regime-Aware Multimodal SEC Filing Alpha Research**

Python, PyTorch, FastAPI, React, TypeScript, DuckDB, CVXPY, Docker

- Built an end-to-end point-in-time equity research platform that combines SEC 10-K/10-Q text, XBRL fundamentals, market state, and macro regimes to predict 20-session abnormal returns with a missing-aware Mixture-of-Experts model.
- Engineered chronological/embargoed evaluation, fold-only preprocessing, model baselines, and a daily long-short optimizer with gross, net, beta, industry, position, transaction-cost, and borrow-cost constraints; shipped the results through a versioned FastAPI contract and React research terminal.
- Added deterministic synthetic verification over 9,976 filing events, authenticated source refresh with hash manifests, automated data-contract checks, a 30-test Python suite with 82% coverage, frontend tests, Docker/Vercel packaging, CI, and scheduled research refreshes.

## Compact two-bullet version

- Developed **EDGAR-MoE**, a point-in-time multimodal US-equity research platform using PyTorch Mixture-of-Experts to adaptively fuse SEC filing text, XBRL ratios, market features, and macro regimes for 20-session alpha scoring.
- Implemented leakage audits, temporal evaluation, constrained long-short backtesting with cost/borrow stress tests, and a production-style FastAPI + React terminal; verified the complete path on 9,976 deterministic synthetic filing events with automated tests and CI.

## Interview framing

The core hypothesis is that the usefulness of each modality changes by regime: filing language may dominate during stress, fundamentals in slower regimes, and recent market behavior around event-driven repricing. The gate therefore observes regime variables and missingness, while each expert produces its own alpha estimate. The most important engineering decision is the point-in-time contract: every feature has an availability timestamp, training transforms are fitted inside each historical fold, and labels that have not matured by the snapshot date are never exposed.

## Accuracy note

The bundled performance numbers are synthetic software-verification results, not evidence of live market alpha. Keep that distinction in the CV and demo. Replace the synthetic-event bullet with authenticated real-data results only after completing the mapping audit and freezing a genuine out-of-sample experiment.
