# ADR 0021: Compare frozen-runtime reproductions before dependency upgrades

- Status: accepted
- Date: 2026-09-22
- Deciders: project maintainer

## Context

The frozen v1 model is hash-pinned, but a dependency upgrade can change the
numerics of inference without changing the checkpoint bytes. The locked-test
dataset and score archive are private, so the normal CI job cannot reproduce the
study. FinBERT feature caches had an additional hazard: their cache identity did
not include the PyTorch or Transformers runtime, allowing a cache produced under
one runtime to be reused after an upgrade.

## Decision

- Include the exact PyTorch and Transformers versions in the FinBERT cache
  identity. A runtime upgrade therefore creates a new cache namespace instead of
  mixing embeddings produced by different inference stacks.
- Extend the private frozen normalization audit to record non-secret runtime
  versions, a digest/shape for the reproduced score vector, and (optionally) a
  private score archive. The report never stores filing text, observations, or
  credentials.
- Compare baseline and candidate reports with
  `scripts/compare_frozen_runtime_reports.py`. The comparison fails closed when
  frozen identity fields differ, the candidate cannot reproduce the locked score
  within the configured tolerance, or test indices/scores differ beyond the
  configured maximum absolute delta.
- Keep the comparison operator-run and private. It is evidence required before
  merging a frozen-inference dependency upgrade; it is not a training step, a
  model-selection path, or permission to change v1.

## Alternatives considered

- Hashing only the model artifact detects checkpoint drift but cannot detect
  runtime numerics or stale embedding caches.
- Running the audit in public CI would require publishing private datasets and
  locked scores, violating the data boundary.
- Silently accepting a changed score vector would make a dependency upgrade a
  hidden change to the frozen research result.

## Consequences

Operators must run the audit in the current and candidate dependency
environments and retain the two private reports/score archives long enough for
review. A compatible runtime can still be rejected for unrelated security or
operational reasons. A failed comparison leaves the frozen dependency pinned;
it does not auto-update the lockfile or retrain the model.

## Verification

The cache identity has unit coverage. The report comparator has unit tests for a
passing cross-version reproduction, score drift, and frozen-identity drift. The
existing artifact/normalization audit remains the source of the baseline score
error, while the comparator verifies the cross-environment decision.
