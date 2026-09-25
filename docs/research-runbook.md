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

Resume is bound to `request.json` (universe, dates, feed, forms, macro series,
batch size, and filing cap). An already finalized checkpoint is verified and
returned without rewriting its manifest; a different request is rejected.
Interrupted checkpoints reuse only assets whose adjacent `.sha256` sidecar
matches. Damaged or sidecar-less source payloads are re-fetched using the
original request, and the new file is atomically published only after the
provider response succeeds. If the provider is still unavailable, the run
remains incomplete; a failed filing download is recorded as attrition rather
than blessing an old document. Older **completed** checkpoints without
`request.json` remain verifiable against their manifest and requested universe.
An older **partial** checkpoint without a request contract is intentionally
not resumed: retain it for diagnosis and start a new checkpoint location.

To see the local I/O cost of the integrity check without using private data,
run `uv run python scripts/benchmark_refresh_resume_integrity.py --size-mib 16
--samples 5`. On the maintainer's 2026-09-24 laptop, counting 376,832 synthetic
rows in a 16.96 MB cache took a 23.045 ms median; hash verification plus the
same count took 28.698 ms (five samples). This is a synthetic local-file
microbenchmark, not a network benchmark or production SLA.

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

## Separate post-v1 duration-aware study (development only)

The v1 result above is immutable. For a new hypothesis, use a **new** checkpoint,
dataset ID, and `config/authenticated-v2.yaml`; do not rebuild or relabel the
v1 dataset. Record the duration-aware XBRL policy, revised split/early-stopping
protocol, source manifest, configuration, code commit, and any universe changes
before selecting a model. In particular, do not infer that the negative v1
locked result has been repaired by the new feature definition.

A paired **development-only** XBRL-policy reconstruction may read the same
unchanged authenticated source checkpoint as frozen v1. Build **two new**
datasets under the same pinned runtime: a legacy-policy reconstruction and a
duration-aware candidate. Never point the builder at the frozen v1 processed
directory. The legacy reconstruction is not the frozen v1 model or a rerun of
its locked test; using the old v1 feature array directly would confound XBRL
with any FinBERT runtime change. These new datasets are not new source
observations or independent tests because the v1 locked outcome is already
known. For example, after verifying the private checkpoint:

```bash
HF_HOME=data/cache/huggingface uv run edgar-moe build-dataset \
  --checkpoint data/raw/authenticated/2026-07-31 \
  --config config/authenticated-free.yaml \
  --output-dir data/processed/paired-legacy-finbert \
  --embedding-cache data/artifacts/embedding-cache-finbert \
  --embedder finbert --device cpu
HF_HOME=data/cache/huggingface uv run edgar-moe build-dataset \
  --checkpoint data/raw/authenticated/2026-07-31 \
  --config config/authenticated-v2.yaml \
  --output-dir data/processed/v2-finbert \
  --embedding-cache data/artifacts/embedding-cache-finbert \
  --embedder finbert --device cpu
```

Before interpreting any model difference, run the private, aggregate-only
input-attribution audit:

```bash
uv run python scripts/compare_xbrl_policy_inputs.py \
  --baseline-dataset data/processed/paired-legacy-finbert/<legacy-rebuild-id> \
  --candidate-dataset data/processed/v2-finbert/<v2-dataset-id> \
  --config config/authenticated-v2.yaml \
  --output data/artifacts/v2-reviews/<v2-dataset-id>/policy-delta.json
uv run python scripts/verify_xbrl_policy_inputs.py \
  --report data/artifacts/v2-reviews/<v2-dataset-id>/policy-delta.json
```

The audit checks the same source and pre-test event set, timing, targets, and
exact text/market/regime parity. It reports pre-test fundamental differences
without releasing row identifiers or values, and pins both verified processed
manifests by SHA-256. A `confounded` result means the
change cannot be attributed to XBRL alone (for example, FinBERT runtime
changes); still describe v2 as a separate study, not a clean policy ablation.
The loader reads the full target array, but the audit indexes only events whose
entry **and horizon** precede the 2025 test boundary. A genuinely new
confirmation needs a precommitted, later holdout and a
fresh checkpoint, not repeated analysis of the known v1 locked window.

