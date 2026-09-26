# ADR 0031: Record candidate-master observation chronology without claiming historical membership

Date: 2026-09-27. Status: accepted.

## Context

The v1 liquidity screen was dated inside its locked period. A later v2 screen
could use a cutoff before the development folds but still apply a security
master assembled years later. The version-2 screen audit pinned CSV/checkpoint
contents, not when the broad master or screen were actually generated. This is
a survivorship and identifier-selection risk, not a forecasting-model bug.

## Decision

`build-universe` now records a local UTC observation of the reviewed
SEC-to-Alpaca master and hashes its CSV and mapping review. After manual
review, `record-universe-capture` refreshes that **present-time** observation;
it cannot be assigned a historical date through the CLI. `screen-universe`
requires the capture and writes a version-3 audit with its hash and the actual
screen generation time. `verify-universe-screen` rejects a master observed
after the declared cutoff, a screen generated before the New York cutoff day
has finished or on/after the first validation
period, changed inputs, or a checkpoint with a different requested roster.
Its optional `--dataset-dir` also verifies the processed asset hashes and
ties the processed source-manifest digest to that same checkpoint.

The verifier deliberately retains
`screen_trace_verified_upstream_membership_unverified`. A locally written
timestamp and today's live master do not prove which delisted, renamed, or
late-listed securities existed at a historical cutoff. The v2 public gate
remains pending and may not represent this trace as point-in-time membership.

## Alternatives considered

- Retrospectively infer listing intervals from current API assets: inactive
  listings and current symbols do not form a complete historical master.
- Trust the `--as-of` screen flag as a collection timestamp: a user can run
  that flag years later, so it is not valid chronology evidence.
- Treat the self-recorded capture as an independently timestamped archive:
  hashes prove content consistency, not historical availability.

## Consequences and verification

Old version-2 audits cannot be silently upgraded. Existing v1 artifacts and
prospective forward evidence are unchanged. Credential-free synthetic tests
exercise late observation, late screen generation, changed source/review,
renamed or removed members, checkpoint mismatch, and processed-asset tampering.
Full historical membership still requires an independently evidenced archive
with per-identifier validity/availability periods, reviewed source rights,
and a separate v2 protocol; see [issue #292](https://github.com/hoangnguyen2003/edgar-moe/issues/292).
