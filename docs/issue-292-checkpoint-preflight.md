# Historical checkpoint compatibility preflight

`scripts/audit_historical_checkpoint.py` checks whether an existing checkpoint
can be reused as the fixed historical source. It independently replays the
complete legacy identity supplement and its previous baselines before reading
the checkpoint's hash-bound requested roster. It compares literal issuer and
symbol pairs, retaining multiple share classes with one CIK. A renamed symbol
or another class of the same issuer is not silently substituted. Missing or
later-delisted source members remain in the denominator.

The preflight separately verifies the processed dataset's source-manifest
binding, dataset identity, four asset filenames and actual asset hashes. This
establishes integrity of those bytes, not historical membership or continuity.
Changed manifests, requested rows, feature bytes, duplicate JSON fields,
symlinked paths, hard-linked files and excessive inputs are refused. Only
counts, hashes, operational statuses and blockers enter the report; issuer,
symbol, identifier and provider rows stay private.

Run from an isolated checkout with the existing private inputs:

```sh
python -m scripts.audit_historical_checkpoint \
  --directory data/artifacts/private-identity-capture \
  --identity-result legacy-governance-<sha256>.json \
  --governing-sources data/artifacts/private-legacy-sources \
  --source-capture data/artifacts/private-candidate-source \
  --retained-checkpoint data/raw/authenticated/2026-07-31 \
  --processed-dataset data/processed/v2-finbert/<dataset-id> \
  --output-dir data/artifacts/historical-checkpoint-preflight
```

The new output directory and content-addressed report are owner-only and
exclusively created. For independent replay, replace `--output-dir` with
`--verify-report data/artifacts/historical-checkpoint-preflight/compatibility-<sha256>.json`.
The supplied paths must be canonical directories without aliases.

Exit **2** means a valid retained report with historical reuse **blocked**;
exit **1** means invalid or unreadable evidence. This command intentionally has
no successful-admission mode. Even identical literal rosters cannot establish
security continuity, source rights, screen selection, or a complete new study
chain. No HTTP client, provider read, model run, checkpoint write or publication
release occurs. The complete checkpoint's other raw assets are outside this
preflight; the report binds its manifest and requested-roster bytes explicitly.

The original [historical candidate protocol](issue-292-historical-candidate-source.md)
and [identity evidence](issue-292-security-identity.md) remain separate. The
frozen v1, original v2 dataset and append-only forward records are unchanged.

## Retained checkpoint result, November 14, 2026

The actual source contains 503 securities across 500 issuers; the original
checkpoint requests 500 securities across 500 issuers. Only **302 literal
issuer/symbol pairs match**, although **305 issuer CIKs overlap**. There are
**201 historical source securities absent** from the checkpoint and **198
checkpoint securities outside** the fixed source. The three additional
source share-class rows were retained, not collapsed by CIK. These are literal
comparisons, not a count of independently verified security continuities.

The original processed dataset's four assets match their manifest, and that
manifest binds to the original checkpoint. That integrity result does not
repair the different candidate cohort. The identity replay still has 488
anchors and 15 unresolved candidates. Historical continuity and a separately
identified source/selection/checkpoint/processed-dataset/study chain remain
unverified. The preflight records all these blockers and exits 2; no original
dataset is renamed or admitted as the historical cohort.

The [row-free audit](../ops/issue292/checkpoint-compatibility-audit-20261114.json)
binds the content-addressed report, exact verifier code and a private 16-file
packet, including the original processed assets and requested roster. The
preceding 21-file identity packet was rehashed separately. Both complete
preflight invocations independently replayed the identity chain and reproduced
the same report without network requests. A modified copy of the real feature
asset was refused; the original remained unchanged. Validation: 2,521 full-suite
tests passed, 36 skipped, 85.87% coverage, and 42 focused checks passed.
