# Paired XBRL runtime and saved-evidence checks

The current retained evidence does **not** prove completion of
[#275](https://github.com/hoangnguyen2003/edgar-moe/issues/275). In addition to
the unresolved source-use decision, its saved input-attribution report fails
the current contract, and both processed manifests lack complete recorded
runtime provenance. A standalone valid outcome report is insufficient.

The new [saved-evidence verifier](../scripts/verify_paired_xbrl_evidence.py)
checks the report/manifest/selection chain without loading a dataset, decoding
an array, calculating metrics, training or contacting a provider. Its output
stays under ignored `data/artifacts/` in a new owner-only file. It refuses to
overwrite a prior audit. Existing datasets and reports are read-only inputs.

## Observed gaps

An evidence-only audit of the retained pair on 2026-10-27 made 39 checks. Four failed:

- **Input report contract:** the retained report lacks the current
  `same_python_runtime` and `same_text_encoder` fields. Its old
  `isolated_policy_change` label is not proof under the current contract.
- **Pinned Python runtime:** neither processed manifest records
  `provenance.python_runtime`.
- **Pinned FinBERT identity:** both encoder identities omit
  `tokenizer_revision`, `batch_size` and `device`. Matching incomplete identity
  strings do not establish a matching pinned runtime.
- **Report runtime claims:** the missing flags cannot certify the runtime.

The outcome report passes its standalone shape/hash check and is linked to the
retained input report. The saved selection JSON hashes, dataset identities,
source-manifest reference, configurations, full model-name rosters, declared
pre-test flags, champions and fold summaries also bind consistently. Those are
content and declaration checks; they do not establish the truth of provenance
or revalidate the underlying predictions.

The source reference matches the retained checkpoint manifest bytes. Its raw
provider payload hashes were not rechecked. Processed binary assets and OOF
archives were deliberately **not read** after metadata failed. No locked labels,
model weights or fresh outcomes were accessed or generated. No private research
figures are included in this document.

## What the verifier establishes

The [implementation](../src/edgar_moe/modeling/policy_evidence.py) requires both
declared configurations as explicit operator inputs, rather than accepting a
smaller roster from the selection being verified. It checks that the pair
differs only in the XBRL policy, uses the expected full rosters, and retains the
pinned `2025-01-01` boundary and protocol revisions. A rehashed report cannot
silently point to another input audit, manifest, selection or champion.

Only after all metadata checks pass does it stream-hash the fixed processed
assets and OOF archive. Even a successful `content_bound_only` result explicitly
does **not** verify numerical results, source permission, historical membership,
pre-test audit chronology or issue completion. Duplicate JSON keys, nonfinite
JSON, unknown asset paths, symlinked files and oversized/nonregular inputs
refuse without rewriting evidence. The separate source-use hold is unchanged.

Run the evidence-only command with explicit private paths:

```bash
.venv/bin/python scripts/verify_paired_xbrl_evidence.py \
  --baseline-dataset <legacy-rebuild-directory> \
  --candidate-dataset <candidate-directory> \
  --baseline-selection <legacy-selection-directory> \
  --candidate-selection <candidate-selection-directory> \
  --input-audit <retained-input-report.json> \
  --outcome-report <retained-outcome-report.json> \
  --source-manifest <retained-authenticated-checkpoint-manifest.json> \
  --output data/artifacts/<new-private-directory>/content-audit.json
```

The command exits nonzero on a blocked chain and retains the failure report.
An older report passing its own verifier must not substitute for this check.
The default baseline/candidate configs are `config/authenticated-free.yaml` and
`config/authenticated-v2.yaml`; explicit alternatives must describe the full
declared study rather than authorize a reduced retrospective comparison.

## Required resolution

Recover and review contemporaneous build provenance if it exists. Do not copy
today's environment into old manifests, invent missing historical fields or
rewrite frozen artifacts. If the recorded build cannot be established, a
source-cleared rebuild needs new dataset/study identities and complete runtime
records. Any new attribution or outcome calculation remains subject to the
source-use decision and original development/locked-boundary restrictions.
The saved-evidence check is not permission to rerun those operations.

Source-specific use evidence and interpretation/publication review are still
required. The original acceptance also requires an adequately evidenced shared
checkpoint, pre-test input audit chronology, full paired model/OOF validation,
cost sensitivity and explicit failed hypotheses. No metadata-only audit can
stand in for those requirements. Keep v2 pending and #275 open.

The synthetic tests cover both the input/outcome contracts and the saved
evidence chain. They demonstrate that a valid
standalone outcome cannot hide an obsolete input report, matching incomplete
runtime strings cannot be blessed with true flags, reduced rosters/config drift
cannot pass, and tampered/rebound evidence refuses without array access.
Nonfinite JSON includes numeric overflow such as `1e999`; symlinks in parent
directories are also refused. These are local filesystem checks, not a sandbox
against an attacker concurrently replacing parent directories.

Private receipts remain in ignored `data/artifacts/issue275-evidence-audit-20261027/`
and are not included in the source change. This change does not run provider
operations, rebuild research, update the public catalog or establish issue
completion.
