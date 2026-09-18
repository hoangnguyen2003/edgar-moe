# Frozen normalization review — September 18, 2026

The local production-pinned checkpoint passed a read-only reproduction audit.
No model was retrained and no registry or frozen artifact was modified.

## Verified evidence

- Checkpoint SHA-256 matches `config/forward.yaml`:
  `5cf77b2a8de255eb959236a48caac1f6c5d429767e250976c0c31324ef0bcd2c`.
- Locked-result content hash, selection identity, OOF archive hash, and saved
  locked-score archive hash pass the existing verification checks.
- Reconstructed split: 4,003 training events and 1,794 locked-test events.
- Training-only target mean: -0.002879797713831067.
- Training-only target standard deviation: 0.07616393268108368.
- Both statistics exactly equal the checkpoint values. Modality and regime
  imputation medians, scaler means, and scaler scales also match exactly.
- Checkpoint champion parameters match the locked-result metadata: 75%
  fundamental anchor and 25% neural prediction.
- CPU predictions reproduce the 1,794 saved scores with maximum absolute
  difference 7.49083295392694e-10 (tolerance: rtol 1e-5, atol 1e-7).

Training standardizes neural targets; inference converts neural predictions back
with `standardized_score * target_std + target_mean`. The fundamental estimator
was fitted on raw return targets, so its prediction is blended without another
target-scale conversion. A deterministic inference regression check now asserts
this behavior using known standardized neural outputs, including individual
expert predictions.

These checks found no normalization mismatch. A negative training target mean is
consistent with, but does not fully explain, the observed negative prediction
bias. This is artifact reproduction, not a new independent test, evidence of
profitability, or verification of current production feature distributions.

## Reproduce

From the repository root with the existing local frozen artifacts and research
dependencies installed:

```sh
uv run python scripts/audit_frozen_normalization.py \
  --selection data/artifacts/walk-forward/research-2026-07-31-3553e7ad78dd/walk-forward-selection.json
```

The next research step is to compare prospective feature distributions and
component outputs against training, without tuning v1 on observed forward
outcomes. A challenger should be separately specified and evaluated prospectively.
