# Frozen inference runtime

This directory contains the minimum reviewed, derived artifacts required to run the
already-selected model on a clean scheduled runner. It does not contain raw SEC,
Alpaca, FRED, or processed training data.

- `frozen-model.pt` is the inference-only model produced by the immutable locked run.
- `locked-test.json` binds the model, training dataset identity, selection hash, and
  locked-test hash.
- `SHA256SUMS` is checked before either file is copied to the ignored runtime path
  referenced by `config/forward.yaml`.

The model is never retrained by the scheduled workflow. Replacing either artifact
requires a new reviewed model ID and prospective protocol.
