# EDGAR-MoE Validation Report

> Status: development and validation complete; the locked test was not evaluated.

> Historical diagnostic: this single-window result is superseded for selection by `reports/walk_forward_report.md`.

## Reproducibility

- Dataset ID: `research-2026-07-31-3553e7ad78dd`
- Run ID: `research-2026-07-31-3553e7ad78dd-64-0.15-42`
- Source-manifest SHA-256: `5d5aa4c9cb67975cad21bdd0a9caf5763ac81b0c31a019ca6edd9f576c8b9603`
- Random seed: `42`
- Text encoder: `finbert:model=ProsusAI/finbert:revision=4556d13015211d73dccd3fdd39d39232506f3e43:chunk_tokens=510:pooling=weighted-cls:max_chunks=4:sampling=uniform-v1:sentiment=negative-neutral-positive`

## Dataset and temporal split

- Audited filing events: 5,961
- Development events: 1,647
- Validation events: 2,331
- Sealed locked-test events: 1,794
- Text parsing failures: 663

## Validation results

| Model | Family | RMSE | MAE | Rank IC | 2023 rank IC | 2024 rank IC |
|---|---|---:|---:|---:|---:|---:|
| Regime-Gated MoE h=64 dropout=0.15 | multimodal | 0.0758 | 0.0538 | 0.0160 | -0.0263 | 0.0557 |
| Elastic Net | linear | 0.0759 | 0.0536 | -0.0260 | -0.1406 | 0.1667 |
| Gradient-Boosted Tabular | tree | 0.0782 | 0.0559 | -0.0382 | -0.0546 | 0.0230 |
| Text-Only Expert | single_modality | 0.0762 | 0.0541 | -0.0204 | -0.0497 | 0.0079 |
| Fundamental-Only Expert | single_modality | 0.0759 | 0.0539 | 0.0997 | 0.1282 | 0.0728 |
| Market-Only Expert | single_modality | 0.0760 | 0.0539 | 0.0391 | -0.0795 | 0.1330 |
| Static Early-Fusion MLP | static_multimodal | 0.2333 | 0.1776 | -0.0046 | -0.0114 | 0.0028 |
| Equal-Weight Expert Ensemble | static_multimodal | 0.0760 | 0.0539 | 0.0549 | -0.0468 | 0.1398 |

## Decision

The configured MoE selected by validation RMSE was **Regime-Gated MoE h=64 dropout=0.15** with rank IC 0.0160. The strongest validation rank result was **Fundamental-Only Expert** at 0.0997.

Because the proposed MoE did not outperform the strongest preregistered baseline on validation rank IC, the locked test remains sealed and no authenticated market-performance snapshot is published.

## Interpretation

- The pipeline and leakage controls passed on authenticated data.
- Validation evidence does not support the claim that regime gating improves ranks.
- These are model-development results, not test results or evidence of live alpha.
