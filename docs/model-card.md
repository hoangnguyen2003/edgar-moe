# Model card: Regime-Gated MoE

## Model

Three modality experts encode filing text, XBRL fundamentals, and market features. The missing-aware gate adds a learned static prior to a scaled regime-conditioned residual, allowing `gate_strength=0` to recover a static mixture and larger values to admit more regime variation. The output is a standardized 20-session abnormal-return estimate.

The walk-forward candidate set also includes a fundamental-anchored hybrid. It combines 75% of the fundamental Elastic Net prediction with a 25% MoE residual, limiting the downside from noisy modalities while retaining nonlinear text, market, and regime information.

FinBERT is frozen during filing embedding. Each configured filing section is tokenized into 510-token spans; at most 12 uniformly spaced spans are pooled so long filings retain beginning/middle/end coverage under a bounded free-compute budget. The original full-section token count remains available to the change features. The trainable model remains deliberately small to limit overfitting and to run on free GPU sessions.

The explicitly resource-constrained `config/authenticated-free.yaml` protocol lowers this cap to four uniformly spaced spans. Dataset provenance records the effective cap so results from the free and full protocols cannot be conflated.

## Objective

Huber regression loss on training-period standardized returns, with auxiliary Huber supervision for every available expert, differentiable correlation regularization, and mild gate-entropy regularization. Hyperparameters and early stopping use pre-test validation folds only.

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

Selection uses expanding folds: train through 2022 / validate 2023, then train through 2023 / validate 2024. The primary criterion maximizes the worst fold's rank IC, followed by validation-count-weighted rank IC and pooled out-of-fold RMSE. The selected hybrid recorded rank IC 0.0617 and 0.0648 in the two folds. Because the anchored family was introduced during pre-test iteration, those figures are development evidence rather than independent confirmation; the 2025+ test remains sealed.

Portfolio: annualized return and volatility, Sharpe, Sortino, maximum drawdown, turnover, hit rate, factor exposures, Newey–West t-statistic, and block-bootstrap confidence intervals.

## Risks

- The gate can memorize regimes or missingness patterns.
- Text representations may learn issuer or sector identity rather than new information.
- Hyperparameter searches can overfit a small validation period.
- Architecture changes motivated by validation diagnostics can overfit the development era.
- Portfolio performance can disappear under realistic costs or borrow constraints.

The report must publish negative results and ablations. A positive Sharpe is not a release criterion.
