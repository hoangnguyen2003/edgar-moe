# EDGAR-MoE Walk-Forward Selection Report

> Status: pre-test expanding-window model selection complete; the 2025+ locked test was not transformed, predicted, or evaluated.

## Reproducibility

- Dataset ID: `research-2026-07-31-3553e7ad78dd`
- Run ID: `research-2026-07-31-3553e7ad78dd-walk-forward-5f8671b78d3e`
- Selection SHA-256: `0bce6d674607af4e6f8e0930332923d1ab8f9c5409c63634c678ad4b67f1f906`
- Source-manifest SHA-256: `5d5aa4c9cb67975cad21bdd0a9caf5763ac81b0c31a019ca6edd9f576c8b9603`
- Random seed: `42`
- Text encoder: `finbert:model=ProsusAI/finbert:revision=4556d13015211d73dccd3fdd39d39232506f3e43:chunk_tokens=510:pooling=weighted-cls:max_chunks=4:sampling=uniform-v1:sentiment=negative-neutral-positive`
- Selection rule: maximize worst-fold rank IC; then validation-count-weighted mean fold rank IC; then minimize pooled out-of-fold RMSE.

## Expanding-window folds

| Fold | Training events | Validation events | Training-label cutoff |
|---|---:|---:|---|
| 2023 | 1,647 | 1,152 | 2023-01-01T00:00:00+00:00 |
| 2024 | 2,824 | 1,153 | 2024-01-01T00:00:00+00:00 |

## Out-of-fold model comparison

