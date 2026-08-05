# Model card: Regime-Gated MoE

## Model

Three modality experts encode filing text, XBRL fundamentals, and market features. A separate gating network observes point-in-time regime variables and missing-modality masks, then produces softmax weights across experts. The output is a standardized 20-session abnormal-return estimate.

FinBERT is frozen during filing embedding. The trainable model remains deliberately small to limit overfitting and to run on free GPU sessions.

## Objective

Huber regression loss on training-period standardized returns, with mild gate-entropy regularization to discourage collapse. Hyperparameters and early stopping use validation data only.

## Required comparisons

- Elastic Net
- Gradient-boosted tabular model
- Text-only expert
- Fundamental-only expert
- Market-only expert
- Static early-fusion network
- Equal-weight expert ensemble
- Regime-gated MoE

## Evaluation

Prediction: RMSE, MAE, rank information coefficient, and directional diagnostics.

Portfolio: annualized return and volatility, Sharpe, Sortino, maximum drawdown, turnover, hit rate, factor exposures, Newey–West t-statistic, and block-bootstrap confidence intervals.

## Risks

- The gate can memorize regimes or missingness patterns.
- Text representations may learn issuer or sector identity rather than new information.
- Hyperparameter searches can overfit a small validation period.
- Portfolio performance can disappear under realistic costs or borrow constraints.

The report must publish negative results and ablations. A positive Sharpe is not a release criterion.
