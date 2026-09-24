# ADR 0029: Bind authenticated refresh resumes to request and asset identities

- Status: accepted
- Date: 2026-09-24
- Deciders: project maintainer

## Context

The refresh runner writes source files incrementally before a final hashed
manifest. Previously, `--resume` reused market bars and other source files
based on path existence, then wrote a new manifest. A syntactically valid but
truncated bars file could therefore be counted and certified as a fresh input.
The same dated directory could also be resumed with a changed universe, feed,
forms, or macro series. A completed checkpoint was rebuilt rather than treated
as immutable.

## Decision

Write a non-secret `request.json` before collecting assets. It pins the exact
universe, dates, feed, forms, macro series, batch size, and filing cap. Resume
of a partial checkpoint requires an exact request match. Each completed source
asset receives an adjacent SHA-256 sidecar; only matching assets are reused.
Missing or mismatched sidecars cause a fetch using the pinned request, with
atomic file replacement after a successful response. A failed filing fetch is
recorded as attrition and never promoted from a stale local file.

If a final manifest exists, verify every manifest asset, compare the request,
and return without mutating any bytes. Older complete checkpoints without
`request.json` are checked against their manifest and requested-universe file.
Older partial checkpoints without a request contract fail closed. Final
manifests hash the request itself; the sidecars are resume aids, not substitutes
for final verification or authentication of a remote provider.

## Alternatives considered

- Trust existence or parseability: cheap, but unable to detect a valid-prefix
  truncation and silently changes the effective research input.
- Redownload every asset on resume: safe against stale caches, but repeats
  thousands of paced SEC requests after an unrelated market/macro failure.
- Central mutable progress journal: fewer files, but a single high-contention
  journal adds partial-write and recovery complexity to concurrent filings.

## Consequences and verification

Resume now hashes reusable files, so it spends extra local I/O in exchange for
integrity. A 16.96 MB, 376,832-row synthetic cache took 23.045 ms median for
the prior line count versus 28.698 ms for hash verification plus the same count
on the maintainer's laptop (five samples, 2026-09-24). Reproduce with
`scripts/benchmark_refresh_resume_integrity.py`; this is not a provider or
production throughput estimate. Re-fetching a damaged asset still depends on
provider availability and may consume free-tier quota.

Failure-injection tests interrupt after bar collection, truncate the bars to
valid NDJSON, damage SEC facts, remove a filing sidecar, and verify those assets
are refetched while intact submissions are reused. Other tests prove changed
requests are rejected and completed manifests remain byte-for-byte unchanged.
Private provider restore and large-checkpoint timing remain operator evidence
to collect; no frozen v1 artifact is rewritten by this change.
