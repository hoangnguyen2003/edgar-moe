# Historical checkpoint compatibility preflight

`scripts/audit_historical_checkpoint.py` checks whether an existing checkpoint
can be reused as the fixed historical source. It independently replays the
selected legacy, issuer-proxy, issuer-definition, charter-redesignation or FDIC-bank identity supplement and all
its previous baselines before reading the checkpoint's hash-bound requested
roster. It compares literal issuer and
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
  --identity-result class-redesignation-<sha256>.json \
  --governing-sources data/artifacts/private-legacy-sources \
  --proxy-sources data/artifacts/private-combined-proxy-sources \
  --redesignation-sources data/artifacts/private-redesignation-sources \
  --source-capture data/artifacts/private-candidate-source \
  --retained-checkpoint data/raw/authenticated/2026-07-31 \
  --processed-dataset data/processed/v2-finbert/<dataset-id> \
  --output-dir data/artifacts/historical-checkpoint-preflight
```

The new output directory and content-addressed report are owner-only and
exclusively created. For independent replay, replace `--output-dir` with
`--verify-report data/artifacts/historical-checkpoint-preflight/compatibility-<sha256>.json`.
The supplied paths must be canonical directories without aliases.

Supported result prefixes are `legacy-governance`, `proxy-class`,
`proxy-definition`, `class-redesignation`, `bank-filings` and `capital-definition`.
The filename digest, internal stage kind and declared
algorithm identities must match before the selected stage independently
replays. The report binds that stage kind and the replay algorithm-set hash.
For either proxy stage and the redesignation stage, `--proxy-sources` is mandatory and must preserve every
prior/current source pair needed by the chain. For the legacy-only stage, omit
that option; unused proxy inputs are refused. Missing or corrupt current-stage
sources cannot fall back to an earlier result or an attested anchor count.
For `class-redesignation`, `--redesignation-sources` is also mandatory.
That option is refused for all earlier stages, so no source input is silently ignored.

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

## Issuer-definition identity-chain replay, November 16, 2026

The preflight now accepts the separately content-addressed 490-anchor
issuer-definition result and replays its complete parent chain. The 13
unresolved identities remain blockers. The actual checkpoint still has only
302 literal issuer/symbol matches, with 201 source securities absent and 198
checkpoint securities outside the fixed source. More verified identity anchors
do not repair a different cohort or establish market continuity.

The [current row-free audit](../ops/issue292/checkpoint-compatibility-audit-20261116.json)
binds a new report and independent replay. The November 14 report, its original
algorithm version and all original data remain unchanged. The command still
returns 2 for a valid blocked report and 1 for invalid evidence. Synthetic tests
also demonstrate that even identical rosters and complete identity counts
cannot release the historical continuity or new-study-chain gates.


## Charter-redesignation replay, November 16, 2026

The fourth supported stage independently replays the 491-anchor result and all
its previous baselines. Its [separate row-free audit](../ops/issue292/checkpoint-redesignation-audit-20261116.json)
retains the new report without replacing either earlier checkpoint audit.
Twelve identities remain unresolved. The original requested roster still has
302 literal matches, with 201 source securities absent and 198 checkpoint
securities outside the fixed source. The original processed assets remain
hash verified; historical continuity and a new study chain remain unverified.
A valid blocked report continues to exit 2 and cannot release publication or
mark the issue complete.


## FDIC/SEC bank supplement

The [bank filing supplement](issue-292-bank-filings.md) adds an explicit fifth
replay stage. For `bank-filings-<sha256>.json`, retain both `--bank-sources` and
`--bank-ownership-sources`, plus the full proxy, redesignation and legacy parent
sources. Earlier stages refuse the two bank-only options. The preflight replays
the complete selected stage and binds its decoder and producer versions; no
missing bank source can fall back to an earlier count.

FDIC-periodic/SEC-ownership anchors have their own status. They contribute to the
verified identity total without relabeling their regulator or changing previous
anchor payloads. Historical continuity and a separately identified new study
chain remain independently blocked.

## Explicit capital-definition replay

The [capital-definition supplement](issue-292-capital-definition.md) adds a
sixth stage after the bank result. Select `capital-definition-<sha256>.json`
with `--capital-sources` in addition to all five parent source-family arguments.
Each older stage refuses this new argument; the new stage refuses missing
current or parent sources. Its filename, stage kind, algorithm identities and
independently recomputed payload must all match. There is no fallback to a
previous anchor count.

The new identity total is 493 of 503. The original checkpoint still has 302
literal matches, 201 absent source securities and 198 checkpoint securities
outside the fixed source. Original processed bytes remain separately verified;
continuity, new study binding, publication and issue completion remain blocked.
A valid retained report still exits 2, and invalid evidence exits 1.