After `build-dataset --config config/authenticated-v2.yaml` and
`walk-forward-study --config config/authenticated-v2.yaml` have produced a
separate v2 dataset and selection, run:

```bash
uv run edgar-moe v2-pretest-review \
  --dataset-dir data/processed/<v2-dataset-id> \
  --selection-dir data/artifacts/walk-forward/<v2-dataset-id>
```

This command verifies dataset/selection/OOF hashes and revised protocol
identities, recalculates each model's saved fold metrics, and reports paired
calendar-month-block rank-IC differences against five simpler models. Its
intervals are **conditional on the same development-fold selection**; they are
not independent significance tests. A separate pre-locked OOF portfolio check
shows 10/25/50 bps transaction-cost scenarios and short-borrow cost for the
champion and fundamental baseline only if the active security-day return panel
passes a completeness gate. Otherwise it reports an unavailable reason, not
zero-filled performance. Events extending into the locked period are excluded
from that portfolio check. The output defaults to the private ignored
`data/artifacts/v2-reviews/<dataset-id>/pretest-review.json`, is hash-pinned,
and refuses overwrite. Keep it private while checking sample sizes, calendar
coverage, uncertainty, model complexity, cost sensitivity, and data licenses.

The dataset loader reads the complete target array, but this command never
indexes, scores, or evaluates locked-period labels. It does not update the
forward registry, publish the public snapshot, or authorize a v2 résumé/alpha
claim. Record a fresh locked-test decision separately only after a reviewed protocol and
pretest report exist; never rerun or reinterpret the frozen v1 locked test.

### Reviewed aggregate publication boundary

The public Research page calls the read-only `GET /api/v1/research-evidence`
contract. It always identifies frozen v1 with its selection/snapshot hashes,
sample count, calendar-block rank-IC interval spanning zero, and negative
10 bps Sharpe. V2 is explicitly `pending_review` until an operator reviews a
**separate** pretest report. A successful pipeline run alone must not publish
v2 or change the v1 snapshot.

After checking the private report's source/data/selection identities, full
five-baseline roster, interval readiness, sample sizes, cost panel (or reason
for unavailability), data licenses, and absence of locked-test predictions,
the maintainer may stage only allowlisted aggregates:

```bash
uv run python scripts/publish_v2_research_evidence.py \
  --private-review data/artifacts/v2-reviews/<dataset-id>/pretest-review.json \
  --approval-reference review/issue-<number>
uv run python scripts/verify_research_evidence_catalog.py
```

`--approval-reference` is an auditable self-attestation, **not** an access
control or substitute for PR review. The publisher refuses a bad private
report hash, locked-test predictions, a non-duration-aware policy, or an
already-reviewed catalog; it strips row-level/private report fields. Review
the staged JSON and SHA-256 lock in a PR, run Python/frontend tests and CI,
then squash-merge. Never commit the private report, events, OOF scores,
returns, credentials, or checkpoint files. The build verifies the catalog
against its hash lock and v1 companion report; the API independently
validates the lock and frozen snapshot identity at request time and returns
503 if these drift. The UI distinguishes pending, reviewed-development, and
unavailable evidence without implying independent alpha.

## 7. Publish honestly

- Validate the snapshot and run all Python and frontend checks.
- Commit only derived public output, not raw data, embeddings, credentials, or model checkpoints.
- Deploy the snapshot-backed API/site and verify the live metadata says `authenticated_locked_test`.
- Report negative or inconclusive results as written. Do not substitute synthetic metrics into the authenticated CV bullet.

## Completion definition

The engineering implementation is complete when offline checks pass. The research project is complete only when a reviewed universe, authenticated checkpoint, point-in-time dataset, validation decision, one-time locked test, reproducible report, and deployed authenticated snapshot all exist.
