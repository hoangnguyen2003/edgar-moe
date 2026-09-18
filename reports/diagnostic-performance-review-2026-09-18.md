# Five-session diagnostic performance review

Source: user-supplied `diagnostic-2026-09-18.json`, generated at
2026-09-18 09:08:40 UTC. This is an exploratory audit of the frozen v1 model,
not its official 20-session evaluation or a model promotion decision.

## Sample and results

All 47 forecasts match their data. Earliest-per-model/filing selection removes
four repeated forecasts, leaving 43 events: 42 matured and one pending. Every
comparison below uses the same 42 matured events, without selecting by outcome.

| Measure | Model | Fixed baseline |
| --- | --- | --- |
| RMSE (return units) | 0.065391 | Zero return: 0.064661 |
| MAE (return units) | 0.050787 | Zero return: 0.050177 |
| Directional accuracy | 42.86% | Always negative or always nonnegative: 50.00% |
| Rank IC | -0.228750 | Constant predictions have undefined rank IC |

37 predictions are negative and five positive, whereas observed outcomes split
21 negative and 21 positive. There are no zero predictions or outcomes.
This establishes a negative prediction bias in this sample, not its cause.

Removing any single observation leaves rank IC negative, ranging from -0.298258
to -0.173171. Thus no single observation alone explains the negative pooled IC.
This range is a sensitivity check, **not a confidence interval** or significance
test. Shared market exposure and overlapping horizons limit independence.

Predictions have population standard deviation 0.004050 versus 0.064660 for
five-session outcomes. That difference alone does not prove a scaling bug:
conditional expected returns can have much less dispersion than realized returns,
and these predictions were trained for a different horizon.

## Interpretation and next investigation

The data matching problem is resolved in this artifact; predictive performance
is a separate issue. The model does not beat these fixed baselines on this
short-horizon sample. Do not negate its scores, refit on these outcomes, or select
new weights using this already-observed prospective sample and call it untouched
validation. Preserve the frozen model and official ledger.

Next, audit official 20-session outcomes using the same earliest-event policy and
fixed baselines. Then compare the frozen fundamental anchor and neural component
on identical events, checking checkpoint target normalization against training.
The diagnostic JSON lacks component scores and training calibration statistics,
so it cannot attribute weakness to either component or establish a scaling error.
Any challenger should have a separately recorded research protocol and a fresh
future evaluation period.

## Reproduce the baseline audit

```sh
uv run python -m edgar_moe.forward.audit /path/to/diagnostic-2026-09-18.json
```

The command reads a local artifact and prints JSON. It does not access the
database, modify forecasts, or train a model. It rejects duplicate model/event
rows, non-finite values, and inconsistent matured counts. Coverage in the nested
model metrics describes the evaluated sample, not overall registry coverage.
