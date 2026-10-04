# Isolated partial-write recovery rehearsal

This manual provider exercise uses only a newly created empty Postgres database
on the separately verified isolated branch and an empty private R2 test bucket.
It must not use production or any existing restored copy. The October 4 approval
covers one exercise, not repeated dispatches or clearing an already used target.

The reviewed workflow is **Provider isolated partial-write rehearsal**. Require
`I_UNDERSTAND_ISOLATED_FAILURE`, and dispatch only after operator verification of
the branch topology and bucket-scoped token settings. Endpoint checks cannot
independently establish provider branch topology or prove token IAM scope.

## Configuration

Use new secrets, never repoint the runner, auditor, or restore secrets:

- `EDGAR_MOE_REHEARSAL_DATABASE_URL`: owner of the new empty isolated database
  `edgar_recovery_rehearsal_20261004`, with verified TLS.
- `EDGAR_MOE_REHEARSAL_AUDITOR_DATABASE_URL`: separate non-owner LOGIN role,
  SELECT-only default table grants in that database, no other memberships or
  table/schema/role creation privileges.
- `EDGAR_MOE_REHEARSAL_R2_ENDPOINT_URL`: HTTPS S3 account endpoint.
- `EDGAR_MOE_REHEARSAL_R2_BUCKET`:
  `edgar-moe-recovery-rehearsal-20261004`, empty, public access disabled.
- `EDGAR_MOE_REHEARSAL_R2_ACCESS_KEY_ID` and
  `EDGAR_MOE_REHEARSAL_R2_SECRET_ACCESS_KEY`: object read/write token scoped only
  to that test bucket, never admin or all-bucket access.
- `EDGAR_MOE_REHEARSAL_R2_AUDITOR_ACCESS_KEY_ID` and
  `EDGAR_MOE_REHEARSAL_R2_AUDITOR_SECRET_ACCESS_KEY`: separate object read-only
  token scoped only to the test bucket.

The existing source URL and production bucket name are passed only for endpoint
and bucket-conflict checks. No source connection or object operation occurs.
Secrets are step-scoped; tool installation, building, hashing, scanning and
uploading do not receive them. Child processes remove inherited source/SDK
credentials and receive only their explicitly scoped target credentials.

## Evidence sequence

1. Reject wrong target names, production endpoint aliases, owner-reader identity
   conflicts, reused R2 identities, URL overrides, nonempty databases/buckets,
   or an elevated database reader. No target is ever cleared.
2. Migrate the empty target and seed an explicitly synthetic successful run.
   Mirror its baseline object and require an independent read-only Go audit.
3. Start one new synthetic run and write through the real mirrored store using
   the test bucket's read-only token as the deliberately failing mirror. Require
   an actual **PutObject AccessDenied HTTP 403** response. Another exception or
   an unexpectedly successful write fails the rehearsal, without cleanup.
4. Use the same primary-registration-before-failure path as production. Keep
   the failed run and its immutable primary reference. Require independent Go
   verification of local bytes and detection of the one missing R2 object.
5. Only after that independent verification, run the actual
   `forward-reconcile-artifacts --repair` command with the SELECT-only target
   database credential and bucket-scoped writer token. Repeat once to verify
   idempotence, not to retry a failed exercise. No database writes are authorized
   in either repair.
6. Hash all eight complete synthetic registry tables before and after each
   repair, require identical rows and exactly two remote evidence objects, and
   run the independent R2 auditor after each repair.

The final auditor must still return the single expected `failed_run` finding
and nonzero findings exit code: repairing the mirror must not rewrite the failed
run. Any missing/corrupt object, extra finding, incomplete verification, or
unexpected exit fails the rehearsal. We do not hide this failure by shortening
the auditor's failed-run window or relabeling the run as successful.

Raw SDK, migration and CLI diagnostics are captured in memory, never printed or
uploaded. The fixed receipts, independent audit reports, reconciliation reports
and **synthetic-only** primary files are retained privately in Actions for 30
days after redaction and SHA-256 hashing. Download and verify their byte hashes
into owner-only storage; do not publish raw rows or credentials. These primary
files are generated test fixtures, not provider research payloads. Existing
copies and evidence remain unchanged; no delete/clean/reset operation exists.

This exercises the hosted synthetic recovery control, not a production outage,
real forecast, source-rights clearance, alpha claim, managed-backup RPO/RTO, or
private deployed API least-privilege proof. Overall P0 readiness must stay
blocked until its independent remaining requirements are actually satisfied.
