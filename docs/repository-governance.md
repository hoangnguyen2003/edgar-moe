# Repository governance

The intended lightweight workflow is **branch → pull request → CI →
self-review → GitHub merge → branch deletion**. Local merges are not the normal
path. Controls stay proportionate to a single-maintainer repository.

This document records the settings and expectations that must be enabled in
GitHub; repository administrative settings are external state and are not
represented by files alone.

## Optional `main` branch protection

If the project later gains collaborators or a stronger audit requirement, the
repository’s Settings → Branches → Branch protection rules (or Rulesets) can
protect `main` with required pull requests, reviews, conversation resolution,
up-to-date branches, and the applicable CI checks. Branch protection costs
nothing for a public repository; it remains an optional future control, not a
prerequisite for the current single-maintainer workflow.

## Public repository settings

The repository has been public since 2026-09-22, so standard GitHub-hosted
Actions runners no longer draw on the account's private-repository minutes
(#152). These settings are enabled in GitHub:

- Private vulnerability reporting, the channel `SECURITY.md` points reporters to.
- Secret scanning with push protection.
- Approval before workflows run for pull requests from any outside
  contributor. Fork pull requests never receive repository secrets, and the
  credentialed workflows run only on `schedule` or `workflow_dispatch`.

Everything in the repository is public: source history, commit metadata,
issues, pull requests, and Actions logs and artifacts. Evidence artifacts are
designed to be redacted (provider evidence uploads are gated on a redaction
scan); never put credentials, connection URLs, or raw provider payloads in any
of them.

## Pull requests

`.github/pull_request_template.md` requires a summary, scope, validation, risk,
rollback, architecture impact, and research-integrity notes.
`.github/workflows/pr-policy.yml` rejects branches without a descriptive
approved prefix. The approved prefixes are `feat/`, `fix/`, `chore/`, `docs/`,
`research/`, `refactor/`, `test/`, `security/`, and `ops/`. Dependabot is
allowed to use its standard branch prefix.

After a successful merge, delete the head branch and start new work from the
updated `main`. Keep the PR description and CI results as the durable review
record.

## Security and release boundaries

- Secrets belong only in GitHub/Vercel/Neon/R2 secret stores and must not appear
  in PR text, logs, artifacts, or browser bundles.
- Workflow changes are privileged changes because they can access source-system,
  database, and object-store credentials. Require review for `.github/`, `ops/`,
  `config/`, and deployment files.
- A green PR proves repository checks passed; it does not prove provider quotas,
  database grants, backups, scheduler delivery, or R2 retention. Those require
  separate operational verification.
- CI installs Python dependencies with `uv sync --locked` and web dependencies
  with `npm ci`; dependency changes must include deliberate lockfile updates in
  the reviewed pull request.
- Keep the Vercel/public bundle and private forward runner as separate
  deployment boundaries. The browser must never receive database or R2
  credentials.

## Deployment smoke correlation

The credential-free deployment smoke workflow generates a fresh correlation ID
for every probe. API checks send it as `X-Request-ID` and require the exact
value to be echoed by FastAPI before the check can pass; static CDN checks do
not require the header. Only the generated ID is retained in the redacted
artifact, so an operator can correlate an API probe with serving logs without
capturing response bodies, credentials, or arbitrary response headers. This is
an incident-navigation aid, not a rate limit, authentication control, or proof
that provider-side operations are healthy.

## Served-identity verification

The same probe runs with `--expect-lock config/public_snapshot.lock.json`. For a
Production `deployment_status` event the workflow checks out the deployed
commit, so the served frozen identity (path, data mode, as-of date, snapshot
SHA-256, selection hash, and locked-test hash) must equal the lock reviewed in
that commit, in both `/api/v1/governance` and `data-provenance.json`. A
well-formed but unreviewed snapshot therefore fails the gate, not just a
malformed one. The report records only the mismatched field names. Anyone can
repeat the check from a clone without credentials:

```bash
uv run python scripts/smoke_deployment.py https://edgar-moe.vercel.app \
  --expect-lock config/public_snapshot.lock.json
```

## Current personal-project mode

Branch protection is not enabled because the repository has a single
maintainer. The practical controls are an open PR, green CI, the branch-policy
check, a short self-review using the template, and a deliberate commitment not
to push directly to `main`. If collaborators or regulated evidence are
introduced later, enable a ruleset for `main`.
