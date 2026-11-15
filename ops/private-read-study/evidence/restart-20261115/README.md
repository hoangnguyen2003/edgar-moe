# Separate November 15–22 restart

Status: **prepared; post-start hosted verification and observer activation pending**.
The absolute policy window is November 15, 2026, 15:00 UTC through November 22,
15:00 UTC (22:00 Vietnam time). The policy SHA-256 is
`190f660677e6d498603772d537b6ab8b1c555391ba22e9155b952fa0763db86e`.

This is a fresh observation window, not an extension or replay of November 6–13.
The expired reader remains expired at November 13, 08:15 UTC. One new reader
was created in the existing isolated synthetic database with SELECT-only grants,
no role memberships or administrative privileges, a two-connection limit and an
expiry matching the new policy. The schema and fixture were verified unchanged.
No production dataset, source credential, model or append-only record was changed.

The existing Vercel project is still Hobby with no trial and protects all
deployments. The new deployment is a preview in Singapore. The new reader URL
was installed only in its encrypted preview variable; the HTTP observer receives
no database credential. Project-scoped provider metadata credentials retain their
separate destinations. The Vercel token's owner-declared expiry is December 6;
the current-token metadata endpoint returns 404, so that expiry is not described
as independently provider-verified. Access to the fixed project and denial on
the unrelated project were verified, as was a new trace session.

The endpoint remains fixed at 0.25 CU. At preflight it was idle; project metadata
reported 1,845 compute seconds and 6,448,471 transfer bytes in the current
consumption period. No paid plan, card, trial, upgrade or billing change occurred.
Reported consumption metadata can lag; it is not a measurement of this week.

## Completed setup checks

- Four fixed native application routes passed with the new SELECT-only reader.
- Four further native reads verified the 10,000 invented forecasts, 5,000 labels,
  page/filter counts and nonempty uncertainty branch. Reader cleanup and unchanged
  schema/fixture were verified. Fifteen distinct SELECT query shapes have new
  allowlisted `EXPLAIN (ANALYZE, BUFFERS)` plans, with no SQL parameters or rows.
- The preview enforced platform sign-in (302), application authentication (401)
  and the real future-window boundary (425). These requests opened no database
  connection.
- Two further expected pre-start 425 refusals produced complete matching provider
  REST traces. Both were classified warm. They verify metadata transport on this
  deployment, not database reads, cold starts or measurement days.
- All 132 focused study tests passed on the merged main tree. These use offline
  fixtures and are separate from the live setup checks.

Setup reads do not enter the scheduled-observation sample count. Application
requests are never automatically retried. Only the same trace metadata GET may
be retried within the existing three-attempt bound for delayed ingestion or
explicitly transient failures. Unknown traces remain unknown and failed attempts
remain in the packet.

## Collection and closeout

The unchanged declared traffic model is 168 hourly pairs, two reads per pair,
plus at most 24 verification reads (360 admitted read attempts). Routes rotate
among status, forecast page, fixed ticker filter and performance. Missing or
duplicate GitHub scheduling slots remain visible; there is no catch-up replay.
The protected routes are private and no-store, so zero observed edge hits is a
valid reported cohort rather than an invented cache-hit measurement.

The existing observer stops on failed reads, attribution or receipt validation.
The application rejects requests outside its absolute policy window and the new
database credential expires at the end. The metadata-only finalizer retains
incomplete as well as complete results and disables both bounded workflows after
the end and grace period. It does not automatically assert acceptance or close
#290. Seven real days of collection, errors, cohort sizes and comparison quality
must still be reviewed. No SLO or alert threshold is established by setup tests.

The November 6 local native baseline remains a separate environment. Its timings
are not pooled with HTTP/TLS/provider startup latency. The finalizer's existing
November 6 reference files are historical shared-fixture/setup references;
they do not prove this new deployment or window. This packet's fresh plans and
binding receipts provide the separate restart evidence. Original packets and
the frozen v1 remain unchanged.
