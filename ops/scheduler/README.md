# Optional Cloudflare forward scheduler

`cloudflare-forward-scheduler.mjs` is a small, provider-neutral scheduling
adapter for the existing `forward-production.yml` workflow. It is intentionally
not active by default: the repository keeps the GitHub Actions schedule until a
maintainer deploys this Worker, observes a successful dispatch, and approves a
separate cutover PR.

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
4. Only after that observation, open a separate PR that removes the
   `schedule:` trigger from `.github/workflows/forward-production.yml` while
   retaining `workflow_dispatch`. Keep the PR link and the first dispatch run as
   the cutover evidence.

The current branch deliberately does not remove the GitHub schedule, so simply
merging this adapter cannot stop production cycles.

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
the Worker has dispatched a cycle; retain the first observed dispatch before
opening a cutover PR.

## Verification

Run the isolated Worker contract tests from the repository root:

```bash
node --test ops/scheduler/cloudflare-forward-scheduler.test.mjs
```

These tests cover dispatch payloads, active-run suppression, configuration
validation, token non-disclosure, the absent public trigger, and fail-closed
GitHub errors. They do not claim that a Cloudflare account, GitHub token, or
production cutover has been configured.
