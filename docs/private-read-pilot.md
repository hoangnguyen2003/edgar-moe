# Private authenticated synthetic read pilot

The maintainer approved a **$0**, private/authenticated, synthetic-only pilot on
2026-10-04. This is a separate deployable service, not a change to the public
application. Production writes, changes to source credentials, data deletion,
reseeding and new restore/recovery retries are prohibited. Preserve production,
all three restored copies, the populated synthetic recovery database, the private
test bucket and all retained evidence packets.

## Decision and boundaries

Use a minimal, separately staged FastAPI service on an explicitly verified Vercel
Hobby account, with the existing isolated synthetic recovery reader. Reusing the
project's Python/Postgres tooling avoids another language/service dependency.
Do not deploy into, link to, modify settings of or inject secrets into the public
`edgar-moe` project. Do not run Vercel CLI deployment from the repository root.
The separate project name is `edgar-moe-private-read-pilot`; stop if it already
belongs to another resource or cannot be independently identified.

The [Hobby plan](https://vercel.com/docs/plans/hobby) has no additional-usage
billing; excess usage can pause service. **Documentation is not account evidence.**
Before creating the project or provisioning secrets, inspect the actual account
plan, remaining usage and add-ons, and the existing Neon project's free-plan
status. Do not enable a trial, upgrade, billing method, paid integration, analytics
add-on or new database. If either provider can bill usage, stop: a low estimated
cost is not the approved hard $0 cap. Free quotas may be shared with the public
project, so avoid load tests; quota exhaustion is a stop condition even without
billing. Record this shared-quota risk before deployment.

Use [Vercel Authentication](https://vercel.com/docs/deployment-protection) on all
deployments when the verified free account supports it. Independently, the app
requires an owner-only, random 256-bit bearer token for **every** route, including
invalid paths and method requests. Never put the token in a URL, git, build args,
logs or a public-site environment. The pilot is an owner-only machine-to-machine
credential, not multiuser SSO or an enterprise authorization system. No CORS,
Swagger, static assets, public health route, cookies or browser UI are provided.

The app's fixed routes are:

| Route | Result |
| --- | --- |
| `GET /v1/status` | Table counts and the expected synthetic failed-run count |
| `GET /v1/forecasts?limit=1&ticker=FIXT` | Up to five synthetic rows, explicit safe projection |
| `GET /v1/performance` | Coverage counts; no performance claim |

Only database `edgar_recovery_rehearsal_20261004` and reader role
`edgar_recovery_auditor_20261004` are accepted. Bind the canonical isolated
endpoint hash and full registry digest to the already verified recovery receipt,
not to whichever URL is presented at deployment time. Require `verify-full` TLS
and channel binding. Every request verifies actual DB/session identity, effective
SELECT-only grants (including no memberships, schema CREATE, table/column writes,
grant options, mutating sequences or executable security-definer functions), and
the approved full synthetic snapshot digest. A changed dataset fails closed.
Raw rows are hashed only in memory; responses omit evidence URIs, arbitrary JSON,
provider identities and driver diagnostics. No production/default dotenv fallback.

Each read uses a fresh connection, read-only repeatable-read transaction,
three-second connect/statement timeout, 500ms lock timeout and five-second idle
transaction timeout. It rolls back and closes. One in-flight read and at most
60 admitted reads are allowed **per process**; no overflow, persistent pool or
automatic retries. The absolute expiry must be within 24 hours. Function duration
is capped at 20 seconds. These are **not global platform concurrency limits**:
serverless instances multiply independently. No statement-level timeout is a
proof of an entire request deadline. Retain observed `max_connections`; separately
inspect Neon pooler/account limits and platform autoscaling before a longer pilot.

## Build, deployment gates and verification

1. Create a task branch from latest main, run tests, open a PR and squash-merge
   after checks pass. The public API module does not import the pilot.
2. Stage an allowlisted bundle into a **new** temporary directory:
   `python scripts/stage_private_read_pilot.py --destination /private/tmp/NEW-PILOT-DIRECTORY`.
   Review its manifest: only the minimal app, shared identity/grant inspectors,
   pinned runtime dependencies and separate Vercel config. No research data,
   dotenv files, public assets, migrations, R2 client or registry writer.
3. Verify actual $0 account/usage evidence and isolated target scope. Record the
   existing branch expiry; do not extend it or claim a seven-day observation window
   if it expires earlier. Provision only `PILOT_DATABASE_URL` (hosted SELECT-only
   URL), `PILOT_BEARER_TOKEN`, `PILOT_ENDPOINT_SHA256`, `PILOT_REGISTRY_SHA256` and
   `PILOT_EXPIRES_AT` for the separately identified project. Secrets stay outside
   the staged source bundle and are passed through secure stdin/environment APIs.
4. Deploy the separate project and record its immutable deployment identity.
   Keep platform deployment protection; use its authorized automation bypass
   header privately if needed, never a shareable bypass URL. Verify anonymous
   access is refused by platform and app boundaries. Do not disable platform
   protection to make a smoke script pass.
5. Run the bounded hosted probe with `PILOT_BASE_URL` and `PILOT_BEARER_TOKEN`
   privately scoped to that process (and `PILOT_VERCEL_BYPASS_TOKEN` only if the
   separate project's platform protection requires its authorized automation
   bypass; app authentication is still mandatory):
   `python scripts/probe_private_read_pilot.py --output NEW-PRIVATE-REPORT.json`.
   It performs 15 requests, never follows redirects, retains only allowlisted
   status/timing fields, and stops at the first unexpected response. If platform
   protection intervenes, record that prerequisite as unobserved rather than
   treating an HTML login page as app-auth evidence.
6. Independently recheck fixture digest and previous-copy counts, public-site
   serving boundary, bundle hashes and redaction. Retain a new immutable private
   packet. Do not mark missing account/provider/backup evidence passed.

All responses, including errors, are private/no-store. Never retain tokens,
URLs, headers, raw forecast rows or SDK exceptions in GitHub logs/artifacts.
There is no scheduled observation or load generator in this pilot.

## Stop and rollback

Stop before deployment on missing account authentication, non-free plan, unknown
billing behavior, wrong project, unexpected credentials, changed fixture, grant
drift or insufficient quota. Stop observations on any auth/cache boundary failure,
non-read-only session, unexpected data, limit/timeout errors or public-site impact.
Use the pilot's absolute expiry to stop reads; do not reset its budget or extend
its lifetime silently. The rollback is to leave this separate service unavailable
and its data/evidence intact, not to delete, rewrite or reseed any copy. Any
provider-setting change affecting another project requires separate authorization.

## What this does not close

Issue #361 still needs deployed evidence, actual provider connection budgets and
managed backup/retention observations. Logical recovery time is not managed-backup
RPO/RTO. Issue #290 additionally requires representative volume, safe query plans,
seven actual consecutive observation days and provider latency attribution. This
tiny immutable synthetic fixture establishes neither investment performance nor
representative hosted SLOs. Source-use review stays unresolved; no provider-backed
research refresh, training, forecasts or settlement is authorized.
