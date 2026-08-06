# CV entry

## Recommended one-page version

**EDGAR-MoE — Regime-Aware Multimodal SEC Filing Alpha Research**

Python, PyTorch, FinBERT, FastAPI, React, TypeScript, CVXPY, Docker

- Built an end-to-end point-in-time equity research platform that combines SEC 10-K/10-Q text, XBRL fundamentals, market state, and macro regimes to predict 20-session abnormal returns with a missing-aware Mixture-of-Experts model.
- Collected a resumable, hash-verified authenticated checkpoint spanning 500 screened securities, 17,998 SEC filings, 681,230 daily bars, 258,367 XBRL concepts, and 11,835 point-in-time macro observations; produced 5,961 audited events across 421 issuers with 4.3M feature-availability checks.
- Engineered purged expanding-window evaluation across 33 standalone/hybrid candidates and a constrained long-short simulator; selected a 75% fundamental / 25% MoE hybrid with pre-test rank IC 0.0617 in 2023 and 0.0648 in 2024 while keeping 1,794 locked-test events unscored, and shipped a FastAPI + React research terminal with 48 backend tests, frontend tests, CI, Docker, and Vercel packaging.

## Compact two-bullet version

- Developed **EDGAR-MoE**, a point-in-time multimodal US-equity research platform combining frozen FinBERT filing features, XBRL ratios, market features, and macro regimes with a PyTorch Mixture-of-Experts model.
- Processed 17,998 filings into 5,961 audited events with 4.3M availability checks; selected a fundamental-anchored MoE with positive 2023/2024 pre-test rank IC under a sealed chronological protocol, plus a tested FastAPI + React research terminal.

## Interview framing

The core hypothesis is that the usefulness of each modality changes by regime: filing language may dominate during stress, fundamentals in slower regimes, and recent market behavior around event-driven repricing. The gate therefore observes regime variables and missingness, while each expert produces its own alpha estimate. The most important engineering decision is the point-in-time contract: every feature has an availability timestamp, training transforms are fitted inside each historical fold, and labels that have not matured by the boundary are never exposed. The first validation pass favored fundamentals and rejected the standalone MoE; that led to a deliberately shrunk 75% fundamental / 25% MoE hybrid. In expanding pre-test folds it produced rank IC 0.0617 and 0.0648. This was iterative development, so the 2025+ test remains sealed for independent confirmation.

## Accuracy note

The public demo remains a synthetic software-verification artifact. The authenticated figures above describe data scale, leakage audits, and validation—not locked-test portfolio performance or live alpha. Do not claim returns, Sharpe, or test performance unless the locked protocol is explicitly opened and the complete result is published.
