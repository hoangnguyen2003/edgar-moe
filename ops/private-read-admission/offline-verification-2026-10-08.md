# Offline verification — 2026-10-08

Scope: local synthetic tests and recovery planning only. No live integration,
deployment, provider request, database connection, new cloud resource, secret,
cron, source-backed workload or charge occurred. Existing project work and
copies remain unchanged. This records the local pre-PR verification, not a
merged-release or live-provider acceptance result.

## Changes and observed results

- Added full executor tests with independent Node processes sharing one actual
  temporary SQLite file. Eight competing executors start exactly one synthetic
  read; a settled result without closed-connection acknowledgement keeps the
  slot occupied. A forcibly terminated executor leaves its consumed allowance
  and reservation intact after restart.
- Added a regression for ambiguous persisted JSON. Before the fix, contradictory
  duplicate keys were silently accepted and a synthetic read completed. The
  ledger now requires an exact serializer round trip before validating state;
  duplicate properties and non-round-tripping encodings refuse without running
  the reader or rewriting the original evidence. Eight malformed variants pass
  within the regression test after the fix.
- Wrote `recovery-drill-plan.md`, covering a supported synthetic recovery source,
  a fresh target, actual cost/quota and preservation gates, one attempt, clock
  provenance, recoverable-point evidence, full verification, timing and stops.
  This is a proposed test specification, not authorization or an actual restore.

## Reproduce

With the already available Node v24.15.0:

```sh
node --test ops/private-read-admission/admission.test.mjs ops/private-read-admission/executor-process.test.mjs ops/private-read-admission/study-plan.test.mjs ops/private-read-admission/workerd.test.mjs
```

Observed: **31 passed, 0 failed, 1 skipped**. The skipped workerd test requires
an already installed local Miniflare module path. No dependency was downloaded
or installed to turn that skip into a pass. Local `.mjs` syntax checks passed.

The existing private read-pilot and probe unit suites also passed in the existing
Python environment. They are offline unit evidence, not a repeat hosted smoke or
a provider check. No changes to the private API or deployment configuration were
made for this work.

## Remaining boundaries

This proves local behavior with one shared store, not that every deployed
instance uses that store. Trusted real authentication, actual reader identity,
backend closure, provider storage mapping and live quota behavior remain separate
integration requirements. No emulator initializer or fake fixture authentication
may be deployed. The controller does not claim to prevent arbitrary HTTP traffic
from consuming platform invocation quota before admission.

Managed recovery, RPO/RTO, preservation and $0 feasibility need actual scoped
provider evidence; the plan cannot supply it. Do not reset or extend the expired
pilot. Keep #361 open and keep #290's real seven-day representative observation
requirements separate. No new P0 packet or ready decision is asserted here.
