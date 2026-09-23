# Model card: Regime-Gated MoE

## Model

Three modality experts encode filing text, XBRL fundamentals, and market features. The missing-aware gate adds a learned static prior to a scaled regime-conditioned residual, allowing `gate_strength=0` to recover a static mixture and larger values to admit more regime variation. The output is a standardized 20-session abnormal-return estimate.

The walk-forward candidate set also includes a fundamental-anchored hybrid. It combines 75% of the fundamental Elastic Net prediction with a 25% MoE residual, limiting the downside from noisy modalities while retaining nonlinear text, market, and regime information.

FinBERT is frozen during filing embedding. Each configured filing section is tokenized into 510-token spans; at most 12 uniformly spaced spans are pooled so long filings retain beginning/middle/end coverage under a bounded free-compute budget. The original full-section token count remains available to the change features. The trainable model remains deliberately small to limit overfitting and to run on free GPU sessions.

The explicitly resource-constrained `config/authenticated-free.yaml` protocol lowers this cap to four uniformly spaced spans. Dataset provenance records the effective cap so results from the free and full protocols cannot be conflated.

## Objective

Huber regression loss on training-period standardized returns, with auxiliary Huber supervision for every available expert, differentiable correlation regularization, and mild gate-entropy regularization. Hyperparameters and early stopping use pre-test data only. In frozen v1, each fold early-stopped on the same validation fold that produced its out-of-fold predictions; later studies early-stop on a purged holdout from the training window ([ADR 0017](adr/0017-post-v1-selection-protocol.md)).

## Required comparisons

- Elastic Net
- Gradient-boosted tabular model
- Text-only expert
- Fundamental-only expert
- Market-only expert
- Static early-fusion network
- Equal-weight expert ensemble
- Regime-gated MoE
- Fundamental-anchored MoE

## Evaluation

Prediction: RMSE, MAE, rank information coefficient, and directional diagnostics.

Selection uses expanding folds: train through 2022 / validate 2023, then train through 2023 / validate 2024. The primary criterion maximizes the worst fold's rank IC, followed by validation-count-weighted rank IC and pooled out-of-fold RMSE. The selected hybrid recorded rank IC 0.0617 and 0.0648 in the two folds. Those figures are development evidence rather than independent confirmation: the anchored family was introduced during pre-test iteration, each fold's MoE early-stopped on that fold, and the champion was the best of 33 candidates scored on the same predictions. Part of the drop to the locked-test IC is therefore expected selection bias, not only signal decay.

The hash-frozen champion was subsequently evaluated on 1,794 events accepted from 2025 through the 2026-07-31 cutoff. Locked-test rank IC was 0.0316, RMSE was 0.0916, and MAE was 0.0658. At the base 10 bps cost assumption the portfolio returned -3.28% annualized with 5.18% volatility, a -0.63 Sharpe (95% block-bootstrap interval [-2.31, 0.94]), and a -12.05% maximum drawdown. The predictive ordering remained weakly positive, but the economic hypothesis was unsupported. A [dated companion analysis](../reports/locked_rank_ic_interval_2026-09-23.md) places a 95% two-calendar-month block-bootstrap interval of [-0.0114, 0.0702] around the locked rank IC; because it includes zero, positive ranking skill is not established by this test.

Transaction costs are charged in bps per unit of one-sided turnover, so the 10/25/50 bps scenarios are round-trip costs (half per side). With average daily one-sided turnover of 0.0689, reading them as per-side costs would lower net annual return by roughly 1.7 percentage points at 10 bps and 8.7 at 50 bps; the conclusion that the portfolio is unprofitable is unchanged. In v1, the reported Sharpe used geometric annual return over volatility while its bootstrap interval resampled an arithmetic Sharpe, so the interval is approximate for that estimator. Later studies bootstrap the same statistic ([ADR 0017](adr/0017-post-v1-selection-protocol.md)).

Portfolio: annualized return and volatility, Sharpe, Sortino, maximum drawdown, turnover, hit rate, factor exposures, Newey–West t-statistic, and block-bootstrap confidence intervals.

## Known v1 limitations

These defects were found after the locked test and are disclosed rather than corrected, because v1 is frozen ([ADR 0002](adr/0002-freeze-v1-prospective-evaluation.md)):

- **Mixed-period fundamentals.** Flow facts were chosen without regard to their reporting period. Among 10-Q events, net income was 3-month in 34% of cases and 6- or 9-month year-to-date in 63%. The fundamental expert, which supplies 75% of the champion score, kept only one input, `return_on_assets`, so part of that anchor tracks fiscal-quarter position.
- **Stale concepts.** 41% of events used a `Revenues` value more than 180 days older than the filing, often from a tag the issuer stopped using around 2018, which distorts both margin features.
- **Degenerate baselines.** Fixed-alpha Elastic Nets on raw returns kept 0 of 776 text coefficients and 3 of 807 in the all-feature model, so "Text-Only has no rank IC" reflects over-regularization rather than an absence of text signal.
- **Universe screen.** The 500-name candidate list was ranked on trailing liquidity as of 2026-07-31 (`screen-universe --as-of 2026-07-31`), the last day of the locked-test period, so universe selection used test-period information beyond the disclosed survivorship limitation.

Later studies use duration-aware, staleness-bounded fundamentals ([ADR 0015](adr/0015-xbrl-fact-selection-policy.md)) and scale-invariant baselines ([ADR 0017](adr/0017-post-v1-selection-protocol.md)). The prospective runner keeps scoring v1 with its original feature definition, and the frozen predictor refuses datasets built otherwise.

## Risks

- The gate can memorize regimes or missingness patterns.
- Text representations may learn issuer or sector identity rather than new information.
- Hyperparameter searches can overfit a small validation period.
- Architecture changes motivated by validation diagnostics can overfit the development era.
- Portfolio performance can disappear under realistic costs or borrow constraints.

The report must publish negative results and ablations. A positive Sharpe is not a release criterion.
