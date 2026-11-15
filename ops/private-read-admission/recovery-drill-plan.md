# Proposed isolated managed-recovery drill

Status: **plan only; no provider operations authorized or executed by this file**.
Related: #361. This is not a backup receipt, recovery result, deployment guide,
or evidence that the existing rehearsal child's later state has PITR coverage.

## Purpose and decision

Demonstrate provider-managed recovery of a small, explicitly synthetic dataset
into a new disposable target, without touching production or any existing copy.
Measure a recovery-point gap and complete recovery elapsed time. Results would
apply only to this dataset, recovery mechanism and test; they would not establish
a production SLA, seven-day latency study, or recovery of an unrelated branch.

Use a recovery source that actually supports the selected managed mechanism.
Current Neon documentation limits point-in-time recovery sources to root branches:
[provider documentation source](https://github.com/neondatabase/website/blob/main/content/docs/postgres/backup-restore/branch-restore.md).
Do not infer coverage of child-only changes from the parent's history.
Documentation is a prerequisite, not proof of coverage in the actual account.

## Gate 0: scope and $0 feasibility, before any live operation

An operator must record and approve all of the following together:

- The exact isolated synthetic source and **new** recovery target. Neither may
  resolve to production, the existing rehearsal target, or any preserved copy.
  Record the before-test resource inventory privately to enforce this exclusion.
- Whether the source is an existing permitted root containing the exact synthetic
  state or a separately approved new synthetic root/project. Never promote,
  reset, reseed, or restore an existing copy merely to obtain root status.
- The provider-supported method for obtaining the historic state in the new
  target. Creating a point-in-time copy must be reported as that operation; do
  not label it an in-place restore test. Never restore over the original source.
- Verified current plan, remaining storage/compute/resource allowance, and the
  complete resource footprint of the method, including automatic backup copies.
  No paid plan, add-on, trial, overage-enabled integration, or new charge. Existing
  usage is shared; low usage and a billing alert alone do not guarantee a $0 cap.
- At most one source setup and one recovery attempt, one active test connection
  at a time, no retries, no scheduled/load traffic, and a fixed absolute deadline.
  Resource initialization and writes must be confined to the new approved
  synthetic source; the ordinary app reader remains SELECT-only.
- Explicit permission for the new resources, synthetic setup writes, sequential
  verification connections, and any later cleanup of **only those new resources**.
  This plan authorizes no deletion or lifetime extension by itself.

If the mechanism, target isolation, backup coverage, quota headroom, or billing
behavior cannot be established, stop with **not run / missing prerequisite**.
Do not upgrade or substitute a logical dump to make managed recovery pass.

## Gate 1: predeclare data and recoverable-point evidence

Before setup, prepare an immutable private test specification:

1. A fixed synthetic schema and tiny marker dataset, with canonical expected
   contents, schema identity, counts and digests. No source-provider research
   data, user information, model outputs, or production records.
2. The intended managed history/snapshot mechanism, effective retention window,
   and the timestamp or LSN of the selected durable recovery point.
3. A record of successful synthetic commits and evidence linking the selected
   point to the expected dataset. A timestamp selector or current branch head
   screenshot is not a backup receipt. If point availability cannot be verified,
   mark the recovery-point evidence missing.
4. A simulated failure reference time and predetermined expected restored state.
   Do not destroy any data to simulate failure. Never manufacture older provider
   timestamps, backdate commits, or infer recoverability from a local clock.
5. Clock provenance, the required grant/identity checks and pass/fail criteria.
   Wall-clock time orders durable points; a monotonic timer measures elapsed time.
   Unknown clock alignment makes the recovery-point gap unmeasured.

## Gate 2: one actual managed recovery into the new target

- Independently bind the source and target to the approved private inventory.
  Stop if either identity or data scope differs from the specification.
- Start the elapsed-time clock immediately before requesting target creation or
  recovery. Include provider provisioning, readiness waiting, verification, and
  confirmed read availability; do not time only one SQL query.
- Invoke the approved provider-managed mechanism once, using the recorded point.
  No scripted retries or fallback to another source/point. Bound any read-only
  status polling in the approved test specification before running it.
- Verify target identity, schema, expected counts and complete canonical content
  digests. Check the verification reader's effective permissions and intended
  read availability. Record grants as verified or missing, not inferred from
  a role name or from a different target's earlier test.
- Stop the elapsed-time clock only when all required checks pass and the test
  connection is confirmed closed. Any mismatch is a failed drill, not a reason
  to overwrite the target or rerun recovery.
- Recheck preservation of the original source and existing copies using the
  approved evidence method. Record any unobserved preservation checks honestly;
  obtaining them must not create unapproved production connections.

## Measurements and reporting

- **Observed recovery-point gap (RPO-style measurement):** simulated failure
  reference time minus the latest recovered durable point established by the
  provider/marker evidence. Include uncertainty and dataset scope. The configured
  history window is retention, not the measured gap. A deliberately older chosen
  point is a selected-point gap, not proof of the latest achievable RPO.
- **Observed recovery elapsed time (RTO-style measurement):** recovery-request
  start to independently verified usability, including provisioning and checks.
  A single drill is an observation, not a provider guarantee.
- Record outcome separately for target isolation, recoverable-point evidence,
  content/schema integrity, effective grants, read availability, timing, original
  preservation, cost/quota gate and cleanup. Missing evidence remains missing.

Keep receipts owner-only and redacted. Do not log credentials, URLs, account or
resource identifiers, headers, raw rows, SQL-driver errors, or provider response
payloads in public artifacts. Verify hashes of a **new** evidence packet without
altering earlier packets. Any public summary requires the approved publication
scope. No hypothetical timestamps or synthetic offline results may be reported
as an actual provider recovery or used to mark #361 ready.

## Stop conditions

Stop on unknown billing, quota pressure, public-site impact, unexpected resource
creation, identity/grant/content drift, missing point evidence, deadline expiry,
unconfirmed connection closure, or an operation requiring a retry. Do not alter
production, recover over an existing branch, delete preserved data, relax controls,
extend the old pilot, create secrets/cron, or continue on a different target.

The immediate next safe step is a reviewed admission-controller integration and
a private feasibility/scope record for this drill. Actual hosted admission and
managed-recovery evidence remain outstanding until their real tests pass.