| Model | Family | 2023 rank IC | 2024 rank IC | Worst-fold rank IC | Weighted rank IC | Pooled OOF RMSE |
|---|---|---:|---:|---:|---:|---:|
| Shrinkage-Gated MoE h=32 dropout=0.10 gate=0.00 | multimodal | -0.0479 | 0.0826 | -0.0479 | 0.0173 | 0.0762 |
| Shrinkage-Gated MoE h=32 dropout=0.10 gate=0.25 | multimodal | -0.0424 | 0.0783 | -0.0424 | 0.0179 | 0.0762 |
| Shrinkage-Gated MoE h=32 dropout=0.10 gate=1.00 | multimodal | -0.0246 | 0.0693 | -0.0246 | 0.0224 | 0.0760 |
| Shrinkage-Gated MoE h=32 dropout=0.20 gate=0.00 | multimodal | -0.0622 | 0.0728 | -0.0622 | 0.0053 | 0.0762 |
| Shrinkage-Gated MoE h=32 dropout=0.20 gate=0.25 | multimodal | -0.0579 | 0.0712 | -0.0579 | 0.0067 | 0.0762 |
| Shrinkage-Gated MoE h=32 dropout=0.20 gate=1.00 | multimodal | -0.0402 | 0.0654 | -0.0402 | 0.0126 | 0.0760 |
| Shrinkage-Gated MoE h=64 dropout=0.10 gate=0.00 | multimodal | -0.0291 | 0.0995 | -0.0291 | 0.0353 | 0.0759 |
| Shrinkage-Gated MoE h=64 dropout=0.10 gate=0.25 | multimodal | -0.0269 | 0.0996 | -0.0269 | 0.0364 | 0.0759 |
| Shrinkage-Gated MoE h=64 dropout=0.10 gate=1.00 | multimodal | -0.0192 | 0.1006 | -0.0192 | 0.0407 | 0.0759 |
| Shrinkage-Gated MoE h=64 dropout=0.20 gate=0.00 | multimodal | -0.0302 | 0.0989 | -0.0302 | 0.0344 | 0.0758 |
| Shrinkage-Gated MoE h=64 dropout=0.20 gate=0.25 | multimodal | -0.0280 | 0.0993 | -0.0280 | 0.0357 | 0.0758 |
| Shrinkage-Gated MoE h=64 dropout=0.20 gate=1.00 | multimodal | -0.0192 | 0.0974 | -0.0192 | 0.0391 | 0.0758 |
| Shrinkage-Gated MoE h=64 dropout=0.15 gate=0.25 | multimodal | -0.0292 | 0.0957 | -0.0292 | 0.0333 | 0.0759 |
| Elastic Net | linear | -0.1408 | -0.0204 | -0.1408 | -0.0806 | 0.0761 |
| Gradient-Boosted Tabular | tree | -0.0466 | -0.0346 | -0.0466 | -0.0406 | 0.0781 |
| Text-Only Expert | single_modality | -0.0515 | -0.0253 | -0.0515 | -0.0384 | 0.0762 |
| Fundamental-Only Expert | single_modality | 0.1373 | 0.0252 | 0.0252 | 0.0812 | 0.0761 |
| Market-Only Expert | single_modality | -0.0809 | -0.1079 | -0.1079 | -0.0944 | 0.0763 |
| Static Early-Fusion MLP | static_multimodal | -0.0033 | -0.0022 | -0.0033 | -0.0027 | 0.2132 |
| Equal-Weight Expert Ensemble | static_multimodal | -0.0461 | -0.0707 | -0.0707 | -0.0584 | 0.0762 |
| Fundamental-Anchored MoE h=32 dropout=0.10 gate=0.00 moe=0.25 | anchored_multimodal | 0.0242 | 0.0516 | 0.0242 | 0.0379 | 0.0760 |
| Fundamental-Anchored MoE h=32 dropout=0.10 gate=0.25 moe=0.25 | anchored_multimodal | 0.0284 | 0.0487 | 0.0284 | 0.0386 | 0.0760 |
| Fundamental-Anchored MoE h=32 dropout=0.10 gate=1.00 moe=0.25 | anchored_multimodal | 0.0407 | 0.0385 | 0.0385 | 0.0396 | 0.0760 |
| Fundamental-Anchored MoE h=32 dropout=0.20 gate=0.00 moe=0.25 | anchored_multimodal | 0.0192 | 0.0464 | 0.0192 | 0.0328 | 0.0760 |
| Fundamental-Anchored MoE h=32 dropout=0.20 gate=0.25 moe=0.25 | anchored_multimodal | 0.0229 | 0.0459 | 0.0229 | 0.0344 | 0.0760 |
| Fundamental-Anchored MoE h=32 dropout=0.20 gate=1.00 moe=0.25 | anchored_multimodal | 0.0344 | 0.0347 | 0.0344 | 0.0345 | 0.0760 |
| Fundamental-Anchored MoE h=64 dropout=0.10 gate=0.00 moe=0.25 | anchored_multimodal | 0.0527 | 0.0633 | 0.0527 | 0.0580 | 0.0760 |
| Fundamental-Anchored MoE h=64 dropout=0.10 gate=0.25 moe=0.25 | anchored_multimodal | 0.0555 | 0.0637 | 0.0555 | 0.0596 | 0.0760 |
| Fundamental-Anchored MoE h=64 dropout=0.10 gate=1.00 moe=0.25 | anchored_multimodal | 0.0617 | 0.0648 | 0.0617 | 0.0632 | 0.0760 |
| Fundamental-Anchored MoE h=64 dropout=0.20 gate=0.00 moe=0.25 | anchored_multimodal | 0.0545 | 0.0585 | 0.0545 | 0.0565 | 0.0759 |
| Fundamental-Anchored MoE h=64 dropout=0.20 gate=0.25 moe=0.25 | anchored_multimodal | 0.0571 | 0.0590 | 0.0571 | 0.0580 | 0.0759 |
| Fundamental-Anchored MoE h=64 dropout=0.20 gate=1.00 moe=0.25 | anchored_multimodal | 0.0640 | 0.0600 | 0.0600 | 0.0620 | 0.0760 |
| Fundamental-Anchored MoE h=64 dropout=0.15 gate=0.25 moe=0.25 | anchored_multimodal | 0.0549 | 0.0591 | 0.0549 | 0.0570 | 0.0760 |

## Frozen decision

**Fundamental-Anchored MoE h=64 dropout=0.10 gate=1.00 moe=0.25** is the pre-test champion. Its worst-fold rank IC is 0.0617, weighted rank IC is 0.0632, and pooled OOF RMSE is 0.0760.

The locked period begins 2025-01-01. Locked-test prediction count: **0**. Opening that period requires a separate explicit action after this selection artifact is reviewed and frozen.

## Interpretation

- Each fold refits imputation, scaling, and every model using prior data only.
- Fold labels must mature before the next boundary, providing an event-horizon purge.
- The robust objective rewards models whose rank relationship survives both years.
- Weighted rank IC averages fold ICs by validation count; RMSE pools raw OOF errors.
- The anchored family was introduced after initial pre-test diagnostics favored fundamentals; this is iterative development, not independent confirmation.
- These are development results, not locked-test results or evidence of live alpha.
