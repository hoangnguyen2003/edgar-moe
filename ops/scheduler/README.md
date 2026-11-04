# Optional Cloudflare forward scheduler

`cloudflare-forward-scheduler.mjs` is a small, provider-neutral scheduling
adapter for the existing `forward-production.yml` workflow. It is intentionally
not active by default: the repository keeps the GitHub Actions schedule until a
maintainer deploys this Worker, observes a successful dispatch, and approves a
separate cutover PR. The deployment workflow accepts only the `main` ref and its
explicit `DEPLOY` input.

The committed configuration explicitly disables `workers.dev`, preview URLs,
Logpush and persistent observability. Its validator rejects public routes and
implicit/enabled URL settings. These settings preserve the reviewed private
Worker boundary during deployment; a `fetch` handler returning 404 does not
itself disable public routing. See the official [Wrangler configuration
reference](https://developers.cloudflare.com/workers/wrangler/configuration/).

## Why this boundary exists

GitHub Actions schedule events are best-effort and have no start-time SLO. A
Cloudflare Cron trigger can preserve the current `07:17 UTC` source-cutoff
semantics without moving the cron into the pre-market window. The Worker does
not run inference, read market data, access Postgres/R2, or receive any model
or provider credential. Its only capability is dispatching the already-reviewed
GitHub workflow.

The adapter performs one read-before-write duplicate guard: it lists active runs
of the workflow on `main` and skips dispatch when one is queued or running.
GitHub Actions' existing concurrency group remains the final backstop; this is
not presented as an exactly-once distributed scheduler.

## Secure, staged rollout

### Dormant bootstrap credential verification (not a deployment)

The **Verify dormant scheduler credentials** manual workflow reads the two
Cloudflare secrets from the `scheduler` environment only on `main`. It performs
at most two GET requests against the approved Worker's immutable ID: Worker
metadata and the first page of versions. It checks the bootstrap Account ID
digest, exact Worker identity, disabled `workers.dev` and version URLs, disabled
observability/Logpush, no tail consumers, and zero uploaded versions. There is
no retry, redirect following, Worker invocation, upload, deployment, cron
configuration, workflow dispatch, or access to Postgres/R2/provider credentials.

The retained report contains only fixed reason codes, booleans and bounded
counts/status codes. API responses, URLs, Account IDs and credential values are
never retained or printed. The report is hashed and checked for redaction before
artifact upload. HTTP errors, missing metadata, configuration drift and any
uploaded version fail closed.

Success proves authenticated metadata reads and the undeployed boundary. It
does **not** prove Editor write permission, exclusive token scope, expiration,
billing state, or scheduler timing. Scope/expiration require the reviewed token
policy; the Free plan requires separate account evidence. Keep the temporary
bootstrap Admin token revoked and use only per-Worker Editor access for CI.

Do not run **Deploy optional forward scheduler** after this check: its example
config contains a live cron, and this dormant bootstrap approval does not
authorize deployment, adding a GitHub dispatch secret, source-backed execution,
or production cutover. Those remain separate reviewed and approved steps.

### Prepare code before adding the dispatch credential

The manual **Prepare private forward scheduler without Cron** workflow has two
fixed stages. Its default `VERIFY` stage makes at most four bounded GET requests
against the approved account/Worker: Worker metadata, one version page, Cron
schedules and secret names. An empty Worker is explicitly refused before legacy
script/secret reads. It never reads a secret value or invokes the Worker.

After a successful dormant credential check, choose `PREPARE` once. The workflow
rechecks the exact existing undeployed Worker, uploads the reviewed adapter using
`wrangler.prepare.toml.example`, and verifies disabled public/preview URLs,
disabled persistent logging, an empty Cron schedule, and dispatch-secret presence.
Both deployment workflows copy their reviewed example to `wrangler.toml` before
running Wrangler. The pinned CLI ignores the `.toml.example` extension; a plain
TOML parser passing is insufficient. Preparation runs a credential-free CLI dry
run before any provider read/write, and CI dry-runs both profiles. All earlier
private/provider copies remain intact. The preparation config must
pass `validate_scheduler_config.py ... --prepare`; that mode rejects the active
Cron profile, and the ordinary mode rejects the preparation profile. Missing Cron
configuration is not equivalent to an explicitly empty Cron list.

After preparation, add a separately scoped GitHub dispatch credential as the
Worker's `GITHUB_TOKEN` secret. Do not store an Actions job's ephemeral built-in
token there. Use `VERIFY` for subsequent read-only checks: the initial dormant
verifier intentionally refuses once code exists. Credential presence does not
prove validity or scheduler-origin timing. `PREPARE` does not enable Cron or
change the held provider-execution path; active rollout and live margin evidence
remain separate steps below.

### Later active rollout (requires separate approval)

1. Create a fine-grained GitHub token restricted to this repository with only
   Actions read/write and Contents read. Store it as a Cloudflare Worker secret;
   never put it in `wrangler.toml`, Git, a GitHub issue, or a Worker response.
2. Copy the example configuration and review every non-secret value:

   ```bash
   cp ops/scheduler/wrangler.toml.example ops/scheduler/wrangler.toml
   npx wrangler secret put GITHUB_TOKEN \\
     --config ops/scheduler/wrangler.toml
   npx wrangler deploy --config ops/scheduler/wrangler.toml
   ```

   The committed configuration contract can be checked without Cloudflare
   credentials:

   ```bash
   uv run python scripts/validate_scheduler_config.py
   ```

   For a repeatable account-side deployment, use the manually triggered
   **Deploy optional forward scheduler** GitHub Actions workflow. It runs the
   same configuration and Worker contract checks first, requires an explicit
   `DEPLOY` confirmation, and reads only `CLOUDFLARE_API_TOKEN` and
   `CLOUDFLARE_ACCOUNT_ID` from the `scheduler` environment. The workflow does
   not receive `GITHUB_TOKEN`; set the Worker's `GITHUB_TOKEN` secret directly
   with `wrangler secret put` as shown above.

3. Use the Worker logs and GitHub Actions to confirm a single
   `workflow_dispatch` run starts at the intended UTC time. Check the resulting
   `pre_open_schedule_margin` quality check; it is the application-side source
   of truth for whether the run reached the entry window.

   To make the GitHub-side observation repeatable, copy the run ID from the
   first dispatch and run **Observe optional forward scheduler** from the
   Actions tab. The workflow reads one run with an `actions: read` token and
   retains a hashed, redacted report containing only the event, workflow path,
   `main` ref, commit, timestamps, status, and validation reason codes. A
   passing report proves the run shape and recency; it deliberately records
   `scheduler_origin: unproven_by_github_run_metadata` because GitHub metadata
   cannot prove whether Cloudflare or a human initiated the dispatch.

   To quantify the existing GitHub schedule's lateness, run **Measure forward
   scheduler lateness** from the Actions tab. This read-only workflow requests
   recent `schedule` runs for `forward-production.yml`, compares each
   `created_at` with the expected `17 7 * * 2-6` UTC occurrence, and reports
   queue delay through `run_started_at`. It retains only bounded timestamps,
   run IDs, commit hashes, integer delay summaries, and stable validation codes
   in a hashed redacted artifact. The result is an observation of GitHub's
   recorded timing, not a start-time SLO or proof that Cloudflare initiated a
   run. A `blocked` result means the metadata was incomplete or no run fell in
   the requested lookback window.

4. Only after that observation, open a separate PR that removes the
   `schedule:` trigger from `.github/workflows/forward-production.yml` while
   retaining `workflow_dispatch`. Keep the PR link and the first dispatch run as
   the cutover evidence.

The current branch deliberately does not remove the GitHub schedule, so simply
merging this adapter cannot stop production cycles.

CI also validates the forward workflow's rollback boundary: the `07:17 UTC`
schedule, manual dispatch, CPU-only input, and concurrency group must remain
present together. A deliberate Cloudflare cutover must change that contract in
the same reviewed PR as the schedule removal; an incidental workflow edit is
rejected before merge.

## Failure and rollback behavior

- Missing or malformed configuration fails before any GitHub request.
- A GitHub lookup or dispatch error fails the scheduled invocation without
  logging the token or response body; no forecast is fabricated.
- An active workflow run causes a visible `skipped/active_run` result.
- The public HTTP surface returns `404`; there is no unauthenticated trigger.
- Roll back by disabling the Worker Cron trigger or reverting the separate
  cutover PR. Restoring the GitHub schedule is a repository change and should be
  reviewed like any other privileged workflow change.

The deployment workflow is deliberately manual and does not remove or disable
the GitHub schedule. A successful deployment is not a cutover or evidence that
the Worker has dispatched a cycle. The observation workflow is likewise
read-only evidence, not origin proof; retain its report and the application
margin before opening a cutover PR.

## Verification

Run the isolated Worker contract tests from the repository root:

```bash
node --test ops/scheduler/cloudflare-forward-scheduler.test.mjs
```

These tests cover dispatch payloads, active-run suppression, configuration
validation, token non-disclosure, the absent public trigger, and fail-closed
GitHub errors. They do not claim that a Cloudflare account, GitHub token, or
production cutover has been configured. The separate observation workflow
verifies one selected GitHub run and explicitly leaves scheduler provenance
unproven.

## Credential readiness checked 2026-11-04

The existing GET-only verifier ran on released main:
[run 41771649739](https://github.com/hoangnguyen2003/edgar-moe/actions/runs/41771649739).
The stored credential was refused with HTTP 401 after one request, with zero
external writes. The receipt passed redaction and its SHA-256 matched its
artifact manifest:
`adcd0049eaf2461b0d64a89047491d7e6e95b757931d9bc424835d98d51f9adf`.
This establishes an authentication failure, not the current Worker state or the
precise cause of credential invalidity. No deployment or invocation was attempted.

Replace `CLOUDFLARE_API_TOKEN` in the existing GitHub `scheduler` environment with
a current credential using the reviewed per-Worker Individual Workers Editor
scope for `edgar-moe-forward-scheduler`. Keep the account identity unchanged and
keep credential values out of issue comments, logs and Git. Re-run the dormant
verifier once after replacement. A matching account/Worker and its actual
configuration must be verified before deployment.

Credential renewal alone does not complete #156. Worker dispatch-secret
readiness, the reviewed provider-execution path, real scheduler-origin/forecast
margin evidence, and cutover remain outstanding. The existing nominal
`07:17 UTC` schedule and source cutoff have not changed; actual recorded margin
remains the acceptance evidence.

### Replacement credential verified 2026-11-04

After the owner replaced the stored token with the specified-Worker Editor token,
[run 41802437633](https://github.com/hoangnguyen2003/edgar-moe/actions/runs/41802437633)
passed both fixed GET requests on released main. The exact account and Worker
matched; URLs/logging were disabled and zero versions existed. The redacted
receipt matched its manifest hash:
`c944c3c15d2fb1424344c64b415343736109c4c0ef0ce5f5f71c49fe22d88e2b`.
The prior HTTP 401 receipt remains retained as historical evidence. This fresh
check establishes read access and the empty Worker boundary, without asserting
write permission, token expiry, dispatch readiness or the #156 timing acceptance.

### First preparation attempt and filename diagnosis

[Run 41805845042](https://github.com/hoangnguyen2003/edgar-moe/actions/runs/41805845042)
passed the exact dormant-Worker check but failed the quiet Wrangler upload step.
The independent read-only [check 41806186559](https://github.com/hoangnguyen2003/edgar-moe/actions/runs/41806186559)
verified matching Worker/private URLs and absent Cron, then received HTTP 404
from the legacy secret-list endpoint. Both receipts remain retained; this was
not preparation completion.

A local credential-free Wrangler 4.136.2 dry run reproduced the configuration
loading failure with `.toml.example`: the CLI fell back to the repository name
and could not find an entry point. The exact same bytes copied to `wrangler.toml`
bundled successfully. The workflows now materialize the supported filename,
require that step before upload, and dry-run the real CLI command. Verification
also requires an uploaded version before querying script state. This diagnosis
does not justify broadening the per-Worker token scope or enabling Cron.

### Prepared code and dispatch-secret presence verified

The corrected [preparation run 41808455503](https://github.com/hoangnguyen2003/edgar-moe/actions/runs/41808455503)
on merged commit `f52cca6477d64ba276dd8cc888fc0bb2e7151852` succeeded. Its post-check
verified an uploaded version, private URLs/logging and an empty Cron schedule.
Receipt SHA-256: `8da2eb6a2748729d91fea4757948bc7696f53dd2a99ecc7ee8c76e170c5afd53`.

After the owner added the Worker `GITHUB_TOKEN` secret, the read-only
[verification run 41810126338](https://github.com/hoangnguyen2003/edgar-moe/actions/runs/41810126338)
confirmed its presence while retaining the private/no-Cron state. Receipt
SHA-256: `6c1e3545bfbbc8dd65695576bd0310724332ee28d9f66a61803d8193a54a6797`.
Neither check invokes the Worker or proves GitHub credential validity, dispatch
origin or application forecast margin. The original issue remains open.

Before connecting scheduled execution to existing data, the main-only manual
**Verify private scheduler runtime target metadata** workflow compares configured
target hashes to the retained private #288 reference. It passes credentials only
to one standard-library comparison process and makes no network/database/storage
connections. Missing, malformed and different targets fail closed, retaining
only hashes and presence/match flags. A match is not reuse of that reference's
one-cycle authorization and cannot release the general provider-use hold.

### Finite private #156 observation (November 2026)

The new `ops/issue156/private-scheduler-scope.json` records the owner's reported
permission and personal-project instruction for three private frozen-v1 slots:
November 5, 6 and 7 at the original **07:17 UTC** (14:17 Vietnam time). This is
not independently inspected provider correspondence or reuse of #288's one-cycle
scope. The general production workflow/source/publication holds remain intact.

`forward-private-scheduler.yml` accepts a fresh HMAC over Cloudflare's nominal
`controller.scheduledTime`, the original cron, workflow, ref and CPU device.
The Worker has no HTTP trigger. It returns before contacting GitHub outside the
three dates. GitHub rejects stale/future signatures and reruns, and an atomic R2
`If-None-Match: *` claim prevents the same slot from executing twice. See the
[scheduled-event contract](https://developers.cloudflare.com/workers/runtime-apis/handlers/scheduled/)
and [R2 conditional-write contract](https://developers.cloudflare.com/r2/api/s3/api/).

The dedicated operator checks the reviewed input hashes and private target hashes
before service access, refreshes the same 500-name source CSV/730-day window/four
macro series/IEX/SEC forms, and preserves the New York cutoff and frozen v1.
Existing verified filing bodies and content-keyed embeddings seed the refresh;
new filings are still fetched. No training or locked-test rescoring occurs.
Provider payloads, outputs and logs stay in the existing private R2 store. Only
operational timing/identity/hash receipts enter Actions artifacts. A maximum
1 GB seed and three 1 GB evidence archives bound new storage below 4 GB; existing
copies and registry rows are preserved. The raw source and model protocol are
unchanged; the nominal source cutoff has not been moved earlier.

The `Manage finite private scheduler observation` workflow is main-only and uses
the existing `scheduler` environment and existing Worker. `PREPARE` installs the
separate signing key and signed code with an explicit empty Cron list; `ACTIVATE`
deploys the same reviewed code with the original Cron. `VERIFY_ACTIVE` reads
metadata only. `STOP` disables Cron while retaining the code and both secrets.
`PREPARE` never overwrites an existing signing key; investigate a partially
completed preparation before repeating it. The old preparation verifier expects
only the original secret and is not a verifier for this two-secret profile.

The scheduler-side date guard becomes inert after November 7 even if the Cron
configuration remains present. Run `STOP` after collecting the three receipts.
Deployment, a valid signature, a successful empty cycle or passing synthetic
tests cannot establish #156's acceptance. Actual nonzero scheduled forecasts and
recorded `pre_open_schedule_margin >= 5400` are required; inspect all three
private packets before deciding whether timing is reliable enough to close it.
If no eligible filings occur, retain the inconclusive receipts and prepare a new
finite observation scope instead of inventing forecasts or silently extending
this one.
