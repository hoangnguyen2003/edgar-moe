# EDGAR-MoE Frozen Locked-Test Report

> Frozen run: `research-2026-07-31-3553e7ad78dd-walk-forward-5f8671b78d3e-locked`.

## Reproducibility

- Dataset ID: `research-2026-07-31-3553e7ad78dd`
- Walk-forward selection SHA-256: `0bce6d674607af4e6f8e0930332923d1ab8f9c5409c63634c678ad4b67f1f906`
- Locked-result SHA-256: `9caf4c4dfd12ec8d1981342cd190195e2c45db0b2f1ea751c3b0bcedf3e62987`
- Frozen champion: Fundamental-Anchored MoE h=64 dropout=0.10 gate=1.00 moe=0.25
- Final pre-test training events: 4,003
- Locked-test events: 1,794

## Opening audit

- Completed attempt: 2
- Recovery disclosure: Attempt 1 failed after in-memory scoring and before artifact persistence because event dates were timezone-aware while return dates were timezone-naive; attempt 2 reran the identical frozen selection after date normalization.

## Predictive result

| Model | Development rank IC | Development RMSE | Test rank IC | Test RMSE | Test MAE |
|---|---:|---:|---:|---:|---:|
| Fundamental-Anchored MoE h=64 dropout=0.10 gate=1.00 moe=0.25 | 0.0632 | 0.0760 | 0.0316 | 0.0916 | 0.0658 |

## Locked-test components

| Component | RMSE | MAE | Rank IC |
|---|---:|---:|---:|
| Elastic Net | 0.0920 | 0.0662 | -0.0238 |
| Gradient-Boosted Tabular | 0.0966 | 0.0703 | -0.0611 |
| Text-Only Expert | 0.0917 | 0.0658 | — |
| Fundamental-Only Expert | 0.0917 | 0.0658 | 0.0215 |
| Market-Only Expert | 0.0916 | 0.0658 | 0.0830 |
| Static Early-Fusion MLP | 0.1896 | 0.1421 | 0.0064 |
| Equal-Weight Expert Ensemble | 0.0917 | 0.0658 | 0.0658 |
| Frozen MoE Component | 0.0918 | 0.0660 | 0.0268 |

## Cost-aware portfolio results

| Cost | Annual return | Volatility | Sharpe | 95% bootstrap CI | Max drawdown | Turnover |
|---:|---:|---:|---:|---:|---:|---:|
| 10 bps | -3.28% | 5.18% | -0.6324 | [-2.3132, 0.9450] | -12.05% | 0.0689 |
| 25 bps | -5.77% | 5.18% | -1.1121 | [-2.8245, 0.4459] | -14.00% | 0.0689 |
| 50 bps | -9.77% | 5.20% | -1.8802 | [-3.7190, -0.3565] | -17.66% | 0.0689 |

## Largest locked-test errors

| Ticker | Filing | Accepted | Prediction | Realized | Absolute error |
|---|---|---|---:|---:|---:|
| CIEN | 10-Q | 2026-03-05 | -0.0039 | 0.5788 | 0.5827 |
| AMAT | 10-Q | 2026-05-21 | -0.0001 | 0.4844 | 0.4845 |
| ALAB | 10-Q | 2026-05-05 | -0.0016 | 0.4744 | 0.4760 |
| LITE | 10-Q | 2026-02-03 | -0.0012 | 0.4546 | 0.4558 |
| COHR | 10-Q | 2026-02-04 | -0.0049 | 0.3928 | 0.3977 |
| F | 10-Q | 2026-04-29 | -0.0035 | 0.3879 | 0.3914 |
| MRVL | 10-Q | 2026-05-28 | -0.0034 | 0.3811 | 0.3844 |
| MU | 10-Q | 2025-12-17 | -0.0037 | 0.3673 | 0.3710 |
| UNH | 10-Q | 2025-08-11 | -0.0009 | 0.3646 | 0.3655 |
| LITE | 10-Q | 2025-11-04 | -0.0042 | 0.3519 | 0.3560 |

## Interpretation

This is the only completed locked evaluation tied to the recorded selection hash. It must be reported whether positive, negative, or inconclusive.
