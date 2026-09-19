# Authenticated research runbook

This runbook separates software verification from empirical research. Completing the code does not make the synthetic demo a market result; the final résumé claim must come from the frozen authenticated run below.

## 1. Freeze the protocol

- Keep the dates, target, modalities, candidate grid, costs, and portfolio constraints in `config/default.yaml` unchanged after reading test results.
- Record the Git commit and retain the configuration with the study artifacts.
- Use `config/universe.example.csv` only for connectivity checks.

## 2. Configure free sources

Copy `.env.example` to `.env`, then provide Alpaca paper/data credentials, a FRED API key, and a SEC user agent containing your real contact email. Do not commit `.env`.

Run `edgar-moe build-universe`. Inspect `data/interim/security-mapping-review.json`, correct or exclude questionable mappings, and keep the reviewed `config/universe.csv` with the experiment definition. Then run `edgar-moe screen-universe --as-of YYYY-MM-DD` to produce the dated free-tier candidate set. Inactive Alpaca assets are included in matching, but free sources still cannot guarantee a perfect historical identifier master; disclose this limitation.

## 3. Build immutable inputs

Run `edgar-moe refresh-data --universe config/universe.research.csv --as-of YYYY-MM-DD --config config/authenticated-free.yaml --resume`. The command can take hours for a broad universe because EDGAR requests are deliberately paced. Large SEC JSON and filing documents are gzip-compressed, and each completed issuer is resumable. It must finish with a verified manifest and zero unexplained file-hash failures.

Review:

- filing download failure count and reasons;
- number of issuers and periodic filings;
- bar coverage by symbol and year;
- corporate-action edge cases and symbol changes;
- FRED release coverage;
- source paths and SHA-256 hashes in `manifest.json`.

## 4. Construct the point-in-time dataset

Run `edgar-moe build-dataset --checkpoint <dated-checkpoint> --embedder finbert --device cpu`. The first FinBERT run downloads model weights and is compute-intensive; later runs reuse the content-addressed embedding cache. Use `--device mps` only after benchmarking it on the target macOS/PyTorch combination; `--device auto` opts into automatic MPS selection when PyTorch reports it available.

`config/authenticated-free.yaml` uses four uniformly distributed 510-token spans per long filing. The default protocol uses twelve. Keep the selected protocol fixed through model selection and locked-test evaluation.

Inspect the processed manifest and attrition counts. The build must pass its feature-availability audit. Investigate missing text, missing facts, universe exclusions, immature labels, and large issuer/industry concentration before modeling.

## 5. Select without opening the test

Run `edgar-moe walk-forward-study --dataset-dir <processed-dataset> --config config/authenticated-free.yaml`. This independently refits preprocessing and models for the 2023 and 2024 folds, writes `walk-forward-selection.json` plus hashed out-of-fold predictions, and leaves every 2025+ prediction slot empty. Compare every baseline and ablation, check seed stability, inspect gate behavior, and confirm the portfolio assumptions are still defensible.

Record the selection hash. The anchored model family was added after initial pre-test diagnostics favored the fundamental baseline, so its walk-forward figures remain development evidence; only the untouched locked period can provide independent confirmation.

Do not alter features, candidates, costs, or constraints after deciding to proceed. If the protocol changes, create a new dataset/run ID and repeat validation; do not reuse the old locked-test claim.

## 6. Open the locked period once

Do not use the earlier single-window `run-study --open-locked-test` path for the walk-forward champion: it performs a different selection procedure. After independently reviewing the walk-forward report and recording its SHA-256, run exactly once:

```bash
uv run edgar-moe open-frozen-test \
  --dataset-dir data/processed/<dataset-id> \
  --selection data/artifacts/walk-forward/<dataset-id>/walk-forward-selection.json \
  --confirm-selection-hash <reviewed-selection-sha256> \
  --report-output reports/authenticated_research_report.md \
  --publish-snapshot data/demo/snapshot.json
```

The command verifies both the selection JSON and OOF-prediction hash before it can access locked outcomes, refits only the frozen champion on all pre-2025 matured labels for a fold-derived fixed epoch count, and refuses to overwrite an existing locked artifact.
Locked evaluation publication is transactional: model, score, and locked-result
files are written under a private staging directory and the completed directory
is renamed into place only after all content hashes are recorded. An interrupted
attempt cannot publish a partial frozen dataset, and a retry refuses any final
directory that already exists.

The current authenticated run used selection SHA-256 `0bce6d674607af4e6f8e0930332923d1ab8f9c5409c63634c678ad4b67f1f906` and produced locked-result SHA-256 `9caf4c4dfd12ec8d1981342cd190195e2c45db0b2f1ea751c3b0bcedf3e62987`. Attempt 1 reached in-memory scoring but stopped before persistence when timezone-aware event dates met timezone-naive return dates. After an operational-only normalization fix was tested and committed, attempt 2 reran the identical frozen selection and completed. This recovery is recorded in the immutable artifact, final report, and public snapshot. Do not rerun this dataset; any new hypothesis or protocol requires a new dataset/run ID.

Preserve the selection hash, eventual locked-test hash, model state, processed manifest, source manifest, report, and environment/package lock together.

## 7. Publish honestly

- Validate the snapshot and run all Python and frontend checks.
- Commit only derived public output, not raw data, embeddings, credentials, or model checkpoints.
- Deploy the snapshot-backed API/site and verify the live metadata says `authenticated_locked_test`.
- Report negative or inconclusive results as written. Do not substitute synthetic metrics into the authenticated CV bullet.

## Completion definition

The engineering implementation is complete when offline checks pass. The research project is complete only when a reviewed universe, authenticated checkpoint, point-in-time dataset, validation decision, one-time locked test, reproducible report, and deployed authenticated snapshot all exist.
