# CV entry

## Recommended one-page version

**EDGAR-MoE — Regime-Aware Multimodal SEC Filing Alpha Research**

Python, PyTorch, FinBERT, FastAPI, React, TypeScript, CVXPY, Docker

- Built an end-to-end point-in-time equity research platform that combines SEC 10-K/10-Q text, XBRL fundamentals, market state, and macro regimes to predict 20-session abnormal returns with a missing-aware Mixture-of-Experts model.
- Collected a resumable, hash-verified authenticated checkpoint spanning 500 screened securities, 17,998 SEC filings, 681,230 daily bars, 258,367 XBRL concepts, and 11,835 point-in-time macro observations; produced 5,961 audited events across 421 issuers with 4.3M feature-availability checks.
- Engineered purged expanding-window evaluation across 33 standalone/hybrid candidates and a constrained long-short simulator; froze a 75% fundamental / 25% MoE hybrid at 0.0632 out-of-fold rank IC, then reported 0.0316 rank IC on 1,794 held-out events and a negative -0.63 net Sharpe instead of suppressing the failed trading hypothesis.
- Shipped the authenticated, content-hashed result through a tested FastAPI + React research terminal with CI, Docker, and Vercel packaging.

## Compact two-bullet version

- Developed **EDGAR-MoE**, a point-in-time multimodal US-equity research platform combining frozen FinBERT filing features, XBRL ratios, market features, and macro regimes with a PyTorch Mixture-of-Experts model.
- Processed 17,998 filings into 5,961 audited events with 4.3M availability checks; froze a fundamental-anchored MoE at 0.0632 out-of-fold rank IC, measured 0.0316 on 1,794 held-out events, and published the negative cost-aware result through a tested FastAPI + React terminal.

## Interview framing

The core hypothesis is that the usefulness of each modality changes by regime: filing language may dominate during stress, fundamentals in slower regimes, and recent market behavior around event-driven repricing. The gate therefore observes regime variables and missingness, while each expert produces its own alpha estimate. The most important engineering decision is the point-in-time contract: every feature has an availability timestamp, training transforms are fitted inside each historical fold, and labels that have not matured by the boundary are never exposed. The first validation pass favored fundamentals and rejected the standalone MoE; that led to a deliberately shrunk 75% fundamental / 25% MoE hybrid. It produced rank IC 0.0617 and 0.0648 in the two pre-test folds, then 0.0316 in the frozen 2025–2026 test. The ranking signal remained positive, but the 10 bps portfolio lost 3.28% annualized with a -0.63 Sharpe, so the economically tradable-alpha hypothesis was rejected.

## Accuracy note

The public snapshot is an authenticated historical research artifact, not live alpha or investment advice. State both the positive locked-test rank IC and the negative cost-aware portfolio result; do not describe the strategy as profitable. The synthetic generator is only for software verification.
