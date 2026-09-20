# ADR 0004: Verify the immutable public snapshot at serving time

- Status: accepted
- Date: 2026-09-20
- Decision owners: project maintainer

## Context

The public snapshot is a derived, reviewed representation of the frozen v1
study. CI and the Vercel build already compare `data/demo/snapshot.json` with
`config/public_snapshot.lock.json`, but a serving process can outlive a build
or be packaged from a different checkout. Build-time validation alone does not
prove that the bytes loaded by the API still match the reviewed identity.

## Decision

The production `SnapshotRepository` receives the checked-in lock path and
verifies, before serving the snapshot:

- the lock schema and canonical relative snapshot path;
- the snapshot SHA-256 digest;
- the frozen data mode, cutoff, selection hash, locked-test hash, and
  `research_only` marker.

The repository tracks both file modification times. A changed or unavailable
lock causes a fail-closed integrity error instead of silently serving stale or
unreviewed bytes. Synthetic and test repositories may omit a lock explicitly;
the production API does not.

The Vercel Function includes the lock as an explicit runtime input while its
private `config/**` exclusion remains in place. This preserves the source and
function dependency boundaries without exposing research configuration.

## Consequences

Positive:

- runtime data identity is independently checked after deployment;
- a deployment or filesystem drift becomes visible as degraded/unavailable
  health rather than a silent research result change;
- the frozen model and locked result remain immutable and no retraining path is
  introduced.

Trade-offs:

- the API package includes one small public hash/identity lock file;
- deployments must package the lock with the snapshot;
- local synthetic fixtures need to opt out of lock verification when they are
  intentionally not the reviewed public snapshot.

This remains an integrity check, not an independent notarization mechanism:
an administrator who can replace both files can bypass it. CI, source review,
and deployment provenance remain necessary controls.
