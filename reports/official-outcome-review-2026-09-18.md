# Official-outcome audit — September 18, 2026

Source: complete public `/api/v1/forward/forecasts?limit=100` export from
https://edgar-moe.vercel.app, retrieved September 18, 2026. The response contained
47 of 47 forecast records for `edgar-moe-frozen-v1`. Outcomes are the recorded
official 20-session labels; this audit does not recompute or write them.

Earliest forecast selection occurs before label availability is considered.
Removing four repeats leaves 43 unique model/filing events, of which 21 have
recorded labels and 22 remain unlabeled. Those 21 events all have fundamental
component scores, so the component comparison uses an identical sample.

| Measure | Blended model | Fundamental component | Fixed baseline |
| --- | --- | --- | --- |
| Rank IC | -0.225974 | -0.268831 | Undefined for constant scores |
| RMSE | 0.111790 | 0.111766 | Zero return: 0.112252 |
| MAE | 0.083130 | 0.083104 | Zero return: 0.083410 |
| Directional accuracy | 57.14% | 57.14% | Always negative: 66.67% |

The blended model predicts negative returns in 19/21 cases; outcomes are negative
in 14/21. The blend's ranking is less negative than the fundamental component's,
but its absolute-error metrics are marginally worse. These small differences do
not establish that either component adds reliable predictive value.

This is different from the five-session audit: zero-return RMSE is slightly worse
than the model at the official horizon. Neither result establishes a useful
trading edge. Directional accuracy must be considered against class balance, not
automatically against 50%. No transaction-cost or portfolio-return claim is made.

## Limitations and next step

- This earliest-event audit is supplementary. Published registry metrics retain
  their existing per-forecast definition, which includes repeated events.
- Unlabeled records can be pending or awaiting settlement; this audit does not
  infer that a missing label is a pipeline failure.
- The sample is small, with potentially dependent market exposures and horizons.
  No significance claim or model promotion is warranted.
- The public export has fundamental scores and expert weights, but not expert
  predictions. We do not infer neural predictions from unverified blend weights.
- Next, verify the frozen artifact's target normalization and blend metadata
  against training, then audit stored component predictions if available. Keep
  the v1 model frozen; any challenger needs a separate prospective protocol.

## Reproduce

Save a complete single-model forecast export (all pages combined if necessary,
with `offset: 0` and `total` equal to the item count), then run:

```sh
uv run python -m edgar_moe.forward.audit --official-export /path/to/forecasts.json
```

The audit rejects incomplete exports, duplicate IDs, mixed models, invalid
timestamps, and non-finite evaluated values. Missing component scores are counted,
and comparisons use only rows with both predictions. It prints JSON without
accessing or changing a database. Later exports can yield different metrics as
new forecasts and labels arrive.
