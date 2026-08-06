# EDGAR-MoE Research Report

> Status: complete authenticated study for dataset `research-2026-07-31-3553e7ad78dd`. The frozen locked-test outcome is published in full; it is weakly positive predictively and negative economically.

## Abstract

This study tests whether SEC filing text, point-in-time XBRL fundamentals, and market features have regime-dependent value for predicting 20-session beta-adjusted returns. A 75% fundamental / 25% regime-gated Mixture-of-Experts hybrid was selected across expanding 2023 and 2024 folds, frozen by content hash, and evaluated on 1,794 untouched 2025–2026 events. Rank IC fell from 0.0632 out of fold to 0.0316 in the locked test. The constrained portfolio lost 3.28% annualized at 10 bps with a -0.63 Sharpe, so the tradable-alpha hypothesis was not supported.

## Hypotheses and verdicts

1. **Filing-change features add rank information beyond market and fundamental features — inconclusive.** The frozen hybrid exceeded the fundamental-only component on locked rank IC (0.0316 versus 0.0215), but the market-only expert reached 0.0830 and the design does not isolate text's marginal effect cleanly.
2. **A learned regime gate outperforms static fusion — unsupported in the locked test.** The equal-weight ensemble reached 0.0658 locked rank IC versus 0.0316 for the selected hybrid.
3. **Predictive improvement survives portfolio constraints and costs — unsupported.** Every tested cost scenario produced a negative annualized return and Sharpe.

## Dataset and attrition

- Retrieved periodic filings: `17,998`
- Included issuers: `421`
- Included audited events: `5,961`
- Text parsing failures: `663`
- Immature labels: `133`
- Walk-forward out-of-fold events: `2,305`
- Final pre-test training events: `4,003`
- Locked-test events: `1,794`
- Feature-availability checks: approximately `4.3 million`

The universe is a prior-month top-300 liquid subset of 500 screened US operating-company candidates. All features carry an availability timestamp; the build rejects any feature observed after its event cutoff. Labels must mature before each fold boundary.

## Frozen protocol

- Selection: maximize worst-fold rank IC, then validation-count-weighted rank IC, then minimize pooled OOF RMSE.
- Champion: `Fundamental-Anchored MoE h=64 dropout=0.10 gate=1.00 moe=0.25`.
- Blend: 75% fundamental Elastic Net prediction plus 25% MoE residual.
- Selection SHA-256: `0bce6d674607af4e6f8e0930332923d1ab8f9c5409c63634c678ad4b67f1f906`.
- OOF predictions SHA-256: `25a78eca1df14b8bf04d1863618972569e656e011fe621fc8da8ead787f71bf3`.
- Locked result SHA-256: `9caf4c4dfd12ec8d1981342cd190195e2c45db0b2f1ea751c3b0bcedf3e62987`.

The anchored family was introduced after pre-test diagnostics favored fundamentals, so its 2023/2024 results are development evidence rather than independent confirmation.

## Predictive results

| Period | Events | Rank IC | RMSE | MAE |
|---|---:|---:|---:|---:|
| 2023 fold | — | 0.0617 | — | — |
| 2024 fold | — | 0.0648 | — | — |
| Pooled pre-test OOF | 2,305 | 0.0632 | 0.0760 | 0.0539 |
| Locked 2025–2026 | 1,794 | 0.0316 | 0.0916 | 0.0658 |

The ranking relationship remained positive but weakened by about half out of sample, while prediction error increased.

## Locked-test component comparison

| Component | RMSE | MAE | Rank IC |
|---|---:|---:|---:|
| Frozen selected hybrid | 0.0916 | 0.0658 | 0.0316 |
| Fundamental-only expert | 0.0917 | 0.0658 | 0.0215 |
| Market-only expert | 0.0916 | 0.0658 | 0.0830 |
| Equal-weight expert ensemble | 0.0917 | 0.0658 | 0.0658 |
| Frozen MoE component | 0.0918 | 0.0660 | 0.0268 |
| Gradient-boosted tabular | 0.0966 | 0.0703 | -0.0611 |

The full component table, including the constant text-only rank result, is retained in `reports/authenticated_research_report.md`.

## Cost-aware locked portfolio

| Cost | Annual return | Volatility | Sharpe | 95% bootstrap CI | Max drawdown | Turnover |
|---:|---:|---:|---:|---:|---:|---:|
| 10 bps | -3.28% | 5.18% | -0.63 | [-2.31, 0.94] | -12.05% | 6.89% |
| 25 bps | -5.77% | 5.18% | -1.11 | [-2.82, 0.45] | -14.00% | 6.89% |
| 50 bps | -9.77% | 5.20% | -1.88 | [-3.72, -0.36] | -17.66% | 6.89% |

The base-cost confidence interval includes zero, while the 50 bps interval is entirely negative. These results do not support deployment as a trading strategy.

## Opening audit

Attempt 1 reached in-memory scoring but failed before any locked artifact, report, or snapshot was persisted because timezone-aware event dates were compared with timezone-naive return dates. The backtest boundary was normalized, covered by a regression test, and committed without changing data, selection, features, hyperparameters, or predictions. Attempt 2 reran the identical hash-confirmed selection solely to complete persistence. This disclosure is included in the immutable locked-result hash and public snapshot.

## Limitations

- Free IEX bars cover one venue rather than the consolidated SIP tape.
- Historical identifier mapping, delistings, borrow availability, and realized borrow fees remain imperfect.
- Adjusted daily bars cannot reproduce intraday execution or slippage.
- The anchored family reflects pre-test iteration and its development metrics should not be treated as independent evidence.
- A single locked period cannot establish future alpha; no live trading claim is made.

## Conclusion

The project demonstrates an auditable point-in-time research system, deterministic model selection, leakage controls, immutable evaluation, and honest negative-result reporting. It found a small positive held-out rank relationship, but the chosen model did not beat the best locked component and did not produce profitable net portfolio performance. The appropriate conclusion is research and engineering success, not a validated trading strategy.
